"""Control-path faults with actual children; approval installation tested separately."""

import asyncio
import sys
from dataclasses import replace
from importlib import metadata
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.web.test_live_plugins import running
from tests.web.test_storage_migration import migrate
from tests.web.test_storage_replicas import attach, promoted
from whab.app import create_app
from whab.budgets import storage_upgrade
from whab.budgets.runtime import BudgetRuntime
from whab.budgets.session import BudgetSession
from whab.plugins import PluginError, storage_worker

from .test_storage_upgrade import configuration


@pytest.mark.parametrize("source,destination", [("sqlite", "json"), ("json", "sqlite")])
def test_lost_source_fence_reply_recovers_only_under_original_authority_coordinator(source, destination, tmp_path, monkeypatch):
    async def scenario():
        prepared = prepared_fixture(tmp_path, source)
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        app = create_app(settings=configuration(tmp_path, source))
        async with running(app) as client:
            await app.state.storage_upgrade.upgrade(source, ())
            assert (await client.post("/api/accounts", json={"workspace": "personal", "name": "Acknowledged", "type": "depository", "balance": 41})).status_code == 200
            proxy = app.state.tracked_store.store
            worker, call = proxy.worker, proxy.worker.call
            before = proxy.commit_evidence()

            def lose_role(method, *args, **kwargs):
                result = call(method, *args, **kwargs)
                if method == "set_role" and args[2] == "fenced":
                    worker._unknown = True
                    raise storage_worker.storage_error(source, "storage_write_unknown")
                return result

            monkeypatch.setattr(worker, "call", lose_role)
            status = await migrate(client, app, destination)
            runtime = app.state.budget_runtime
            assert status["operations"][-1]["state"] == "recovery_required"
            assert runtime.status()["admission"] == "gated" and runtime._service_fault is None
            assert app.state.storage_runtime.coordinator.journal.read()["stage"] == "validated"
            with pytest.raises(PluginError):
                await app.state.storage_upgrade.recover("active")
            response = await client.post("/api/storage/recover", json={})
            assert response.status_code == 200, response.text
            assert runtime.status()["health"] == "ready"
            assert app.state.storage_plugin == source
            assert proxy.commit_evidence() == before
            assert proxy.worker is not worker and not worker.healthy
            assert (await client.post("/api/accounts", json={"workspace": "personal", "name": "After recovery", "type": "depository", "balance": 59})).status_code == 200
            assert sorted(row["balance"] for row in proxy.accounts("personal")) == [41, 59]

    asyncio.run(scenario())


@pytest.mark.parametrize("destination,source", [("sqlite", "json"), ("json", "sqlite")])
def test_migration_preview_worker_destination_uuid_prevents_missing_file_recreation(destination, source, tmp_path, monkeypatch):
    async def scenario():
        prepared = prepared_fixture(tmp_path, destination)
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        monkeypatch.setattr(storage_upgrade, "_prepared", lambda *_args: prepared)
        app = create_app(settings=configuration(tmp_path, destination))
        async with running(app) as client:
            await app.state.storage_upgrade.upgrade(destination, ())
            status = await migrate(client, app, source)
            assert status["operations"][-1]["state"] == "complete", status
            preview = await client.post("/api/storage/preview", json={"destination": destination})
            assert preview.status_code == 200, preview.text
            identifier = preview.json()["preview_id"]
            context = app.state.storage_runtime.previews[identifier]["context"]
            assert context.expected_instance_id is not None
            path = context.home / "data" / ("whab-sandbox." + ("sqlite" if destination == "sqlite" else "json"))
            path.unlink()
            before = {entry.relative_to(context.home): entry.read_bytes() for entry in context.home.rglob("*") if entry.is_file()}
            started = await client.post("/api/storage/transition", json={"preview_id": identifier})
            assert started.status_code == 202
            await asyncio.gather(*tuple(app.state.storage_runtime.tasks), return_exceptions=True)
            assert app.state.storage_runtime.operations[identifier]["state"] == "failed"
            assert not path.exists()
            assert {entry.relative_to(context.home): entry.read_bytes() for entry in context.home.rglob("*") if entry.is_file()} == before
            assert app.state.storage_plugin == source
            assert app.state.budget_runtime.status()["health"] == "ready"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_live_descriptor_factory_refuses_retired_code_or_closed_owner_before_native_open(kind, tmp_path, monkeypatch):
    from whab.plugins import StorageContext

    async def scenario():
        first, second = prepared_fixture(tmp_path, kind, "a"), prepared_fixture(tmp_path, kind, "b")
        selected = [first]
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: selected[0])
        app = create_app(settings=configuration(tmp_path, kind))
        try:
            await app.state.storage_upgrade.upgrade(kind, ())
            first_factory = app.state.budget_runtime._active.services["plugins"].registry.storage_plugin(kind).storage
            selected[0] = second
            await app.state.storage_upgrade.upgrade(kind, ())
            destination = tmp_path / "must-remain-absent"
            with pytest.raises(PluginError):
                first_factory(StorageContext(destination, "sandbox", {}, initial_role="copy"))
            assert not destination.exists()
            current_factory = app.state.budget_runtime._active.services["plugins"].registry.storage_plugin(kind).storage
            with pytest.raises(PluginError) as retained:
                app.state.storage_upgrade.release()
            assert retained.value.code == "storage_cleanup_pending"
            await app.state.storage_upgrade.close()
            with pytest.raises(PluginError):
                current_factory(StorageContext(destination, "sandbox", {}, initial_role="copy"))
            assert not destination.exists()
        finally:
            await finish(app)

    asyncio.run(scenario())


@pytest.mark.parametrize("original,destination", [("sqlite", "json"), ("json", "sqlite")])
def test_combined_authority_and_read_fault_explicitly_requires_restart_preserving_destination_ack(original, destination, tmp_path, monkeypatch):
    prepared = {kind: prepared_fixture(tmp_path, kind, marker) for kind, marker in ((original, "a"), (destination, "b"))}
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda _root, kind, *_args, **_kwargs: prepared[kind])
    monkeypatch.setattr(storage_upgrade, "_prepared", lambda _home, kind, _reference: prepared[kind])
    settings = configuration(tmp_path, original)
    app = create_app(settings=settings)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Original acknowledged", "type": "depository", "balance": 31}).status_code == 200
        client.portal.call(app.state.storage_upgrade.upgrade, original, ())
        # TestClient is synchronous; use the owned transition helper directly.
        def execute(target):
            preview = client.post("/api/storage/preview", json={"destination": target})
            assert preview.status_code == 200, preview.text
            response = client.post("/api/storage/transition", json={"preview_id": preview.json()["preview_id"]})
            assert response.status_code == 202, response.text
            async def settle():
                await asyncio.gather(*tuple(app.state.storage_runtime.tasks), return_exceptions=True)
            client.portal.call(settle)
        execute(destination)
        assert app.state.storage_plugin == destination
        client.portal.call(app.state.storage_upgrade.upgrade, destination, ())
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Destination acknowledged", "type": "depository", "balance": 47}).status_code == 200
        journal = app.state.storage_runtime.locations.journal
        write = journal.write
        worker = app.state.tracked_store.store.worker

        def fail_read_after_import(record):
            write(record)
            if record["stage"] == "imported":
                worker._process.kill()
                worker._process.wait(timeout=5)

        monkeypatch.setattr(journal, "write", fail_read_after_import)
        execute(original)
        retained = journal.path.read_bytes()
        record = journal.read()
        assert record["stage"] == "imported" and record["authority_instance_id"] == record["source"]["instance_id"]
        status = client.get("/api/storage/status").json()
        assert status["recovery"] == {"mode": "restart_required", "code": "combined_storage_recovery"}
        assert status["runtime"]["admission"] == "gated"
        recovery = client.post("/api/storage/recover", json={})
        assert recovery.status_code == 400 and "Restart" in recovery.text
        assert journal.path.read_bytes() == retained
    reopened = create_app(settings=settings)
    with TestClient(reopened, base_url="https://127.0.0.1:5173") as client:
        assert reopened.state.storage_plugin == destination
        assert reopened.state.storage_runtime.locations.journal.read()["stage"] == "aborted"
        assert sorted(row["balance"] for row in reopened.state.tracked_store.accounts("personal")) == [31, 47]
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Reopened acknowledged", "type": "depository", "balance": 59}).status_code == 200
    final = create_app(settings=settings)
    with TestClient(final, base_url="https://127.0.0.1:5173"):
        assert sorted(row["balance"] for row in final.state.tracked_store.accounts("personal")) == [31, 47, 59]


def prepared_fixture(home, kind, marker="a"):
    return SimpleNamespace(identifier=marker * 64, plugin_id=kind, distribution=f"whab_plugin_{kind}",
                           version=metadata.version(f"whab_plugin_{kind}"), python=sys.executable, path=home / marker)


async def finish(app):
    await app.state.live_plugins.close()
    app.state.tracked_store.close()


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_latest_held_request_and_background_write_drain_before_code_handover(kind, tmp_path, monkeypatch):
    async def scenario():
        app = create_app(settings=configuration(tmp_path, kind))
        prepared = prepared_fixture(tmp_path, kind)
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        runtime, tracked = app.state.budget_runtime, app.state.tracked_store
        other = BudgetRuntime("other-fictional-budget", {"value": "available"})
        release_request, release_background = asyncio.Event(), asyncio.Event()
        request_entered, background_entered = asyncio.Event(), asyncio.Event()
        source = tracked.store

        async def request():
            async with runtime.request():
                request_entered.set()
                await release_request.wait()
                await asyncio.to_thread(tracked.add_account, "personal", {"name": "Late request", "type": "depository", "balance": 12})

        async def background():
            async with runtime.background():
                background_entered.set()
                await release_background.wait()
                await asyncio.to_thread(tracked.add_account, "personal", {"name": "Late sync", "type": "depository", "balance": 34})

        try:
            request_task, background_task = asyncio.create_task(request()), asyncio.create_task(background())
            await request_entered.wait()
            await background_entered.wait()
            transition = asyncio.create_task(app.state.live_plugins.upgrade(kind, ()))
            await runtime.wait_for_phase("draining")
            assert tracked.store is source and runtime.status()["active_leases"] == 2
            async with other.request() as lease:
                assert lease.service("value") == "available"
            release_request.set()
            await request_task
            assert not transition.done() and tracked.store is source
            release_background.set()
            await background_task
            assert await transition == 2
            assert sorted(row["balance"] for row in tracked.accounts("personal")) == [12, 34]
            assert tracked.commit_evidence().generation == 2
            assert app.state.storage_upgrade.binding.worker.healthy
        finally:
            release_request.set()
            release_background.set()
            await finish(app)
            await other.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary,authority", [("before_replace", "active"), ("after_directory_fsync", "candidate")])
def test_lost_code_decision_reply_resolves_exact_durable_choice(tmp_path, monkeypatch, boundary, authority):
    async def scenario():
        app = create_app(settings=configuration(tmp_path, "json"))
        prepared = prepared_fixture(tmp_path, "json")
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        manager = app.state.storage_upgrade
        original = app.state.tracked_store.commit_evidence()
        fired = False

        def fault(stage):
            nonlocal fired
            if not fired and stage == boundary:
                fired = True
                raise OSError("fictional lost journal publication reply")

        manager.journal.fault = fault
        try:
            with pytest.raises(PluginError) as failure:
                await manager.upgrade("json", ())
            assert failure.value.code == "commit_uncertain"
            assert app.state.budget_runtime.status()["health"] == "recovery_required"
            if authority == "candidate":
                with pytest.raises(PluginError):
                    await manager.recover("active")
            await manager.recover(authority)
            assert app.state.tracked_store.commit_evidence() == original
            assert app.state.budget_runtime.status()["health"] == "ready"
            assert (manager.binding is not None) == (authority == "candidate")
        finally:
            await finish(app)

    asyncio.run(scenario())


def test_candidate_writer_open_failure_keeps_committed_code_choice_gated_until_recovery(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(settings=configuration(tmp_path, "json"))
        prepared = prepared_fixture(tmp_path, "json")
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        create_worker = storage_upgrade._worker
        failed = False

        def create(candidate, **kwargs):
            worker = create_worker(candidate, **kwargs)
            open_backend = worker.open

            def open_once(context):
                nonlocal failed
                if not context.read_only and not failed:
                    failed = True
                    raise storage_worker.storage_error("json")
                return open_backend(context)

            worker.open = open_once
            return worker

        monkeypatch.setattr(storage_upgrade, "_worker", create)
        try:
            with pytest.raises(PluginError):
                await app.state.storage_upgrade.upgrade("json", ())
            assert app.state.budget_runtime.status()["admission"] == "gated"
            assert app.state.storage_upgrade.journal.snapshot()["active"]["identifier"] == prepared.identifier
            await app.state.storage_upgrade.recover("candidate")
            assert app.state.storage_upgrade.binding.worker.healthy
            assert app.state.tracked_store.accounts("personal") == []
        finally:
            await finish(app)

    asyncio.run(scenario())


def test_simultaneous_code_transitions_serialize_and_keep_optional_worker_ownership(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(settings=configuration(tmp_path, "sqlite"))
        prepared = prepared_fixture(tmp_path, "sqlite")
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        try:
            results = await asyncio.gather(app.state.storage_upgrade.upgrade("sqlite", ()), app.state.storage_upgrade.upgrade("sqlite", ()))
            assert sorted(results) == [2, 3]
            assert app.state.storage_upgrade.binding.worker.healthy
            # A normal optional selection must retain the required storage
            # binding/resource rather than close its financial worker.
            assert await app.state.live_plugins.activate(()) == 4
            assert app.state.storage_upgrade.binding.worker.healthy
            assert "sqlite" in app.state.budget_runtime._active.services["plugins"].workers
        finally:
            await finish(app)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_restart_with_pending_native_intent_stays_gated_until_explicit_receipt_recovery(kind, tmp_path, monkeypatch):
    prepared = prepared_fixture(tmp_path, kind)
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
    # Approval/reopen validation has its independent installed-wheel suite. This
    # test isolates the app's retained identity and gated startup decision.
    monkeypatch.setattr(storage_upgrade, "_prepared", lambda *_args: prepared)
    settings = configuration(tmp_path, kind)
    app = create_app(settings=settings)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        client.portal.call(app.state.storage_upgrade.upgrade, kind, ())
        worker = app.state.tracked_store.store.worker
        call = worker.call

        def lose(method, *args, **kwargs):
            result = call(method, *args, **kwargs)
            if method == "execute_operation":
                raise storage_worker.storage_error(kind, "storage_write_unknown")
            return result

        worker.call = lose
        lost = client.post("/api/accounts", json={"workspace": "personal", "name": "Committed before restart", "type": "depository", "balance": 99})
        assert lost.status_code == 400
        assert app.state.budget_runtime.status()["health"] == "recovery_required"
    reopened = create_app(settings=settings)
    with TestClient(reopened, base_url="https://127.0.0.1:5173") as client:
        assert reopened.state.budget_runtime.status()["health"] == "recovery_required"
        assert client.get("/api/storage/status").json()["code"]["uncertain"] is True
        recovered = client.post("/api/plugins/management/recover", json={"authority": "active"})
        assert recovered.status_code == 200, recovered.json()
        assert reopened.state.budget_runtime.status()["health"] == "ready"
        assert [row["balance"] for row in reopened.state.ledger_service.accounts("personal")] == [99]


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_saved_worker_never_recreates_missing_or_accepts_replaced_native_instance(kind, tmp_path, monkeypatch):
    prepared = prepared_fixture(tmp_path, kind)
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(storage_upgrade, "_prepared", lambda *_args: prepared)
    settings = configuration(tmp_path, kind)
    app = create_app(settings=settings)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        client.portal.call(app.state.storage_upgrade.upgrade, kind, ())
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Original", "type": "depository", "balance": 123}).status_code == 200
    path = tmp_path / "data" / f"whab-sandbox.{kind}"
    original = path.with_name(path.name + ".preserved")
    path.rename(original)
    raw = original.read_bytes()
    with pytest.raises(PluginError):
        create_app(settings=settings)
    assert not path.exists() and original.read_bytes() == raw
    from whab.plugins import StorageContext, load_plugins
    from whab.plugins.storage import open_storage
    replacement = open_storage(load_plugins((kind,)).storage_plugin(kind), StorageContext(tmp_path, "sandbox", {}))
    replaced_identity = replacement.commit_evidence().instance_id
    replacement.close()
    replaced_raw = path.read_bytes()
    with pytest.raises(PluginError):
        create_app(settings=settings)
    descriptor = storage_upgrade.storage_worker_descriptor(tmp_path, "default", "sandbox", load_plugins((kind,)).storage_plugin(kind))
    with pytest.raises(PluginError):
        descriptor.storage(StorageContext(tmp_path, "sandbox", {}, expected_instance_id=replaced_identity))
    assert path.read_bytes() == replaced_raw and original.read_bytes() == raw


@pytest.mark.parametrize("primary,copy", [("sqlite", "json"), ("json", "sqlite")])
def test_storage_workers_preserve_copies_protected_state_and_original_container_across_promotion_and_reverse_migration(primary, copy, tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    prepared = {name: prepared_fixture(tmp_path, name, marker) for name, marker in ((primary, "a"), (copy, "b"))}
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda _root, name, *_args, **_kwargs: prepared[name])
    monkeypatch.setattr(storage_upgrade, "_prepared", lambda _home, name, _reference: prepared[name])

    async def scenario():
        key = Fernet.generate_key()
        session = BudgetSession.create(tmp_path, "sandbox", "Fictional worker copy", storage=primary)
        session.manifest["plugins"].append({"id": "unavailable", "enabled": False, "apiVersion": 1,
                                            "settings": {"nested": {"score": 0.8}}, "state": {"score": 0.8}})
        session.files["plugins/unavailable/model.bin"] = b"fictional-opaque-model"
        settings = replace(configuration(tmp_path, primary), encryption_key=key.decode())
        app = create_app(settings=settings, budget_session=session)
        async with running(app) as client:
            assert (await client.post("/api/accounts", json={"workspace": "personal", "name": "First", "type": "depository", "balance": 10})).status_code == 200
            token = Fernet(key).encrypt(b"fictional-access-token").decode()
            app.state.tracked_store.save_item("fictional-item", token, "Fictional provider", provider_id="fictional")
            service = app.state.ledger_service
            await app.state.storage_upgrade.upgrade(primary, ())
            identifier = await attach(client, app, copy)
            assert (await client.post("/api/accounts", json={"workspace": "personal", "name": "Late", "type": "depository", "balance": 20})).status_code == 200
            original_path, original_raw = session.path, session.path.read_bytes()
            response = await client.post(f"/api/storage/copies/{identifier}/promote", json={})
            assert response.status_code == 202
            await promoted(app)
            assert app.state.storage_plugin == copy, app.state.storage_replicas.status()
            await app.state.storage_upgrade.upgrade(copy, ())
            assert app.state.ledger_service is service
            assert (await client.post("/api/accounts", json={"workspace": "personal", "name": "Destination ack", "type": "depository", "balance": 30})).status_code == 200
            await migrate(client, app, primary)
            assert app.state.storage_plugin == primary
            assert app.state.storage_upgrade.binding.worker.healthy
            assert app.state.tracked_store.get_item("fictional-item")["token"] == token
            assert session.files["plugins/unavailable/model.bin"] == b"fictional-opaque-model"
            assert original_path.read_bytes() == original_raw
        reopened = create_app(settings=settings, budget_session=BudgetSession.open(tmp_path, "sandbox", session.path))
        async with running(reopened):
            assert reopened.state.storage_upgrade.binding.worker.healthy
            assert sorted(row["balance"] for row in reopened.state.tracked_store.accounts("personal")) == [10, 20, 30]
            assert reopened.state.tracked_store.get_item("fictional-item")["token"] == token
            assert original_path.read_bytes() == original_raw

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["source_fenced", "committed"])
def test_stopped_new_host_repairs_both_retained_roles_before_interrupted_reverse_migration_resolution(tmp_path, monkeypatch, boundary):
    from whab.plugins import StorageContext

    prepared = {kind: prepared_fixture(tmp_path, kind, marker) for kind, marker in (("sqlite", "a"), ("json", "c"))}
    replacement = {kind: prepared_fixture(tmp_path, kind, marker) for kind, marker in (("sqlite", "b"), ("json", "d"))}
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda _root, kind, *_args, **_kwargs: prepared[kind])
    monkeypatch.setattr(storage_upgrade, "_prepared", lambda _home, kind, _reference: prepared[kind])
    settings = configuration(tmp_path, "sqlite")
    app = create_app(settings=settings)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        def execute(preview_id):
            response = client.post("/api/storage/transition", json={"preview_id": preview_id})
            assert response.status_code == 202, response.json()
            async def settle():
                await asyncio.gather(*tuple(app.state.storage_runtime.tasks), return_exceptions=True)
            client.portal.call(settle)
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Acknowledged", "type": "depository", "balance": 31}).status_code == 200
        client.portal.call(app.state.storage_upgrade.upgrade, "sqlite", ())
        first = client.post("/api/storage/preview", json={"destination": "json"})
        assert first.status_code == 200
        execute(first.json()["preview_id"])
        assert app.state.storage_plugin == "json"
        client.portal.call(app.state.storage_upgrade.upgrade, "json", ())
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Later acknowledged", "type": "depository", "balance": 47}).status_code == 200
        journal = app.state.storage_runtime.locations.journal
        write = journal.write
        def interrupted(record):
            write(record)
            if record["stage"] == boundary:
                raise OSError("fictional stopped host after authority publication")
        monkeypatch.setattr(journal, "write", interrupted)
        preview = client.post("/api/storage/preview", json={"destination": "sqlite"})
        assert preview.status_code == 200, preview.json()
        execute(preview.json()["preview_id"])
        assert app.state.budget_runtime.status()["health"] == "recovery_required"
        locations = app.state.storage_runtime.locations
        authority = journal.read()
        assert authority["stage"] == boundary
    # A genuine different-host artifact test separately covers exact-version
    # negotiation. Here old references are forbidden to isolate authority/role
    # recovery with real native children and no old-factory fallback.
    def approved(_home, kind, reference):
        if reference["identifier"] != replacement[kind].identifier:
            raise PluginError("The previous host environment is unavailable.", code="worker_environment_unavailable", plugin_id=kind)
        return replacement[kind]
    monkeypatch.setattr(storage_upgrade, "_prepared", approved)
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda _root, kind, *_args, **_kwargs: replacement[kind])
    with pytest.raises(PluginError):
        create_app(settings=settings)
    native_paths = [path for entry in locations.data["instances"].values()
                    for path in (locations.home(entry) / "data").rglob("*") if path.is_file()]
    before_bytes = {path: path.read_bytes() for path in native_paths}
    for side in ("source", "destination"):
        kind = authority[f"{side}_plugin_id"]
        instance = authority[side]["instance_id"]
        context = StorageContext(locations.home(locations.data["instances"][instance]), "sandbox", {}, expected_instance_id=instance)
        revision = storage_upgrade.storage_worker_revision(tmp_path, "default", "sandbox", kind)
        storage_upgrade.replace_saved_storage_worker(tmp_path, kind, (), context, expected_revision=revision)
    assert journal.read() == authority
    assert all(path.read_bytes() == raw for path, raw in before_bytes.items())
    reopened = create_app(settings=settings)
    with TestClient(reopened, base_url="https://127.0.0.1:5173") as client:
        expected = "json" if boundary == "source_fenced" else "sqlite"
        assert reopened.state.storage_plugin == expected
        assert reopened.state.budget_runtime.status()["health"] == "ready"
        assert sorted(row["balance"] for row in reopened.state.ledger_service.accounts("personal")) == [31, 47]
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Post-recovery", "type": "depository", "balance": 59}).status_code == 200
    again = create_app(settings=settings)
    with TestClient(again, base_url="https://127.0.0.1:5173"):
        assert sorted(row["balance"] for row in again.state.ledger_service.accounts("personal")) == [31, 47, 59]


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_code_commit_cannot_recreate_native_file_deleted_after_readiness(kind, tmp_path, monkeypatch):
    app = create_app(settings=configuration(tmp_path, kind))
    prepared = prepared_fixture(tmp_path, kind)
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
    path = tmp_path / "data" / ("whab-sandbox." + kind)
    removed = False
    def remove(stage):
        nonlocal removed
        if stage == "before_replace" and not removed:
            removed = True
            path.unlink()
    app.state.storage_upgrade.journal.fault = remove
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        with pytest.raises(PluginError):
            client.portal.call(app.state.storage_upgrade.upgrade, kind, ())
        assert removed and not path.exists()
        assert app.state.budget_runtime.status()["health"] == "recovery_required"


@pytest.mark.parametrize("control", ["migration", "promotion"])
def test_captured_bank_continuation_refuses_authority_change_after_drain_before_any_decision(tmp_path, control):
    from whab.plugins.effect_store import EffectError

    async def scenario():
        app = create_app(settings=configuration(tmp_path, "sqlite"))
        async with running(app) as client:
            runtime = app.state.budget_runtime
            attempts = []

            def busy():
                assert runtime.status()["admission"] == "gated"
                assert runtime.status()["active_leases"] == 0
                attempts.append(True)
                raise EffectError("continuation_busy")

            identifier = await attach(client, app, "json") if control == "promotion" else None
            before = app.state.tracked_store.commit_evidence()
            journal = app.state.storage_runtime.coordinator.journal
            original = journal.read()
            app.state.banks["fictional"] = SimpleNamespace(prepare_close=busy)
            if control == "migration":
                status = await migrate(client, app, "json")
                assert status["operations"][-1]["error"]["code"] == "continuation_busy"
            else:
                response = await client.post(f"/api/storage/copies/{identifier}/promote", json={})
                assert response.status_code == 202
                await promoted(app)
                status = (await client.get("/api/storage/copies")).json()
                assert status["operations"][-1]["state"] == "failed"
            assert attempts == [True]
            assert journal.read() == original
            assert app.state.tracked_store.commit_evidence() == before
            assert runtime.status()["admission"] == "open"
            assert app.state.storage_plugin == "sqlite"
            app.state.banks.pop("fictional")
    asyncio.run(scenario())


def test_code_only_storage_upgrade_keeps_captured_bank_and_native_authority(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(settings=configuration(tmp_path, "json"))
        prepared = prepared_fixture(tmp_path, "json")
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        captured = SimpleNamespace(prepare_close=lambda: pytest.fail("Code-only upgrade must retain the captured continuation"))
        app.state.banks["fictional"] = captured
        try:
            before = app.state.tracked_store.commit_evidence()
            await app.state.storage_upgrade.upgrade("json", ())
            assert app.state.tracked_store.commit_evidence() == before
            assert app.state.budget_runtime._active.services["banks"]["fictional"] is captured
        finally:
            app.state.banks.pop("fictional")
            await finish(app)
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_durable_code_baseline_blocks_recovery_even_when_reopened_proxy_baseline_is_older(kind, tmp_path, monkeypatch):
    from whab.plugins import StorageContext, load_plugins
    from whab.plugins.storage import open_storage
    from whab.plugins.storage_proxy import StorageWorkerProxy

    context = StorageContext(tmp_path, "sandbox", {})
    native = open_storage(load_plugins((kind,)).storage_plugin(kind), context)
    native.close()
    path = tmp_path / "data" / ("whab-sandbox." + ("sqlite" if kind == "sqlite" else "json"))
    pristine = path.read_bytes()

    async def scenario():
        app = create_app(settings=configuration(tmp_path, kind))
        prepared = prepared_fixture(tmp_path, kind)
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        controller = app.state.storage_upgrade
        try:
            app.state.tracked_store.add_account("personal", {"name": "Acknowledged before conversion", "type": "depository", "balance": 77})
            expected = app.state.tracked_store.commit_evidence()
            await controller.upgrade(kind, ())
            target = app.state.tracked_store.store
            target.close()
            acknowledged = path.read_bytes()
            path.write_bytes(pristine)
            target.backend = StorageWorkerProxy(storage_upgrade._worker(prepared, on_create=controller.own_worker),
                controller.context, tmp_path / "plugin-workers/storage-intents", on_create=controller.own_proxy)
            assert target.backend._before.generation < expected.generation
            target.backend._fenced = True
            app.state.budget_runtime.fence_storage(kind)
            journal_before = controller.journal._raw
            intents_before = target.backend.intents.store._raw
            with pytest.raises(PluginError):
                await controller.recover("active")
            assert app.state.budget_runtime.status()["admission"] == "gated"
            assert target.backend.worker_recovery_required()
            assert controller.journal._raw == journal_before
            assert target.backend.intents.store._raw == intents_before
            assert path.read_bytes() == pristine
            path.write_bytes(acknowledged)
            await controller.recover("active")
            assert app.state.budget_runtime.status()["admission"] == "open"
            assert app.state.tracked_store.commit_evidence() == expected
            assert [row["balance"] for row in app.state.tracked_store.accounts("personal")] == [77]
        finally:
            await finish(app)
    asyncio.run(scenario())
