"""Construction failures release initial owners without entering a lifespan."""

import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

import whab.app as composition
from whab.budgets.runtime import RuntimeResource
from whab.config import Settings
from whab.plugins import Categorization, Interface, Plugin, PluginError, PluginRegistry
from whab.plugins.ui_selection import UISelectionStore
from whab.services.categorization_runtime import CategorizationLifecycle


class Engine:
    defaults = {}

    def __init__(self, _context):
        self.closed = 0

    def index(self):
        return {"active": None, "models": []}

    def select(self, *_args):
        pass

    delete = rename = train = save = predict = select

    def close(self):
        self.closed += 1


def configuration(home):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui="")


def owners(monkeypatch):
    """Observe real native, engine and journal ownership, without mocking close."""
    observed = SimpleNamespace(stores=[], engines=[], lifecycles=[], selections=[])
    open_storage = composition.open_storage
    initial = CategorizationLifecycle.initial
    selection = composition.UISelectionStore

    def native(*args, **kwargs):
        store = open_storage(*args, **kwargs)
        close = store.close
        record = SimpleNamespace(owner=store, closed=0)

        def release():
            record.closed += 1
            close()

        store.close = release
        observed.stores.append(record)
        return store

    def category(self, *args, **kwargs):
        # A disabled namespace remains an owned private journal on reopen too.
        self._namespace("fictional")
        service, resource = initial(self, *args, **kwargs)
        observed.engines.extend((*service.engines.values(), *service.demo_engines.values()))
        observed.lifecycles.append(self)
        return service, resource

    def visual(*args, **kwargs):
        owner = selection(*args, **kwargs)
        observed.selections.append(owner)
        return owner

    monkeypatch.setattr(composition, "open_storage", native)
    monkeypatch.setattr(CategorizationLifecycle, "initial", category)
    monkeypatch.setattr(composition, "UISelectionStore", visual)
    return observed


def plugins():
    return PluginRegistry((sqlite_plugin, Plugin("fictional", "Fictional categorizer", categorization=Categorization(Engine))))


def fail_at(monkeypatch, phase, error):
    def fail(*_args, **_kwargs):
        raise error

    if "." in phase:
        owner, name = phase.split(".")
        monkeypatch.setattr(getattr(composition, owner), name, fail)
    else:
        monkeypatch.setattr(composition, phase, fail)


@pytest.mark.parametrize("phase", [
    "VisualController", "StorageRuntime", "StorageReplicas", "FastAPI.include_router",
    "health_router", "StorageRuntime.attach", "StorageRuntime.router", "StorageReplicas.router",
    "BudgetHost.router", "create_remote_router", "BudgetHost.register",
])
def test_tail_failure_closes_owned_resources_and_same_journals_reopen(tmp_path, monkeypatch, phase):
    observed = owners(monkeypatch)
    error = RuntimeError("Fictional construction boundary")
    with monkeypatch.context() as faults:
        fail_at(faults, phase, error)
        with pytest.raises(RuntimeError) as caught:
            composition.create_app(settings=configuration(tmp_path), plugins=plugins())
    assert caught.value is error
    assert observed.stores[0].closed == 1
    assert [engine.closed for engine in observed.engines] == [1, 1]
    assert observed.lifecycles[0]._effects == {}
    with UISelectionStore(tmp_path, "default", "sandbox", None) as selection:
        assert selection.snapshot()["selection"] is None
    # Reopening the complete construction also reacquires the category journal.
    app = composition.create_app(settings=configuration(tmp_path), plugins=plugins())
    assert observed.stores[1].closed == 0
    asyncio.run(app.state.budget_runtime.shutdown())
    app.state.store.close()
    assert [engine.closed for engine in observed.engines] == [1, 1, 1, 1]


@pytest.mark.parametrize("error", [asyncio.CancelledError(), SystemExit(27)])
def test_startup_cleanup_preserves_base_exception_inside_running_loop(tmp_path, monkeypatch, store, error):
    observed = owners(monkeypatch)
    fail_at(monkeypatch, "StorageRuntime", error)

    async def run():
        with pytest.raises(type(error)) as caught:
            composition.create_app(store=store, settings=configuration(tmp_path), plugins=plugins())
        assert caught.value is error
        await asyncio.sleep(0)

    asyncio.run(run())
    assert not observed.stores  # A caller-owned backend remains usable.
    assert store.accounts("personal") == []
    assert [engine.closed for engine in observed.engines] == [1, 1]
    assert observed.lifecycles[0]._effects == {}
    with UISelectionStore(tmp_path, "default", "sandbox", None):
        pass


def test_legacy_interface_failure_closes_visual_and_category_journals(tmp_path, monkeypatch):
    observed = owners(monkeypatch)

    def broken(_context):
        raise RuntimeError("Fictional private factory details")

    registry = PluginRegistry((*plugins().descriptors(), Plugin("legacy", "Legacy API", interface=Interface(broken))))
    with pytest.raises(PluginError) as caught:
        composition.create_app(settings=configuration(tmp_path), plugins=registry)
    assert caught.value.code == "interface_startup_failed"
    assert "private" not in str(caught.value)
    assert observed.stores[0].closed == 1
    assert [engine.closed for engine in observed.engines] == [1, 1]
    assert observed.lifecycles[0]._effects == {}
    with UISelectionStore(tmp_path, "default", "sandbox", None):
        pass


def test_failed_worker_cleanup_retains_owner_and_retries_without_double_close(tmp_path, monkeypatch):
    observed = owners(monkeypatch)

    class Child:
        timeout = 30
        cleanup_pending = False
        closed = 0

        def close(self):
            self.closed += 1
            self.cleanup_pending = self.closed == 1
            if self.cleanup_pending:
                raise RuntimeError("Fictional reap failure")

    worker = Child()
    binding = SimpleNamespace(worker=worker, resource=RuntimeResource("fictional-worker", worker.close))
    monkeypatch.setattr(composition, "restore_workers", lambda _home, registry, _existing, **_kwargs: (registry, {"fictional-worker": binding}))
    error = RuntimeError("Fictional tail failure")
    fail_at(monkeypatch, "StorageRuntime", error)
    with pytest.raises(RuntimeError) as caught:
        composition.create_app(settings=configuration(tmp_path), plugins=plugins())
    assert caught.value is error
    assert worker.closed == 1 and worker.timeout == 1 and worker.cleanup_pending
    assert observed.stores[0].closed == 1
    assert [engine.closed for engine in observed.engines] == [1, 1]
    assert observed.lifecycles[0]._effects == {}
    with UISelectionStore(tmp_path, "default", "sandbox", None):
        pass
    assert error._whab_startup_cleanup.retry()
    assert worker.closed == 2 and not worker.cleanup_pending
    assert error._whab_startup_cleanup.retry()
    assert worker.closed == 2 and observed.stores[0].closed == 1


def test_tail_failure_revokes_prepared_api_binding(tmp_path, monkeypatch, store):
    from whab.plugins import PluginContract, RemoteLedgerInterface

    monkeypatch.setenv("FICTIONAL_API_TOKEN", "fictional-secret-token-" * 3)
    descriptor = Plugin("fictional-api", "Fictional API", interface=Interface(
        adapter=RemoteLedgerInterface(token_setting="FICTIONAL_API_TOKEN")), contract=PluginContract(lifecycle="live"))
    prepared, original = [], composition.prepare_interfaces

    def prepare(*args, **kwargs):
        result = original(*args, **kwargs)
        prepared.append(result.binding)
        return result

    monkeypatch.setattr(composition, "prepare_interfaces", prepare)
    fail_at(monkeypatch, "BudgetHost.register", RuntimeError("Fictional registration failure"))
    with pytest.raises(RuntimeError):
        composition.create_app(store=store, settings=configuration(tmp_path), plugins=PluginRegistry((descriptor,)))
    assert prepared[0]._closed and prepared[0]._token == b""


def test_installed_category_child_is_reaped_on_tail_failure(tmp_path, monkeypatch, store):
    from whab.plugins import load_plugins
    from whab.plugins.categorization_worker import WorkerCategorizationFactory

    observed = owners(monkeypatch)
    factories, initial = [], CategorizationLifecycle.initial

    def started(self, *args, **kwargs):
        service, resource = initial(self, *args, **kwargs)
        factories.extend(service._worker_factories)
        return service, resource

    monkeypatch.setattr(CategorizationLifecycle, "initial", started)
    error = RuntimeError("Fictional post-worker failure")
    fail_at(monkeypatch, "StorageRuntime", error)
    with pytest.raises(RuntimeError) as caught:
        composition.create_app(store=store, settings=configuration(tmp_path), plugins=load_plugins(("random-forest-categorization",)))
    assert caught.value is error
    assert len(factories) == 1 and isinstance(factories[0], WorkerCategorizationFactory)
    assert not factories[0].worker.healthy and not factories[0].worker.cleanup_pending
    assert observed.lifecycles[0]._effects == {}
    with UISelectionStore(tmp_path, "default", "sandbox", None):
        pass


def test_discarded_failure_keeps_scope_owner_and_blocks_new_backend_until_reaped(tmp_path, monkeypatch):
    observed = owners(monkeypatch)

    class Child:
        timeout = 30
        cleanup_pending = True
        closed = 0
        blocked = True

        def close(self):
            self.closed += 1
            self.cleanup_pending = self.blocked

    child = Child()
    binding = SimpleNamespace(worker=child, resource=RuntimeResource("fictional-worker", child.close))
    with monkeypatch.context() as faults:
        faults.setattr(composition, "restore_workers", lambda _home, registry, _existing, **_kwargs: (registry, {"fictional-worker": binding}))
        fail_at(faults, "StorageRuntime", RuntimeError("Discard this fictional construction exception"))
        try:
            composition.create_app(settings=configuration(tmp_path), plugins=plugins())
        except RuntimeError:
            pass
    with pytest.raises(PluginError) as caught:
        composition.create_app(settings=configuration(tmp_path), plugins=plugins())
    assert caught.value.code == "startup_cleanup_pending"
    assert len(observed.stores) == 1 and child.closed == 2
    # A different runtime home is not held behind this budget's pending child.
    other = composition.create_app(settings=configuration(tmp_path / "other"), plugins=plugins())
    asyncio.run(other.state.budget_runtime.shutdown())
    other.state.store.close()
    child.blocked = False
    app = composition.create_app(settings=configuration(tmp_path), plugins=plugins())
    assert child.closed == 3 and len(observed.stores) == 3
    asyncio.run(app.state.budget_runtime.shutdown())
    app.state.store.close()
    assert composition.retry_startup_cleanup(tmp_path, "default", "sandbox")


def test_recovery_retry_reaps_discarded_failure_then_requires_fresh_visual_revision(tmp_path, monkeypatch):
    from whab.plugins.recovery import RecoveryApplication

    observed = owners(monkeypatch)
    original = composition.UISelectionStore
    blocked = True

    def retained(*args, **kwargs):
        owner = original(*args, **kwargs)
        close = owner.close

        def release():
            if blocked:
                raise OSError("Fictional retained visual lock")
            close()

        owner.close = release
        return owner

    with monkeypatch.context() as faults:
        faults.setattr(composition, "UISelectionStore", retained)
        fail_at(faults, "StorageRuntime", RuntimeError("Discard this fictional construction exception"))
        try:
            composition.create_app(settings=configuration(tmp_path), plugins=plugins())
        except RuntimeError:
            pass
    calls = []

    def factory(**arguments):
        calls.append(True)
        return composition.create_app(plugins=plugins(), **arguments)

    shell = RecoveryApplication(configuration(tmp_path), factory=factory)
    with TestClient(shell, base_url="https://127.0.0.1:5173") as client:
        before = client.get("/api/recovery").json()
        assert before["visual"] is None
        assert before["errors"][0]["code"] == "visual_decision_unreadable"
        assert len(observed.stores) == 1  # GET inspection cannot kill/reap.
        refused = client.post("/api/recovery/retry", json={"revision": before["revision"]})
        assert refused.json()["error"]["code"] == "startup_cleanup_pending"
        assert not calls and len(observed.stores) == 1
        blocked = False
        stale = client.post("/api/recovery/retry", json={"revision": before["revision"]})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
        assert not calls and len(observed.stores) == 1
        fresh = client.get("/api/recovery").json()
        assert not fresh["errors"] and fresh["visual"]["plugin_id"] is None
        assert client.post("/api/recovery/retry", json={"revision": fresh["revision"]}).json() == {"started": True}
        assert calls == [True] and len(observed.stores) == 2


def test_recovery_shutdown_retries_retained_startup_owner(tmp_path, monkeypatch):
    from whab.plugins.recovery import RecoveryApplication

    child = SimpleNamespace(timeout=10, cleanup_pending=True, closed=0)

    def release():
        child.closed += 1
        child.cleanup_pending = child.closed == 1

    child.close = release
    retained = composition._StartupResources(tmp_path, "default", "sandbox")
    retained.worker(child)
    retained.close(RuntimeError("Discard this fictional failure"))
    assert child.closed == 1
    with TestClient(RecoveryApplication(configuration(tmp_path)), base_url="https://127.0.0.1:5173") as client:
        assert client.get("/api/recovery").status_code == 200
        assert child.closed == 1
    assert child.closed == 2 and not child.cleanup_pending
    assert composition.retry_startup_cleanup(tmp_path, "default", "sandbox")


@pytest.mark.parametrize("restore", ["restore_visual_worker", "restore_workers"])
def test_restore_constructor_failure_owns_child_before_binding_is_returned(tmp_path, monkeypatch, restore):
    observed = owners(monkeypatch)
    child = SimpleNamespace(timeout=20, cleanup_pending=False, closed=0)

    def release():
        child.closed += 1

    child.close = release
    error = PluginError("Fictional malformed startup handshake", code="worker_failed")

    def broken(_home, _registry, *_args, worker_owner):
        worker_owner(child)
        raise error

    monkeypatch.setattr(composition, restore, broken)
    with pytest.raises(PluginError) as caught:
        composition.create_app(settings=configuration(tmp_path), plugins=plugins())
    assert caught.value is error and child.closed == 1 and child.timeout == 1
    assert observed.stores[0].closed == 1


def test_independent_failed_startups_retain_every_owner_for_same_scope(tmp_path):
    class OwnedDescriptor:
        def __init__(self, name):
            self.descriptor = os.open(tmp_path / name, os.O_CREAT | os.O_RDWR, 0o600)
            self.blocked, self.attempts = True, 0

        def close(self):
            self.attempts += 1
            if self.blocked:
                raise OSError("Fictional startup release remains pending")
            os.close(self.descriptor)

    first, second, other = (OwnedDescriptor(name) for name in ("first", "second", "other"))

    def failed_owner(home, resource):
        owner = composition._StartupResources(home, "default", "sandbox")
        owner.own(resource)
        owner.close(RuntimeError("Discard this independent construction failure"))
        # Neither the construction exception nor this owner is returned. The
        # process registry must retain both independently failing constructors.

    try:
        failed_owner(tmp_path, first)
        failed_owner(tmp_path, second)
        failed_owner(tmp_path / "other-home", other)
        assert [owner.attempts for owner in (first, second, other)] == [1, 1, 1]
        assert not composition.retry_startup_cleanup(tmp_path, "default", "sandbox")
        assert [owner.attempts for owner in (first, second, other)] == [2, 2, 1]
        # Try every retained owner even when the oldest still refuses cleanup.
        second.blocked = False
        assert not composition.retry_startup_cleanup(tmp_path, "default", "sandbox")
        assert [owner.attempts for owner in (first, second, other)] == [3, 3, 1]
        with pytest.raises(OSError):
            os.fstat(second.descriptor)
        os.fstat(first.descriptor)
        first.blocked = False
        assert composition.retry_startup_cleanup(tmp_path, "default", "sandbox")
        with pytest.raises(OSError):
            os.fstat(first.descriptor)
        assert composition.retry_startup_cleanup(tmp_path, "default", "sandbox")
        assert [owner.attempts for owner in (first, second, other)] == [4, 3, 1]
        os.fstat(other.descriptor)
        other.blocked = False
        assert composition.retry_startup_cleanup(tmp_path / "other-home", "default", "sandbox")
        assert other.attempts == 2
        with pytest.raises(OSError):
            os.fstat(other.descriptor)
    finally:
        for resource in (first, second, other):
            resource.blocked = False
        composition.retry_startup_cleanup(tmp_path, "default", "sandbox")
        composition.retry_startup_cleanup(tmp_path / "other-home", "default", "sandbox")
