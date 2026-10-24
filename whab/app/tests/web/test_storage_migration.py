"""Live local-storage controls keep services, original containers and authority."""

import asyncio
import subprocess
import sys
from dataclasses import replace

import pytest

from tests.web.test_budget_sessions import settings
from tests.web.test_live_plugins import running
from whab.app import create_app
from whab.budgets.host import BudgetHost
from whab.budgets.session import BudgetSession
from whab.plugins import PluginError


async def settled(app):
    if app.state.storage_runtime.tasks:
        await asyncio.gather(*tuple(app.state.storage_runtime.tasks), return_exceptions=True)


async def migrate(client, app, destination, prefix=""):
    preview = await client.post(f"{prefix}/api/storage/preview", json={"destination": destination})
    assert preview.status_code == 200, preview.text
    started = await client.post(f"{prefix}/api/storage/transition", json={"preview_id": preview.json()["preview_id"]})
    assert started.status_code == 202, started.text
    await settled(app)
    return (await client.get(f"{prefix}/api/storage/status")).json()


@pytest.mark.parametrize(("source", "destination"), [("sqlite", "json"), ("json", "sqlite")])
def test_default_live_migration_preserves_service_identity_cached_calls_revision_retry_and_reopens(tmp_path, source, destination):
    async def scenario():
        configuration = replace(settings(tmp_path), storage=source)
        app = create_app(settings=configuration)
        async with running(app) as client:
            service = app.state.ledger_service
            tracked = app.state.tracked_store
            captured_accounts = tracked.accounts
            expected_revision = service.revision
            reply = service.command("add_account", "personal", {"name": "Fictional", "type": "depository", "balance": 10000},
                                    request_id="stable-original-command", expected_revision=expected_revision)
            revision = service.revision
            status = await migrate(client, app, destination)
            assert status["operations"][-1]["state"] == "complete", status
            assert status["storage"] == destination
            assert app.state.ledger_service is service
            assert app.state.tracked_store is tracked
            assert service.revision == revision
            assert service.command("add_account", "personal", {"name": "Fictional", "type": "depository", "balance": 10000},
                                   request_id="stable-original-command", expected_revision=expected_revision) == reply
            assert captured_accounts("personal")[0]["name"] == "Fictional"
            created = await client.post("/api/accounts", json={"workspace": "personal", "name": "Later", "type": "depository", "balance": 1})
            assert created.status_code == 200, created.text
            assert {row["name"] for row in captured_accounts("personal")} == {"Fictional", "Later"}
            assert (await client.get("/api/budget")).json()["storage"] == destination
            assert app.state.plugin_manager.required == (destination,)
            assert app.state.live_plugins.required == frozenset({destination})
        reopened = create_app(settings=configuration)
        async with running(reopened) as client:
            assert (await client.get("/api/budget")).json()["storage"] == destination
            assert {row["name"] for row in reopened.state.tracked_store.accounts("personal")} == {"Fictional", "Later"}
            reverse = await migrate(client, reopened, source)
            assert reverse["operations"][-1]["state"] == "complete", reverse
            assert len(reopened.state.tracked_store.accounts("personal")) == 2
    asyncio.run(scenario())


def test_named_budget_preserves_original_container_unknown_namespaces_and_reopens_from_original(tmp_path):
    async def scenario():
        session = BudgetSession.create(tmp_path, "sandbox", "Fictional budget", storage="sqlite")
        session.manifest["plugins"].append({"id": "unavailable", "enabled": False, "apiVersion": 1,
                                            "settings": {"choice": "preserve", "minimumScore": 0.8},
                                            "state": {"model": "opaque", "nested": [{"weight": 0.25}]}})
        session.files["plugins/unavailable/model.bin"] = b"fictional-opaque-model\x00bytes"
        session.manifest["plugins"].append({"id": "json", "enabled": False, "apiVersion": 1,
                                            "settings": {}, "state": {}})
        session.files["plugins/json/opaque-addon.bin"] = b"preserved-disabled-destination-bytes"
        app = create_app(settings=settings(tmp_path), budget_session=session)
        async with running(app) as client:
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Before", "type": "depository", "balance": 10})
            assert response.status_code == 200
            original_path = session.path
            original_bytes = original_path.read_bytes()
            before_state = session.plugin_record("unavailable")
            status = await migrate(client, app, "json")
            assert status["operations"][-1]["state"] == "complete", status
            assert session.path != original_path
            assert original_path.read_bytes() == original_bytes
            assert session.plugin_record("unavailable") == before_state
            assert session.files["plugins/unavailable/model.bin"] == b"fictional-opaque-model\x00bytes"
            assert session.files["plugins/json/opaque-addon.bin"] == b"preserved-disabled-destination-bytes"
            after = await client.post("/api/accounts", json={"workspace": "personal", "name": "After", "type": "depository", "balance": 20})
            assert after.status_code == 200
            assert original_path.read_bytes() == original_bytes
        reopened_session = BudgetSession.open(tmp_path, "sandbox", original_path)
        assert reopened_session.manifest["storage"] == "json"
        reopened = create_app(settings=settings(tmp_path), budget_session=reopened_session)
        async with running(reopened) as client:
            assert {row["name"] for row in reopened.state.tracked_store.accounts("personal")} == {"Before", "After"}
            assert reopened_session.plugin_record("unavailable") == before_state
            assert reopened_session.files["plugins/unavailable/model.bin"] == b"fictional-opaque-model\x00bytes"
            assert reopened_session.files["plugins/json/opaque-addon.bin"] == b"preserved-disabled-destination-bytes"
            status = await migrate(client, reopened, "sqlite")
            assert status["operations"][-1]["state"] == "complete", status
            assert reopened_session.files["plugins/json/opaque-addon.bin"] == b"preserved-disabled-destination-bytes"
            assert original_path.read_bytes() == original_bytes
    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["source.json", "manifest.json", "plugin-data.whab"])
def test_missing_host_namespace_recovery_stops_migrated_budget_without_overwriting_native_data(tmp_path, missing):
    async def scenario():
        session = BudgetSession.create(tmp_path, "sandbox", "Fictional budget", storage="sqlite")
        session.manifest["plugins"].append({"id": "unavailable", "enabled": False, "apiVersion": 1,
                                            "settings": {}, "state": {}})
        session.files["plugins/unavailable/model.bin"] = b"fictional-model"
        app = create_app(settings=settings(tmp_path), budget_session=session)
        async with running(app) as client:
            original_path = session.path
            status = await migrate(client, app, "json")
            assert status["operations"][-1]["state"] == "complete", status
        (session.storage_home / missing).unlink()
        before = {path: path.read_bytes() for path in session.storage_home.rglob("*") if path.is_file()}
        with pytest.raises(ValueError, match="local plugin recovery state"):
            BudgetSession.open(tmp_path, "sandbox", original_path)
        assert {path: path.read_bytes() for path in before} == before
    asyncio.run(scenario())


def test_preview_is_required_and_security_guards_remain(tmp_path):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            assert (await client.post("/api/storage/transition", json={"preview_id": "00000000-0000-0000-0000-000000000001"})).status_code == 400
            assert (await client.post("/api/storage/preview", json={"destination": "sqlite"})).status_code == 400
            assert (await client.post("/api/storage/preview", json={"destination": "json", "extra": True})).status_code == 400
            assert (await client.post("/api/storage/preview?workspace=demo", json={"destination": "json"})).status_code == 403
            assert (await client.post("/api/storage/preview", json={"destination": "json"}, headers={"origin": "https://untrusted.invalid"})).status_code == 403
            assert (await client.get("http://127.0.0.1:5173/api/storage/status")).status_code == 400
    asyncio.run(scenario())


def test_missing_runtime_key_for_connections_and_changed_key_binding_fail_before_handover(tmp_path):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            app.state.tracked_store.save_item("fictional", "ciphertext-fixture", "Fictional institution")
            failed = await client.post("/api/storage/preview", json={"destination": "json"})
            assert failed.status_code == 400 and "existing runtime encryption key" in failed.text
            assert app.state.storage_runtime.locations.data is None
        keyed = replace(settings(tmp_path / "keyed"), encryption_key="a" * 64)
        keyed_app = create_app(settings=keyed)
        async with running(keyed_app) as client:
            preview = await client.post("/api/storage/preview", json={"destination": "json"})
            assert preview.status_code == 200
        with pytest.raises(PluginError, match="encryption key"):
            changed = create_app(settings=replace(keyed, encryption_key="b" * 64))
            changed.state.storage_runtime.locations.check_key("b" * 64)
    asyncio.run(scenario())


def test_secondary_budget_background_work_drains_while_default_budget_stays_available(tmp_path):
    async def scenario():
        configuration = settings(tmp_path)
        host = BudgetHost(configuration)
        app = create_app(settings=configuration, budget_host=host)
        async with running(app) as client:
            created = await client.post("/api/budget/new", json={"name": "Fictional secondary"})
            child_id = created.json()["id"]
            child, session = host.applications[child_id]
            entered, release = asyncio.Event(), asyncio.Event()
            async def background():
                async with child.state.budget_runtime.background():
                    entered.set()
                    await release.wait()
                    child.state.tracked_store.add_account("personal", {"name": "Final old write", "type": "depository", "balance": 1})
            old = asyncio.create_task(background())
            await entered.wait()
            prefix = f"/budgets/{child_id}"
            preview = await client.post(f"{prefix}/api/storage/preview", json={"destination": "json"})
            assert preview.status_code == 200, preview.text
            result = await client.post(f"{prefix}/api/storage/transition", json={"preview_id": preview.json()["preview_id"]})
            assert result.status_code == 202
            await child.state.budget_runtime.wait_for_phase("draining")
            status = (await client.get(f"{prefix}/api/storage/status")).json()
            assert status["runtime"]["admission"] == "gated"
            assert (await client.get("/budgets/home/api/workspace?workspace=personal")).status_code == 200
            assert app.state.storage_plugin == "sqlite"
            release.set()
            await old
            await settled(child)
            assert child.state.storage_plugin == "json"
            assert child.state.tracked_store.accounts("personal")[0]["name"] == "Final old write"
            assert (await client.get(f"{prefix}/api/storage/status")).json()["operations"][-1]["state"] == "complete"
    asyncio.run(scenario())


@pytest.mark.parametrize("decision_persisted", [False, True])
def test_uncertain_journal_decision_exposes_progress_and_explicit_recovery_chooses_durable_side(tmp_path, decision_persisted, monkeypatch):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            journal = app.state.storage_runtime.locations.journal
            original_write = journal.write
            fired = False
            def write(record):
                nonlocal fired
                if record["stage"] == "committed" and not fired:
                    fired = True
                    if decision_persisted:
                        original_write(record)
                    raise OSError("synthetic-private-failure-must-not-leak")
                return original_write(record)
            monkeypatch.setattr(journal, "write", write)
            status = await migrate(client, app, "json")
            assert status["runtime"]["health"] == "recovery_required"
            assert status["operations"][-1]["state"] == "recovery_required"
            assert "synthetic-private" not in str(status)
            response = await client.post("/api/storage/recover", json={})
            assert response.status_code == 200, response.text
            assert app.state.storage_plugin == ("json" if decision_persisted else "sqlite")
            assert (await client.get("/api/workspace?workspace=personal")).status_code == 200
            assert app.state.budget_runtime.status()["admission"] == "open"
            if decision_persisted:
                assert journal.read()["stage"] == "retired"
            else:
                assert journal.read()["stage"] == "aborted"
    asyncio.run(scenario())


def test_changed_runtime_key_after_committed_handover_stops_before_opening_authority(tmp_path):
    async def scenario():
        configuration = replace(settings(tmp_path), encryption_key="a" * 64)
        app = create_app(settings=configuration)
        async with running(app) as client:
            status = await migrate(client, app, "json")
            assert status["operations"][-1]["state"] == "complete"
        native_files = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
        with pytest.raises(PluginError, match="encryption key"):
            create_app(settings=replace(configuration, encryption_key="b" * 64))
        assert {path: path.read_bytes() for path in native_files} == native_files
    asyncio.run(scenario())


_CRASH_APPLICATION = r'''
import asyncio, os, signal, sys
from dataclasses import replace
from pathlib import Path
from tests.web.test_budget_sessions import settings
from tests.web.test_live_plugins import running
from whab.app import create_app
from whab.budgets.session import BudgetSession
path, source, destination, boundary, named = Path(sys.argv[1]), *sys.argv[2:]
async def main():
    configuration = replace(settings(path), storage=source, encryption_key='a'*64)
    session = None
    if named == 'yes':
        session = BudgetSession.create(path, 'sandbox', 'Fictional crash budget', storage=source, path=path/'original.whab')
        session.manifest['plugins'].append({'id':'unavailable','enabled':False,'apiVersion':1,'settings':{},'state':{'model':'preserve'}})
        session.files['plugins/unavailable/model.bin'] = b'opaque-fictional-model'
    app = create_app(settings=configuration, budget_session=session)
    async with running(app) as client:
        response = await client.post('/api/accounts',json={'workspace':'personal','name':'Before crash','type':'depository','balance':123})
        assert response.status_code == 200, response.text
        journal = app.state.storage_runtime.locations.journal
        write = journal.write
        def crashing_write(record):
            write(record)
            if record['stage'] == boundary:
                os.kill(os.getpid(), signal.SIGKILL)
        journal.write = crashing_write
        preview = await client.post('/api/storage/preview',json={'destination':destination})
        assert preview.status_code == 200, preview.text
        response = await client.post('/api/storage/transition',json={'preview_id':preview.json()['preview_id']})
        assert response.status_code == 202, response.text
        await asyncio.gather(*tuple(app.state.storage_runtime.tasks))
        raise AssertionError('crash boundary not reached')
asyncio.run(main())
'''


@pytest.mark.parametrize(("source", "destination"), [("sqlite", "json"), ("json", "sqlite")])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("boundary", ["source_fenced", "committed", "active"])
def test_application_process_death_recovers_selected_authority_before_cached_manifest_checks(tmp_path, source, destination, named, boundary):
    result = subprocess.run([sys.executable, "-c", _CRASH_APPLICATION, str(tmp_path), source, destination,
                             boundary, "yes" if named else "no"], capture_output=True, timeout=20, check=False)
    assert result.returncode < 0, result.stderr.decode()
    original = tmp_path / "original.whab"
    original_bytes = original.read_bytes() if named else None
    expected = source if boundary == "source_fenced" else destination
    async def scenario():
        configuration = replace(settings(tmp_path), storage=source, encryption_key="a" * 64)
        session = BudgetSession.open(tmp_path, "sandbox", original) if named else None
        app = create_app(settings=configuration, budget_session=session)
        async with running(app) as client:
            assert app.state.storage_plugin == expected
            assert app.state.tracked_store.accounts("personal")[0]["name"] == "Before crash"
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "After recovery", "type": "depository", "balance": 1})
            assert response.status_code == 200
            if named:
                assert session.plugin_record("unavailable")["state"] == {"model": "preserve"}
                assert session.files["plugins/unavailable/model.bin"] == b"opaque-fictional-model"
                if boundary != "source_fenced":
                    assert original.read_bytes() == original_bytes
        session = BudgetSession.open(tmp_path, "sandbox", original) if named else None
        reopened = create_app(settings=configuration, budget_session=session)
        async with running(reopened):
            assert reopened.state.storage_plugin == expected
            assert {row["name"] for row in reopened.state.tracked_store.accounts("personal")} == {"Before crash", "After recovery"}
    asyncio.run(scenario())
