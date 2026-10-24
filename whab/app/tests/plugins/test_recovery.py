"""Startup recovery reads metadata first and opens reviewed factories only on retry."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from whab import __main__ as cli
from whab.config import Settings
from whab.plugins import Plugin, PluginError, PluginRegistry, recovery
from whab.plugins.configuration import read_selection, write_selection
from whab.plugins.ui_selection import DISABLED_GENERATION, UISelectionStore


def settings(home, **changes):
    return replace(Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui=""), **changes)


def entry(name, *, distribution="fictional-plugin", version="1"):
    return SimpleNamespace(name=name, dist=SimpleNamespace(metadata={"Name": distribution}, version=version),
                           load=lambda: pytest.fail("Recovery inspection must not load entry-point targets"))


def status(client):
    response = client.get("/api/recovery")
    assert response.status_code == 200
    return response.json()


def saved_visual(home):
    reference = {"environment": "a" * 64, "distribution": "fictional-ui", "version": "1.0.0"}
    with UISelectionStore(home, "default", "sandbox", "fictional-ui") as selected:
        selected.publish({"id": str(uuid4()), "selection": "fictional-ui", "generation": "b" * 64,
                          "worker": reference}, selected.revision(selected.snapshot()))
        revision = selected.revision(selected.snapshot())
    return home / "visual-ui/visual-selection.json", revision, reference


def test_minimal_visual_disable_preserves_decision_and_retries_real_headless_app(tmp_path, monkeypatch):
    from whab.app import create_app

    path, revision, reference = saved_visual(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(recovery, "installed_entrypoints", lambda: (entry("fictional-ui"), entry("sqlite")))
    factory_calls = []
    def factory(**arguments):
        factory_calls.append(True)
        return create_app(**arguments)
    app = recovery.RecoveryApplication(settings(tmp_path, ui="web"), factory=factory)
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        state = status(client)
        assert not factory_calls and state["visual"]["worker"] == reference
        assert state["visual"]["revision"] == revision and state["visual"]["can_disable"]
        assert {row["id"] for row in state["plugins"] if row["required"]} == {"fictional-ui", "sqlite"}
        assert {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()} == before
        assert client.post("/api/recovery/visual/disable", json={"revision": "0" * 64}).status_code == 409
        assert path.read_bytes() == original
        response = client.post("/api/recovery/visual/disable", json={"revision": state["revision"]})
        assert response.status_code == 200 and not factory_calls
        assert response.json()["visual"]["plugin_id"] is None
        assert response.json()["visual"]["generation"] == DISABLED_GENERATION
        assert not response.json()["visual"]["can_disable"]
        assert (tmp_path / "visual-ui/history" / (revision + ".json")).read_bytes() == original
        with UISelectionStore(tmp_path, "default", "sandbox", None) as selected:
            assert selected.snapshot()["workers"]["fictional-ui"] == reference
        assert client.post("/api/recovery/retry", json={"revision": response.json()["revision"]}).status_code == 200
        assert factory_calls == [True] and client.get("/api/plugins").status_code == 200
        assert client.get("/budget/current-month/usd").status_code == 404


def test_visual_recovery_disable_retains_tls_host_origin_and_strict_request_guards(tmp_path):
    path, _, _ = saved_visual(tmp_path)
    original = path.read_bytes()
    with TestClient(recovery.RecoveryApplication(settings(tmp_path)), base_url="https://127.0.0.1:5173") as client:
        state = status(client)
        body = {"revision": state["revision"]}
        assert client.post("http://127.0.0.1:5173/api/recovery/visual/disable", json=body,
                           headers={"X-Forwarded-Proto": "https"}).status_code == 400
        for headers in ({"Host": "outside.example"}, {"Origin": "https://outside.example"},
                        {"Sec-Fetch-Site": "cross-site"}):
            assert client.post("/api/recovery/visual/disable", json=body, headers=headers).status_code == 403
        assert client.post("/api/recovery/visual/disable", json={**body, "plugin": "other"}).status_code == 400
        assert client.post("/api/recovery/visual/disable", content=b"{}",
                           headers={"Content-Type": "text/plain"}).status_code == 415
    assert path.read_bytes() == original


@pytest.mark.parametrize("invalid", ["busy", "schema", "scope", "missing-lock"])
def test_unavailable_visual_decision_never_chooses_another_ui_or_executes_code(tmp_path, monkeypatch, invalid):
    path, _, _ = saved_visual(tmp_path)
    if invalid == "schema":
        path.write_bytes(path.read_bytes().replace(b'"format":1', b'"format":99', 1))
    elif invalid == "scope":
        path.write_bytes(path.read_bytes().replace(b'"budget_id":"default"', b'"budget_id":"other"'))
    elif invalid == "missing-lock":
        path.with_name(path.name + ".lock").unlink()
    owner = UISelectionStore(tmp_path, "default", "sandbox", None) if invalid == "busy" else None
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    monkeypatch.setattr(recovery, "installed_entrypoints", lambda: (entry("fictional-ui"),))
    try:
        with TestClient(recovery.RecoveryApplication(settings(tmp_path), factory=lambda **_: pytest.fail("No code")),
                        base_url="https://127.0.0.1:5173") as client:
            state = status(client)
            assert state["visual"] is None
            assert any(error["code"] == "visual_decision_unreadable" for error in state["errors"])
            assert client.post("/api/recovery/visual/disable", json={"revision": state["revision"]}).status_code == 400
    finally:
        if owner is not None:
            owner.close()
    after = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    assert after.pop(tmp_path / ".plugin-recovery.lock") == b""
    assert after == before


def test_static_inspection_reads_package_metadata_without_importing_disabled_plugins_or_creating_backends(tmp_path, monkeypatch):
    write_selection(tmp_path, ("broken", "uninstalled"))
    monkeypatch.setattr(recovery, "installed_entrypoints", lambda: (entry("broken"), entry("duplicate"), entry("duplicate")))
    app = recovery.RecoveryApplication(settings(tmp_path), factory=lambda **_: pytest.fail("No startup was requested"))
    before = set(tmp_path.rglob("*"))
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert "Plugin recovery" in client.get("/").text
        projection = status(client)
        catalog = {row["id"]: row for row in projection["plugins"]}
        assert catalog["broken"]["distribution"] == "fictional-plugin"
        assert catalog["duplicate"]["duplicate"] is True
        assert catalog["uninstalled"]["enabled"] and not catalog["uninstalled"]["installed"]
        assert catalog["sqlite"]["required"]
        assert not projection["starting"] and projection["startup_error"] is None
    assert set(tmp_path.rglob("*")) == before


def test_disable_is_compare_and_set_removal_only_and_preserves_unknown_selection(tmp_path, monkeypatch):
    write_selection(tmp_path, ("broken", "uninstalled"))
    monkeypatch.setattr(recovery, "installed_entrypoints", lambda: (entry("broken"),))
    with TestClient(recovery.RecoveryApplication(settings(tmp_path)), base_url="https://127.0.0.1:5173") as client:
        original = status(client)
        response = client.post("/api/recovery/disable", json={"revision": original["revision"], "plugins": ["broken"]})
        assert response.status_code == 200
        assert read_selection(tmp_path) == ("uninstalled",)
        assert client.post("/api/recovery/disable", json={"revision": original["revision"], "plugins": ["uninstalled"]}).status_code == 409
        current = status(client)
        for disabled in (["sqlite"], ["never-enabled"], ["uninstalled", "uninstalled"], "uninstalled"):
            rejected = client.post("/api/recovery/disable", json={"revision": current["revision"], "plugins": disabled})
            assert rejected.status_code == 400
        assert read_selection(tmp_path) == ("uninstalled",)


@pytest.mark.parametrize("raw", [b"not-json", b'{"version":true,"plugins":[]}', b'{"version":1,"plugins":[],"plugins":["broken"]}', b"x" * 20000])
def test_invalid_global_selection_requires_explicit_archive_and_clear(tmp_path, monkeypatch, raw):
    path = tmp_path / "plugins.json"
    path.write_bytes(raw)
    monkeypatch.setattr(recovery, "installed_entrypoints", lambda: ())
    with TestClient(recovery.RecoveryApplication(settings(tmp_path)), base_url="https://127.0.0.1:5173") as client:
        state = status(client)
        assert state["can_clear_selection"]
        disabled = client.post("/api/recovery/disable", json={"revision": state["revision"], "plugins": []})
        assert disabled.status_code == 400 and path.read_bytes() == raw
        response = client.post("/api/recovery/selection/clear-invalid", json={"revision": state["revision"]})
        assert response.status_code == 200
        archive = tmp_path / "plugin-recovery-archive" / response.json()["archive"]
        assert archive.read_bytes() == raw and archive.stat().st_mode & 0o777 == 0o600
        assert read_selection(tmp_path) == ()


def test_archive_failure_does_not_clear_original_invalid_selection(tmp_path, monkeypatch):
    original = b"synthetic-private-invalid-record"
    (tmp_path / "plugins.json").write_bytes(original)
    controller = recovery.RecoveryController(settings(tmp_path))
    current = controller.inspect()[0]
    monkeypatch.setattr(recovery, "_flush", lambda _: (_ for _ in ()).throw(OSError("synthetic-private-path")))
    with pytest.raises(recovery.RecoveryError) as failed:
        controller.clear_invalid(current["revision"])
    assert failed.value.code == "write_failed"
    assert (tmp_path / "plugins.json").read_bytes() == original
    assert next((tmp_path / "plugin-recovery-archive").iterdir()).read_bytes() == original


def test_invalid_selection_changed_after_archival_is_not_overwritten_by_stale_clear(tmp_path, monkeypatch):
    original = b"invalid-fictional-record"
    (tmp_path / "plugins.json").write_bytes(original)
    controller = recovery.RecoveryController(settings(tmp_path))
    current = controller.inspect()[0]
    archive = recovery._archive

    def changed_after_archive(*args):
        result = archive(*args)
        write_selection(tmp_path, ("different-selection",))
        return result

    monkeypatch.setattr(recovery, "_archive", changed_after_archive)
    with pytest.raises(recovery.RecoveryError) as conflict:
        controller.clear_invalid(current["revision"])
    assert conflict.value.code == "revision_conflict"
    assert read_selection(tmp_path) == ("different-selection",)
    assert next((tmp_path / "plugin-recovery-archive").iterdir()).read_bytes() == original


def test_concurrent_recovery_owner_cannot_publish_a_second_selection_decision(tmp_path):
    write_selection(tmp_path, ("fictional",))
    controller = recovery.RecoveryController(settings(tmp_path))
    current = controller.inspect()[0]
    with recovery._decision_lock(tmp_path):
        with pytest.raises(recovery.RecoveryError) as conflict:
            controller.disable(current["revision"], ["fictional"])
    assert conflict.value.code == "revision_conflict"
    assert read_selection(tmp_path) == ("fictional",)


@pytest.mark.parametrize("valid", [False, True])
def test_worker_reset_archives_exact_reference_and_never_opens_or_deletes_environment(tmp_path, monkeypatch, valid):
    monkeypatch.setattr(recovery, "installed_entrypoints", lambda: ())
    directory = tmp_path / "plugin-workers" / "active"
    directory.mkdir(parents=True)
    reference = directory / "report.json"
    raw = json.dumps({"format": 1, "plugin": "report", "environment": "a" * 64, "distribution": "fictional-report", "version": "1"}).encode() if valid else b'{"synthetic-private":"never-display"}'
    reference.write_bytes(raw)
    environment = tmp_path / "plugin-workers" / "environments" / ("a" * 64)
    environment.mkdir(parents=True)
    sentinel = environment / "preserved.bin"
    sentinel.write_bytes(b"reviewed-environment-bytes")
    with TestClient(recovery.RecoveryApplication(settings(tmp_path)), base_url="https://127.0.0.1:5173") as client:
        state = status(client)
        assert state["workers"][0]["status"] == ("recorded" if valid else "invalid")
        assert "synthetic-private" not in json.dumps(state)
        response = client.post("/api/recovery/workers/report/reset", json={"revision": state["revision"]})
        assert response.status_code == 200
        assert not reference.exists()
        assert next((directory.parent / "recovery-archive").iterdir()).read_bytes() == raw
        assert sentinel.read_bytes() == b"reviewed-environment-bytes"


def test_symlinked_worker_reference_is_never_followed_or_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "installed_entrypoints", lambda: ())
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_bytes(b"synthetic-private-outside-record")
    directory = tmp_path / "plugin-workers" / "active"
    directory.mkdir(parents=True)
    (directory / "report.json").symlink_to(outside)
    try:
        controller = recovery.RecoveryController(settings(tmp_path))
        public = controller.inspect()[0]
        assert "synthetic-private" not in json.dumps(public)
        with pytest.raises(recovery.RecoveryError):
            controller.reset_worker(public["revision"], "report")
        assert outside.read_bytes() == b"synthetic-private-outside-record"
    finally:
        outside.unlink()


def test_recovery_uses_existing_https_host_origin_body_and_content_type_guards(tmp_path):
    with TestClient(recovery.RecoveryApplication(settings(tmp_path)), base_url="https://127.0.0.1:5173") as client:
        assert client.get("http://127.0.0.1:5173/api/recovery", headers={"X-Forwarded-Proto": "https"}).status_code == 400
        assert client.get("/api/recovery", headers={"Host": "outside.example"}).status_code == 403
        assert client.post("/api/recovery/retry", json={}, headers={"Origin": "https://outside.example"}).status_code == 403
        assert client.post("/api/recovery/retry", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post("/api/recovery/retry", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
        assert client.post("/api/recovery/retry", content=b"x" * 33000, headers={"Content-Type": "application/json"}).status_code == 413
        assert client.post("/api/recovery/retry", content=b"not-json", headers={"Content-Type": "application/json"}).status_code == 400
        assert client.post("/api/recovery/retry", json={"revision": "0" * 64, "extra": True}).status_code == 400


@pytest.mark.parametrize("failure", ["factory", "lifespan"])
def test_failed_candidate_keeps_minimal_listener_available_and_redacts_exception_payloads(tmp_path, failure):
    write_selection(tmp_path, ("broken",))
    closed = []

    def factory(**_):
        if failure == "factory":
            raise PluginError("synthetic-private-source-payload", code="not-safe", plugin_id="broken")

        @asynccontextmanager
        async def lifespan(_):
            try:
                raise PluginError("synthetic-private-source-payload", code="not-safe", plugin_id="broken")
                yield
            finally:
                closed.append(True)

        return FastAPI(lifespan=lifespan)

    app = recovery.RecoveryApplication(settings(tmp_path), factory=factory)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        current = status(client)
        response = client.post("/api/recovery/retry", json={"revision": current["revision"]})
        assert response.status_code == 400
        assert response.json()["error"]["plugin_id"] == "broken"
        assert "synthetic-private" not in response.text
        assert status(client)["startup_error"]["code"] == "startup_failed"
        assert "Plugin recovery" in client.get("/").text
    assert closed == ([] if failure == "factory" else [True])


def test_retry_runs_real_full_app_and_its_lifespan_before_same_listener_activation(tmp_path):
    from whab.app import create_app

    recorded = []
    thread = get_ident()

    def full_factory(**arguments):
        assert get_ident() != thread
        full = create_app(**arguments)
        recorded.append(full)
        return full

    app = recovery.RecoveryApplication(settings(tmp_path), factory=full_factory)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert app._candidate is None
        current = status(client)
        response = client.post("/api/recovery/retry", json={"revision": current["revision"]})
        assert response.status_code == 200 and response.json() == {"started": True}
        assert app._candidate is recorded[0]
        assert client.get("/api/plugins").status_code == 200
        assert client.get("/api/recovery").status_code == 404
        assert client.get("http://127.0.0.1:5173/api/plugins").status_code == 400
    with pytest.raises(Exception, match="closed"):
        recorded[0].state.store.accounts("personal")


def test_retry_keeps_owned_startup_alive_after_request_cancellation_and_listener_shutdown_cleans_candidate(tmp_path):
    entered, release = Event(), Event()
    closed = []

    def factory(**_):
        entered.set()
        assert release.wait(5), "Fictional startup was not released"

        @asynccontextmanager
        async def lifespan(_):
            try:
                yield
            finally:
                closed.append(True)

        full = FastAPI(lifespan=lifespan)

        @full.get("/ready")
        async def ready():
            return {"ready": True}

        return full

    async def scenario():
        app = recovery.RecoveryApplication(settings(tmp_path), factory=factory)
        async with app.shell.router.lifespan_context(app.shell):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:5173") as client:
                current = (await client.get("/api/recovery")).json()
                request = asyncio.create_task(client.post("/api/recovery/retry", json={"revision": current["revision"]}))
                try:
                    assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 5)
                    assert (await client.get("/api/recovery")).json()["starting"]
                    assert (await client.post("/api/recovery/retry", json={"revision": current["revision"]})).status_code == 409
                    request.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await request
                finally:
                    release.set()
                assert await asyncio.wait_for(app._retry_task, 5) == {"started": True}
                assert (await client.get("/ready")).json() == {"ready": True}
        assert closed == [True]

    asyncio.run(scenario())


def test_actual_bank_startup_failure_closes_native_backend_then_explicit_disable_allows_real_retry(tmp_path):
    from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

    from whab.app import create_app

    write_selection(tmp_path, ("broken",))
    opened, bank_calls = [], []

    def bank(_):
        bank_calls.append(True)
        raise RuntimeError("synthetic-private-bank-payload")

    broken = Plugin("broken", "Fictional failed bank", bank=bank)

    def factory(**arguments):
        selected = (broken, sqlite_plugin) if "broken" in arguments["settings"].plugins else (sqlite_plugin,)
        candidate = create_app(plugins=PluginRegistry(selected), **arguments)
        opened.append(candidate)
        return candidate

    app = recovery.RecoveryApplication(settings(tmp_path, plugins=("broken",)), factory=factory)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        current = status(client)
        assert not opened and not bank_calls
        failed = client.post("/api/recovery/retry", json={"revision": current["revision"]})
        assert failed.status_code == 400 and failed.json()["error"]["plugin_id"] == "broken"
        assert "synthetic-private" not in failed.text
        assert bank_calls == [True]
        with pytest.raises(Exception, match="closed"):
            opened[0].state.store.accounts("personal")
        current = status(client)
        disabled = client.post("/api/recovery/disable", json={"revision": current["revision"], "plugins": ["broken"]})
        assert disabled.status_code == 200
        started = client.post("/api/recovery/retry", json={"revision": disabled.json()["revision"]})
        assert started.status_code == 200
        assert bank_calls == [True] and client.get("/api/plugins").status_code == 200


def test_control_request_queued_before_activation_cannot_edit_selection_after_full_app_becomes_active(tmp_path):
    write_selection(tmp_path, ("fictional",))
    entered, release = Event(), Event()

    def factory(**_):
        entered.set()
        assert release.wait(5), "Fictional startup was not released"
        return FastAPI()

    async def scenario():
        app = recovery.RecoveryApplication(settings(tmp_path), factory=factory)
        async with app.shell.router.lifespan_context(app.shell):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:5173") as client:
                current = (await client.get("/api/recovery")).json()
                retry = asyncio.create_task(client.post("/api/recovery/retry", json={"revision": current["revision"]}))
                assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 5)
                disable = asyncio.create_task(client.post("/api/recovery/disable", json={"revision": current["revision"], "plugins": ["fictional"]}))
                await asyncio.sleep(0)
                release.set()
                assert (await retry).status_code == 200
                rejected = await disable
                assert rejected.status_code == 400 and rejected.json()["error"]["code"] == "already_active"
                assert read_selection(tmp_path) == ("fictional",)

    asyncio.run(scenario())


def test_recovery_cli_bypasses_only_saved_selection_and_retains_exact_listener_tls_guards(tmp_path, monkeypatch):
    (tmp_path / "plugins.json").write_bytes(b"invalid-selection")
    for key in ("WHAB_BUDGET", "WHAB_BUDGET_PASSWORD_ENV", "WHAB_BUDGET_COPIES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WHAB_HOME", str(tmp_path))
    monkeypatch.setenv("WHAB_ENV", "sandbox")
    monkeypatch.setenv("WHAB_STORAGE", "sqlite")
    monkeypatch.setenv("WHAB_UI", "")
    monkeypatch.setenv("WHAB_PLUGINS", "")
    with pytest.raises(PluginError):
        Settings.load()
    assert Settings.load(read_plugin_selection=False).home == tmp_path
    calls, tls = [], []
    monkeypatch.setattr(cli, "validate_tls", lambda *args: tls.append(args))
    monkeypatch.setattr(cli.uvicorn, "run", lambda target, **kwargs: calls.append((target, kwargs)))
    monkeypatch.setattr(sys, "argv", ["whab", "--recover-plugins"])
    cli.main()
    target, options = calls[0]
    assert target == "whab.plugins.recovery:create_app" and tls
    assert options["factory"] and options["host"] == "127.0.0.1"
    assert options["proxy_headers"] is False and options["forwarded_allow_ips"] == ""
    assert options["ssl_certfile"] and options["ssl_keyfile"] and not options["reload"]
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("arguments", [("--create", "Fictional"), ("--copy", "copy.whab"), ("--reload",)])
def test_recovery_cli_refuses_creation_copy_and_development_reload(arguments, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["whab", "--recover-plugins", *arguments])
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: pytest.fail("Invalid flags must not start a listener"))
    with pytest.raises(SystemExit) as rejected:
        cli.main()
    assert rejected.value.code == 2
    assert "separately" in capsys.readouterr().err
