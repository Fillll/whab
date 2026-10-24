"""Synthetic engines exercise host ownership without financial/provider access."""

import asyncio
import json
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from starlette.concurrency import run_in_threadpool

from whab.budgets.runtime import BudgetRuntime, RuntimeCandidate, RuntimeTransitionError
from whab.domain.errors import StoreError
from whab.plugins import Categorization, Plugin, PluginContract, PluginError
from whab.services.categorization import CategorizationService, local_context, local_files
from whab.services.categorization_runtime import (
    CategorizationLifecycle,
    LeasedCategorization,
    ReadinessNamespace,
    live_categorizer,
)
from whab.services.ledger import LedgerService


class Engine:
    defaults = {"trees": 1}

    def __init__(self, context):
        self.context = context
        self.closed = 0

    def index(self):
        raw = self.context.read_file("models.json")
        return json.loads(raw) if raw else {"active": None, "models": []}

    def select(self, identifier):
        self.context.write_file("models.json", json.dumps({"active": identifier, "models": []}).encode())

    def delete(self, identifier):
        self.context.delete_file("models.json")

    def rename(self, identifier, name):
        self.select(name)

    def train(self, rows, parameters):
        yield {"stage": "Training fictional model", "progress": 50}
        return {"id": "fictional-model"}

    def save(self, model, name):
        self.select(model["id"])
        return model

    def predict(self, rows):
        return []

    def close(self):
        self.closed += 1


def descriptor(identifier="first", factory=Engine):
    return Plugin(identifier, identifier, categorization=Categorization(factory),
                  contract=PluginContract(lifecycle="live"))


def registry(plugins):
    return SimpleNamespace(descriptors=lambda: plugins)


def setup(tmp_path, store, plugins=()):
    ledger = LedgerService(store, budget_id="fictional")
    root = tmp_path / "budgets" / "fictional" / "plugin-data"

    def snapshot(identifier):
        base = root / identifier
        return {path.relative_to(base).as_posix(): path.read_bytes()
                for path in base.rglob("*") if path.is_file()}

    lifecycle = CategorizationLifecycle(ledger, lambda name: local_context(tmp_path, "fictional", name), snapshot)
    service, resource = lifecycle.initial(plugins)
    runtime = BudgetRuntime("fictional", {"categorization": service, "plugins": registry(plugins)},
                            (resource,) if resource else ())
    return ledger, lifecycle, service, runtime, root


async def change(runtime, lifecycle, plugins, commit=None):
    async def finalize(preparation, candidate):
        return await run_in_threadpool(lifecycle.finalize, preparation, candidate)

    def committed(candidate, number):
        if commit:
            commit(candidate, number)
        lifecycle.committed(candidate)

    return await runtime.transition(
        lambda prep: RuntimeCandidate({**prep.active.services, "plugins": registry(plugins)}, prep.active.resources),
        finalize=finalize, commit=committed,
    )


def test_enable_disable_replace_preserves_namespaces_and_retires_callbacks(tmp_path, store):
    async def scenario():
        first, second = descriptor(), descriptor("second")
        ledger, lifecycle, initial, runtime, root = setup(tmp_path, store)
        facade = LeasedCategorization(runtime)
        ledger.categorization = facade
        assert not facade.enabled()
        assert await change(runtime, lifecycle, (first,)) == 2
        engine = facade.engines["first"]
        engine.select("saved-first")
        facade.demo_engines["first"].select("saved-demo")
        assert await change(runtime, lifecycle, (second,)) == 3
        assert engine.closed == 1
        with pytest.raises(StoreError, match="retired"):
            engine.context.write_file("late.json", b"late")
        assert set(facade.contexts) == {"first", "second"}
        assert (root / "first" / "models.json").read_bytes() == b'{"active": "saved-first", "models": []}'
        assert await change(runtime, lifecycle, ()) == 4
        assert not facade.enabled()
        assert await change(runtime, lifecycle, (first,)) == 5
        assert facade.engines["first"].index()["active"] == "saved-first"
        assert facade.demo_engines["first"].index()["active"] == "saved-demo"
        await runtime.shutdown()
        # A fresh host constructs from the exact same namespace, without copying
        # models into another plugin or merging personal and demo histories.
        reopened, _ = lifecycle.initial((first,))
        assert reopened.engines["first"].index()["active"] == "saved-first"
        assert reopened.demo_engines["first"].index()["active"] == "saved-demo"
        reopened.close()
        assert not (root / "first" / "late.json").exists()
        assert not initial.engines

    asyncio.run(scenario())


def test_unchanged_categorizer_retains_engine_and_training_continuation(tmp_path, store):
    async def scenario():
        plugin = descriptor()
        _, lifecycle, service, runtime, _ = setup(tmp_path, store, (plugin,))
        run = service.action("personal", {"action": "train", "pluginId": "first"})
        assert await change(runtime, lifecycle, (plugin,)) == 2
        assert runtime._active.services["categorization"] is service
        assert run["session"] in service.runs
        assert not service.engines["first"].closed
        await runtime.shutdown()
        assert service.engines["first"].closed == 1
        assert not service.runs

    asyncio.run(scenario())


def test_idle_unsaved_training_defers_change_until_cancel_or_finish(tmp_path, store):
    async def scenario():
        plugin = descriptor()
        _, lifecycle, service, runtime, _ = setup(tmp_path, store, (plugin,))
        values = {"action": "train", "pluginId": "first"}
        run = service.action("personal", values)
        with pytest.raises(RuntimeTransitionError) as caught:
            await change(runtime, lifecycle, ())
        assert caught.value.code == "continuation_busy" and caught.value.plugin_id == "first"
        assert runtime.status()["admission"] == "open" and runtime.status()["generation"] == 1
        assert run["session"] in service.runs
        assert service.action("personal", {**values, "action": "step", "session": run["session"]})["progress"] == 50
        service.action("personal", {**values, "action": "cancel", "session": run["session"]})
        assert await change(runtime, lifecycle, ()) == 2
        await runtime.shutdown()

    asyncio.run(scenario())


def test_finalizer_observes_model_saved_by_active_step_before_drain(tmp_path, store):
    async def scenario():
        entered, finish = Event(), Event()

        class HeldEngine(Engine):
            def save(self, model, name):
                entered.set()
                assert finish.wait(5)
                return super().save(model, name)

        first, upgraded = descriptor(factory=HeldEngine), descriptor(factory=Engine)
        _, lifecycle, service, runtime, _ = setup(tmp_path, store, (first,))
        run = service.action("personal", {"action": "train", "pluginId": "first"})
        step = {"action": "step", "pluginId": "first", "session": run["session"]}
        service.action("personal", step)
        async def held():
            async with runtime.request():
                return await run_in_threadpool(service.action, "personal", step)

        operation = asyncio.create_task(held())
        assert await asyncio.to_thread(entered.wait, 5)
        transition = asyncio.create_task(change(runtime, lifecycle, (upgraded,)))
        await runtime.wait_for_phase("draining")
        finish.set()
        assert (await operation)["complete"]
        assert await transition == 2
        current = runtime._active.services["categorization"]
        assert current.engines["first"].index()["active"] == "fictional-model"
        assert service.engines["first"].closed == 1
        await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["write_file", "delete_file", "save_state"])
def test_candidate_factory_cannot_publish_and_failure_preserves_active_model(tmp_path, store, operation):
    async def scenario():
        retained = []

        def bad(context):
            retained.append(context)
            arguments = {"write_file": ("models.json", b"overwrite"), "delete_file": ("models.json",),
                         "save_state": ({"overwrite": True},)}[operation]
            getattr(context, operation)(*arguments)
            return Engine(context)

        first = descriptor()
        _, lifecycle, service, runtime, root = setup(tmp_path, store, (first,))
        service.engines["first"].select("retained")
        before = (root / "first" / "models.json").read_bytes()
        with pytest.raises(RuntimeTransitionError) as caught:
            await change(runtime, lifecycle, (descriptor(factory=bad),))
        assert caught.value.code == "readiness_failed"
        assert caught.value.plugin_id == "first"
        assert runtime._active.services["categorization"] is service
        assert not service.engines["first"].closed
        assert (root / "first" / "models.json").read_bytes() == before
        with pytest.raises(StoreError, match="retired"):
            retained[0].read_file("models.json")
        await runtime.shutdown()

    asyncio.run(scenario())


def test_candidate_failure_closes_both_engines_and_no_private_error_survives(tmp_path, store):
    async def scenario():
        created = []

        class Broken(Engine):
            def index(self):
                raise ValueError("private vocabulary secret")

        def factory(context):
            engine = Broken(context)
            created.append(engine)
            return engine

        _, lifecycle, service, runtime, _ = setup(tmp_path, store)
        with pytest.raises(RuntimeTransitionError):
            await change(runtime, lifecycle, (descriptor(factory=factory),))
        assert len(created) == 2 and all(engine.closed == 1 for engine in created)
        assert runtime._active.services["categorization"] is service
        assert "private vocabulary" not in str(runtime.status())
        await runtime.shutdown()

    asyncio.run(scenario())


def test_engine_factory_failure_closes_previous_engine_and_revokes_context(tmp_path, store):
    contexts, created = [], []

    def factory(context):
        contexts.append(context)
        if len(contexts) == 2:
            raise ValueError("private vocabulary secret")
        engine = Engine(context)
        created.append(engine)
        return engine

    ledger = LedgerService(store, budget_id="fictional")
    with pytest.raises(PluginError) as caught:
        CategorizationService(ledger, (descriptor(factory=factory),), lambda key: local_context(tmp_path, "fictional", key))
    assert caught.value.code == "categorization_startup_failed" and created[0].closed == 1
    assert "private vocabulary" not in str(caught.value)
    for context in contexts:
        with pytest.raises(StoreError, match="retired"):
            context.write_file("delayed.json", b"late")


def test_readiness_snapshot_is_detached_bounded_and_closed(tmp_path):
    base = local_context(tmp_path, "fictional", "first")
    files = {"models.json": b"before"}
    namespace = ReadinessNamespace("first", base, files)
    context = namespace.context()
    files["models.json"] = b"outside change"
    assert context.read_file("models.json") == b"before"
    for name in ("../escape", "/absolute", "demo/../../escape", "state.json", None):
        with pytest.raises(StoreError):
            context.read_file(name)
    namespace.activate()
    context.write_file("models.json", b"after")
    assert context.read_file("models.json") == b"after"
    namespace.close()
    for callback, args in ((context.read_file, ("models.json",)), (context.write_file, ("models.json", b"late")),
                           (context.delete_file, ("models.json",)), (context.save_state, ({},))):
        with pytest.raises(StoreError, match="retired"):
            callback(*args)
    assert base.read_file("models.json") == b"after"
    with pytest.raises(StoreError):
        namespace.activate()


def test_live_support_requires_exact_scoped_categorization_adapter():
    plugin = descriptor()
    assert live_categorizer(plugin)
    assert not live_categorizer(replace(plugin, contract=None))
    assert not live_categorizer(replace(plugin, contract=PluginContract()))
    assert not live_categorizer(replace(plugin, insights=lambda _: ()))
    assert not live_categorizer(replace(plugin, contract=PluginContract(lifecycle="live", state_version=2, state_upgrades=((1, 2),))))


def test_complete_local_snapshot_rejects_links_and_preserves_opaque_files(tmp_path, monkeypatch):
    context = local_context(tmp_path, "fictional", "first")
    assert local_files(tmp_path, "fictional", "first") == {}
    context.write_file("models.json", b"model-index")
    context.write_file("future/opaque.bin", b"\x00\xff\x01")
    assert local_files(tmp_path, "fictional", "first") == {
        "models.json": b"model-index", "future/opaque.bin": b"\x00\xff\x01",
    }
    root = tmp_path / "budgets" / "fictional" / "plugin-data" / "first"
    other = tmp_path / "other"
    other.mkdir()
    (root / "link").symlink_to(other, target_is_directory=True)
    with pytest.raises(StoreError, match="symbolic link"):
        local_files(tmp_path, "fictional", "first")
    (root / "link").unlink()
    (root / "future" / "alias").symlink_to(other / "secret")
    for callback, arguments in ((context.read_file, ("future/alias",)),
                                 (context.write_file, ("future/alias", b"overwrite")),
                                 (context.delete_file, ("future/alias",))):
        with pytest.raises(StoreError):
            callback(*arguments)
    (root / "future" / "alias").unlink()
    monkeypatch.setattr("whab.services.categorization.MAX_BUNDLE_BYTES", 1)
    with pytest.raises(StoreError, match="supported size"):
        local_files(tmp_path, "fictional", "first")
    with pytest.raises(StoreError):
        context.write_file("models.json", "not-bytes")
    # Replacing a root after obtaining callbacks cannot redirect another owner.
    root.rename(root.with_name("old"))
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises(StoreError):
        context.write_file("new.json", b"late")
    assert not (other / "new.json").exists()


def test_local_callbacks_share_host_lock_and_copy_failure_does_not_undo_save(tmp_path):
    from threading import RLock

    lock, changes = RLock(), []

    def changed():
        assert lock._is_owned()
        changes.append(True)
        raise OSError("synthetic copy failure")

    context = local_context(tmp_path, "fictional", "first", lock=lock, on_change=changed)
    context.write_file("models.json", b"committed")
    assert context.read_file("models.json") == b"committed"
    context.delete_file("models.json")
    assert changes == [True, True] and context.read_file("models.json") is None


@pytest.mark.parametrize("invalid", ["awaitable", "missing", "badclose"])
def test_engine_shape_validation_closes_malformed_resources(tmp_path, store, invalid):
    closed = []

    async def async_factory(_):
        return Engine(None)

    def factory(_):
        return SimpleNamespace(defaults={}, close=(1 if invalid == "badclose" else lambda: closed.append(True)))

    ledger = LedgerService(store, budget_id="fictional")
    with pytest.raises(PluginError) as caught:
        CategorizationService(ledger, (descriptor(factory=async_factory if invalid == "awaitable" else factory),),
                              lambda key: local_context(tmp_path, "fictional", key))
    assert caught.value.code == "categorization_startup_failed"
    assert closed == ([True] if invalid == "missing" else [])


def test_cleanup_attempts_every_engine_even_if_one_fails_and_is_idempotent(tmp_path, store):
    closed = []

    class BadClose(Engine):
        def close(self):
            closed.append(self.context)
            if len(closed) == 1:
                raise RuntimeError("private model vocabulary")

    _, _, service, _, _ = setup(tmp_path, store, (descriptor(factory=BadClose),))
    with pytest.raises(StoreError) as caught:
        service.close()
    assert len(closed) == 2 and "private model" not in str(caught.value)
    service.close()
    assert len(closed) == 2
    for context in closed:
        with pytest.raises(StoreError, match="retired"):
            context.write_file("late", b"late")


@asynccontextmanager
async def running(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:5173") as client:
            yield client


def install(monkeypatch, *plugins):
    entries = tuple(SimpleNamespace(name=plugin.id, group="whab.plugins", load=lambda item=plugin: item)
                    for plugin in plugins)
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **_: entries)


async def settle(app):
    if app.state.live_plugins._tasks:
        await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)


def test_application_live_selection_training_deferral_and_reopen(tmp_path, store, monkeypatch):
    from whab_plugin_sqlite.plugin import plugin as sqlite

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import PluginRegistry
    from whab.plugins.configuration import read_selection

    async def scenario():
        first, second = descriptor(), descriptor("second")
        install(monkeypatch, sqlite, first, second)
        settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
        app = create_app(store=store, settings=settings, plugins=PluginRegistry((sqlite,)))
        ledger = app.state.ledger_service
        revision, lock = ledger.revision, ledger._lock
        async with running(app) as client:
            path = "/api/plugins/management/transition"
            assert (await client.post(path, json={"plugins": ["first"]})).status_code == 202
            await settle(app)
            assert read_selection(tmp_path) == ("first",)
            assert ledger.categorization.enabled()

            async def action(name, **values):
                result = await client.post("/api/categorization", json={
                    "workspace": "personal", "payload": {"action": name, "pluginId": "first", **values},
                })
                assert result.status_code == 200, result.text
                return result.json()

            run = await action("train")
            busy = await client.post(path, json={"plugins": ["second"]})
            assert busy.status_code == 409 and busy.json()["code"] == "continuation_busy"
            assert busy.json()["plugin_id"] == "first"
            assert read_selection(tmp_path) == ("first",)
            assert (await action("step", session=run["session"]))["progress"] == 50
            assert (await action("step", session=run["session"]))["complete"]
            revision = ledger.revision
            old = ledger.categorization.engines["first"]
            assert (await client.post(path, json={"plugins": ["second"]})).status_code == 202
            await settle(app)
            assert old.closed == 1 and set(ledger.categorization.engines) == {"second"}
            assert (await client.post(path, json={"plugins": []})).status_code == 202
            await settle(app)
            assert not ledger.categorization.enabled()
            assert (await client.post(path, json={"plugins": ["first"]})).status_code == 202
            await settle(app)
            assert ledger.revision == revision and ledger._lock is lock
            assert (await action("status"))["active"] == "fictional-model"
        reopened = create_app(store=store, settings=replace(settings, plugins=("first",)),
                              plugins=PluginRegistry((sqlite, first)))
        async with running(reopened) as client:
            result = await client.post("/api/categorization", json={
                "workspace": "personal", "payload": {"action": "status", "pluginId": "first"},
            })
            assert result.status_code == 200 and result.json()["active"] == "fictional-model"

    asyncio.run(scenario())


def test_application_failed_factory_keeps_saved_selection_and_active_namespace(tmp_path, store, monkeypatch):
    from whab_plugin_sqlite.plugin import plugin as sqlite

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import PluginRegistry
    from whab.plugins.configuration import read_selection

    async def scenario():
        retained = []

        def invalid(context):
            retained.append(context)
            context.write_file("models.json", b"private malformed model")

        first, invalid_plugin = descriptor(), descriptor("second", invalid)
        install(monkeypatch, sqlite, first, invalid_plugin)
        settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ("first",), ui="")
        app = create_app(store=store, settings=settings, plugins=PluginRegistry((sqlite, first)))
        async with running(app) as client:
            old = app.state.ledger_service.categorization.engines["first"]
            old.select("retained")
            response = await client.post("/api/plugins/management/transition", json={"plugins": ["second"]})
            assert response.status_code == 202
            await settle(app)
            status = (await client.get("/api/plugins/runtime")).json()
            assert status["operations"][-1]["error"]["code"] == "readiness_failed"
            assert status["operations"][-1]["error"]["plugin_id"] == "second"
            assert status["runtime"]["generation"] == 1 and status["runtime"]["admission"] == "open"
            assert read_selection(tmp_path) is None
            assert old.index()["active"] == "retained" and old.closed == 0
            assert "private malformed model" not in str(status)
            with pytest.raises(StoreError, match="retired"):
                retained[0].read_file("models.json")

    asyncio.run(scenario())


@pytest.mark.parametrize("written", [False, True])
def test_uncertain_selection_recovery_activates_only_validated_namespace(tmp_path, store, monkeypatch, written):
    from whab_plugin_sqlite.plugin import plugin as sqlite

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import PluginRegistry

    async def scenario():
        first, second = descriptor(), descriptor("second")
        install(monkeypatch, sqlite, first, second)
        settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ("first",), ui="")
        app = create_app(store=store, settings=settings, plugins=PluginRegistry((sqlite, first)))
        live = app.state.live_plugins
        persist = live.persist

        def uncertain(*args):
            if written:
                persist(*args)
            raise OSError("private lost response")

        live.persist = uncertain
        async with running(app):
            old = app.state.ledger_service.categorization.engines["first"]
            with pytest.raises(RuntimeTransitionError) as caught:
                await live.activate(("second",))
            assert caught.value.code == "commit_uncertain"
            assert app.state.budget_runtime.status()["admission"] == "gated"
            assert old.closed == 0
            assert await live.recover("candidate" if written else "active") == (2 if written else 1)
            active = app.state.ledger_service.categorization
            engine = active.engines["second" if written else "first"]
            engine.select("after-recovery")
            assert engine.index()["active"] == "after-recovery"
            assert old.closed == int(written)

    asyncio.run(scenario())


def test_unstarted_application_shutdown_releases_owned_worker_and_namespace_journal(tmp_path, store):
    from whab_plugin_random_forest_categorization.plugin import plugin

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import PluginRegistry
    from whab.plugins.categorization_worker import WorkerCategorizer

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (plugin.id,), ui="")
    first = create_app(store=store, settings=settings, plugins=PluginRegistry((plugin,)))
    engine = first.state.ledger_service.categorization.engines[plugin.id]
    assert isinstance(engine, WorkerCategorizer)
    worker = engine.factory.worker
    assert worker.healthy
    asyncio.run(first.state.budget_runtime.shutdown())
    assert not worker.healthy and not worker.cleanup_pending
    assert engine.closed
    # No ASGI lifespan entered: the same process can open the same private
    # journal again without a leaked lifetime lock or module-global engine.
    second = create_app(store=store, settings=settings, plugins=PluginRegistry((plugin,)))
    try:
        assert second.state.ledger_service.categorization.engines[plugin.id].index()['models'] == []
    finally:
        asyncio.run(second.state.budget_runtime.shutdown())


def test_portable_host_state_edits_join_authoritative_journal_when_disabled_and_reopen(tmp_path):
    from fastapi.testclient import TestClient
    from whab_plugin_random_forest_categorization.plugin import plugin

    from whab.app import create_app
    from whab.budgets.session import BudgetSession
    from whab.config import Settings

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    session = BudgetSession.create(tmp_path, "sandbox", "Fictional worker budget", (plugin.id,))
    app = create_app(settings=settings, budget_session=session)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        manager = app.state.ledger_service.categorization.engines[plugin.id].factory.effects
        manager.context(writable=True).write("unknown.bin", b"preserve exact own bytes")
        checkpoint = []
        session.after_checkpoint = lambda: checkpoint.append(session.path.read_bytes())
        response = client.put(f"/api/budget/plugins/{plugin.id}", json={
            "state": {"operatorEdit": 1}, "settings": {"label": "Fictional settings"}})
        assert response.status_code == 200, response.text
        assert response.json()["state"] == manager.snapshot.state == {"operatorEdit": 1}
        assert response.json()["settings"] == {"label": "Fictional settings"}
        assert len(checkpoint) == 1
        response = client.post("/api/plugins/management/transition", json={"plugins": []})
        assert response.status_code == 202
        async def settle():
            if app.state.live_plugins._tasks:
                await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)
        client.portal.call(settle)
        assert not app.state.ledger_service.categorization.enabled()
        response = client.put(f"/api/budget/plugins/{plugin.id}", json={"state": {"operatorEdit": 2}})
        assert response.status_code == 200, response.text
        assert response.json()["state"] == manager.snapshot.state == {"operatorEdit": 2}
        assert response.json()["settings"] == {"label": "Fictional settings"}
        assert manager.snapshot.files["unknown.bin"] == b"preserve exact own bytes"
        raw = manager.journal.store.path.read_bytes()
        invalid = client.put(f"/api/budget/plugins/{plugin.id}", json={"state": {"operatorEdit": 3},
            "settings": {"accessToken": "forbidden-fictional-secret"}})
        assert invalid.status_code == 400
        assert manager.journal.store.path.read_bytes() == raw
    reopened = create_app(settings=settings, budget_session=session)
    with TestClient(reopened, base_url="https://127.0.0.1:5173") as client:
        record = client.get(f"/api/budget/plugins/{plugin.id}").json()
        assert not record["enabled"] and record["state"] == {"operatorEdit": 2}
        assert record["settings"] == {"label": "Fictional settings"}
        assert session.read_file(plugin.id, "unknown.bin") == b"preserve exact own bytes"


def test_journal_open_failure_closes_child_acquired_before_namespace_binding(tmp_path, store, monkeypatch):
    from whab.plugins.effect_store import EffectError

    child = SimpleNamespace(closed=0, cleanup_pending=False)
    def close():
        child.closed += 1
    child.close = close
    entry = SimpleNamespace(name='first', dist=SimpleNamespace(metadata={'Name': 'fictional-package'}, version='1.0.0'))
    monkeypatch.setattr('whab.services.categorization_runtime.metadata.entry_points', lambda **_: (entry,))
    monkeypatch.setattr('whab.plugins.report_worker.ReportWorker', lambda *_args, **_kwargs: child)
    lifecycle = CategorizationLifecycle(LedgerService(store, budget_id='fictional'),
        lambda name: local_context(tmp_path, 'fictional', name), lambda _: {},
        worker_home=tmp_path, materialization=nullcontext)
    monkeypatch.setattr(lifecycle, '_namespace', lambda _: (_ for _ in ()).throw(EffectError('effect_unavailable')))
    with pytest.raises(EffectError):
        lifecycle.initial((descriptor(),))
    assert child.closed == 1


def test_partial_worker_hello_failure_retains_owner_and_refuses_another_child_until_reaped(tmp_path, store, monkeypatch):
    import base64
    import os
    import signal
    import subprocess
    import sys

    from whab.plugins import report_worker
    from whab.plugins.wire import Frame

    packet = base64.b64encode(Frame({'bad': 'hello'}).encode()).decode()
    source = (f'import base64,signal,sys;sys.stdout.buffer.write(base64.b64decode({packet!r}));'
              'sys.stdout.buffer.flush();signal.pause()')
    popen, children = subprocess.Popen, []
    def start(_args, **kwargs):
        child = popen([sys.executable, '-I', '-u', '-c', source], **kwargs)
        children.append(child)
        return child
    entry = SimpleNamespace(name='first', dist=SimpleNamespace(metadata={'Name': 'fictional-package'}, version='1.0.0'))
    lifecycle = CategorizationLifecycle(LedgerService(store, budget_id='fictional'),
        lambda name: local_context(tmp_path, 'fictional', name), lambda _: {},
        worker_home=tmp_path, materialization=nullcontext)
    try:
        with monkeypatch.context() as failure:
            failure.setattr(report_worker.subprocess, 'Popen', start)
            failure.setattr(report_worker, '_stop_process_group', lambda _: (_ for _ in ()).throw(OSError('fictional reap failure')))
            failure.setattr('whab.services.categorization_runtime.metadata.entry_points', lambda **_: (entry,))
            with pytest.raises(PluginError) as error:
                lifecycle.initial((descriptor(),))
            assert error.value.code == 'worker_startup_failed'
            worker, = lifecycle._pending_workers
            assert worker.cleanup_pending and children[0].poll() is None
            with pytest.raises(PluginError) as error:
                lifecycle.close()
            assert error.value.code == 'worker_cleanup_pending'
            with pytest.raises(PluginError) as error:
                lifecycle._service((descriptor(),))
            assert error.value.code == 'worker_cleanup_pending'
            assert len(children) == 1 and lifecycle._pending_workers == [worker]
        lifecycle.close()
        assert children[0].poll() is not None and not lifecycle._pending_workers
        service = lifecycle._service(())
        assert not service.enabled()
        service.close()
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
