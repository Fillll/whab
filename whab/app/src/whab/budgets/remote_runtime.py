"""Host-owned live control of one retained remote authority binding.

Workers describe a protocol; they never receive configuration or issue requests.
A disabled decision pauses financial admission while preserving transport, local
revision and uncertain request identity. No control implicitly changes authority.
"""

import copy
import hmac
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from hashlib import sha256
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from whab.domain.errors import StoreError
from whab.plugins import Plugin, PluginContract, PluginError, RemoteLedgerClient, load_descriptors
from whab.plugins.effect_store import EffectError, encode
from whab.plugins.environments import ApprovedWheel, prepare_environment
from whab.plugins.live import RegistryGeneration
from whab.plugins.management import installed_entrypoints
from whab.plugins.remote_client_adapter import (
    client_record,
    configuration,
    read_client_record,
    supports_remote_client,
)
from whab.plugins.remote_selection import RemoteSelectionStore
from whab.plugins.remote_transport import RemoteStore
from whab.plugins.runtime import PluginRegistry
from whab.plugins.sdk import valid_plugin_name
from whab.plugins.worker_selection import _start, reopen_environment

from .runtime import RuntimeCandidate, RuntimeResource


def _error(code="remote_binding_changed", plugin_id=None):
    messages = {
        "remote_binding_changed": "The replacement does not match this exact remote authority and credential binding. Preserve the current client; code upgrades cannot choose another server or budget.",
        "remote_client_paused": "This remote client is paused. Re-enable the retained authority from local management before accessing its ledger.",
        "remote_selection_changed": "The remote client decision changed. Inspect its current status before retrying.",
        "remote_recovery_required": "The private remote client decision requires validated recovery. Preserve its files and retained versions.",
        "remote_cleanup_pending": "A previous remote descriptor worker still requires cleanup. Preserve its environment and retry cleanup before preparing another version.",
        "restart_required": "This legacy remote client requires a restart; install a compatible declarative client for live control.",
    }
    return PluginError(messages[code], code=code, plugin_id=plugin_id)



_CHILDREN = {}
_CHILDREN_LOCK = RLock()


def _track(home, worker):
    with _CHILDREN_LOCK:
        children = _CHILDREN.setdefault(str(Path(home).resolve()), [])
        if all(worker is not prior for prior in children):
            children.append(worker)


def _forget(home, worker):
    with _CHILDREN_LOCK:
        key = str(Path(home).resolve())
        children = _CHILDREN.get(key, [])
        if worker in children:
            children.remove(worker)
        if not children:
            _CHILDREN.pop(key, None)


def _retire(home, worker):
    _track(home, worker)
    try:
        worker.close()
    finally:
        if not worker.cleanup_pending:
            _forget(home, worker)
    if worker.cleanup_pending:
        raise _error("remote_cleanup_pending", worker.plugin_id)


def _reap(home):
    with _CHILDREN_LOCK:
        for worker in tuple(_CHILDREN.get(str(Path(home).resolve()), ())):
            _retire(home, worker)


def _prepare_worker(home, plugin, *, approvals=None, reference=None, worker_owner=None):
    """Own probe/startup handles from allocation, including failed cleanup."""
    _reap(home)
    def own(worker):
        _track(home, worker)
        if worker_owner is not None:
            worker_owner(worker)
    try:
        if approvals is not None:
            prepared = prepare_environment(Path(home) / "plugin-workers/environments", plugin.id, approvals, worker_owner=own)
        else:
            prepared = reopen_environment(Path(home) / "plugin-workers/environments", plugin.id, reference["environment"], worker_owner=own)
            if (prepared.distribution, prepared.version) != (reference["distribution"], reference["version"]):
                raise _error("remote_recovery_required", plugin.id)
        # Every readiness probe must already have retired before a usable
        # environment is returned. Never close an adopted active worker here.
        _reap(home)
        proxy, binding = _start(plugin, prepared, home, worker_owner=own)

        async def close():
            await run_in_threadpool(_retire, home, binding.worker)

        owned = replace(binding, resource=RuntimeResource(plugin.id, close))
        _forget(home, binding.worker)
        return proxy, owned
    except BaseException:
        try:
            _reap(home)
        except Exception:
            pass
        raise



def _approval_records(approvals, plugin_id):
    if (type(approvals) is not tuple or not 1 <= len(approvals) <= 128
            or any(type(wheel) is not ApprovedWheel for wheel in approvals)):
        raise _error("remote_recovery_required", plugin_id)
    records = sorted((wheel.path.name, wheel.sha256) for wheel in approvals)
    if (len({row[0] for row in records}) != len(records)
            or any(not name.endswith(".whl") or "\\" in name or not 1 <= len(name) <= 255 for name, _ in records)):
        raise _error("remote_recovery_required", plugin_id)
    return records


def _revision(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def remote_client_revision(home, budget_id, environment):
    """Read the stopped authority CAS without executing native or saved code."""
    with RemoteSelectionStore(home, budget_id, environment) as owner:
        return owner.revision(owner.snapshot())


def replace_saved_remote_worker(home, budget_id, environment, plugin_id, approvals, *, expected_revision,
                                config, binding_key):
    """Repair only a stopped same-binding client with approved current-host code.

    The caller supplies its locally unlocked profile/key; neither is persisted
    or passed to the worker. Exact durable retries acknowledge their receipt
    without opening an artifact or executing code, including after a later pause.
    """
    if not valid_plugin_name(plugin_id) or not _revision(expected_revision):
        raise _error("remote_selection_changed", plugin_id if valid_plugin_name(plugin_id) else None)
    records = _approval_records(approvals, plugin_id)
    binding = None
    try:
        with RemoteSelectionStore(home, budget_id, environment) as owner:
            identity = encode({"scope": owner.store.scope, "expected": expected_revision,
                               "plugin": plugin_id, "approvals": [list(row) for row in records]}, 32768)
            identifier = str(uuid5(NAMESPACE_URL, "whab-remote-repair-v1:" + sha256(identity).hexdigest()))
            current = owner.snapshot()
            previous = next((row for row in current["decisions"] if row["id"] == identifier), None)
            if previous is not None:
                return previous
            if owner.revision(current) != expected_revision or current["choice"]["plugin"] != plugin_id:
                raise _error("remote_selection_changed", plugin_id)
            proxy, binding = _prepare_worker(home, descriptor(current["choice"]), approvals=approvals)
            if (not supports_remote_client(proxy)
                    or binding_identity(proxy.storage, config, binding_key, budget_id, environment) != current["binding"]):
                raise _error(plugin_id=plugin_id)
            choice = {"plugin": plugin_id, "adapter": client_record(proxy.storage), "enabled": current["choice"]["enabled"],
                      "worker": {"environment": binding.environment.identifier,
                                 "distribution": binding.environment.distribution, "version": binding.environment.version}}
            _retire(home, binding.worker)
            binding = None
            return owner.publish({"id": identifier, "expected_revision": expected_revision, "choice": choice}, expected_revision)
    except (EffectError, PluginError):
        raise
    except Exception:
        raise _error("remote_recovery_required", plugin_id) from None
    finally:
        if binding is not None:
            _retire(home, binding.worker)


def binding_identity(adapter, config, key, budget_id, environment):
    # A scoped keyed digest is the only persisted binding evidence. Its inputs
    # remain host-local, including normalized endpoint and exact bearer value.
    values = configuration(adapter, config)
    raw = encode({"scope": [budget_id, environment], "binding": list(values)}, 32768)
    return hmac.new(key.encode("utf-8"), b"whab-remote-binding-v1\x00" + raw, sha256).hexdigest()


def descriptor(choice):
    return Plugin(choice["plugin"], "Retained remote client",
                  storage=read_client_record(choice["adapter"]),
                  contract=PluginContract(authority="remote", lifecycle="live"))


def saved_remote_record(home, budget_id, environment):
    directory = Path(home) / "remote-client"
    path = directory / "remote-client.json"
    if not path.exists() and not path.is_symlink():
        return None
    with RemoteSelectionStore(home, budget_id, environment) as owner:
        return owner.snapshot()


def prepare_remote_session(session):
    """Apply only a proved saved remote selection before cached-manifest checks."""
    from .storage_runtime import storage_manifest

    record = saved_remote_record(session.storage_home, session.id, session.environment)
    session.remote_authority = record
    if record is None:
        return False
    if session.storage_authority:
        raise _error("remote_recovery_required")
    selected = record["choice"]["plugin"]
    session.manifest = storage_manifest(session.manifest, selected)
    old = f"plugins/{record['origin_plugin']}/connection.json"
    new = f"plugins/{selected}/connection.json"
    if new not in session.files and old in session.files:
        session.files[new] = session.files[old]
    return session._incoming_content == record["original_source"]


@dataclass(frozen=True)
class RemoteGeneration:
    plugin_id: str
    adapter: RemoteLedgerClient
    enabled: bool
    binding: str
    worker: object = None

    @classmethod
    def from_record(cls, record):
        choice = record["choice"]
        return cls(choice["plugin"], read_client_record(choice["adapter"]), choice["enabled"], record["binding"],
                   MappingProxyType(dict(choice["worker"])) if choice["worker"] else None)

    def choice(self):
        return {"plugin": self.plugin_id, "adapter": client_record(self.adapter), "enabled": self.enabled,
                "worker": dict(self.worker) if self.worker else None}


@dataclass
class RemoteBootstrap:
    owner: RemoteSelectionStore
    plugin: Plugin
    bindings: dict
    resource: RuntimeResource

    @property
    def generation(self):
        return RemoteGeneration.from_record(self.owner.snapshot())

    def close(self):
        try:
            for binding in self.bindings.values():
                _retire(self.owner.store.root.parent, binding.worker)
        finally:
            self.owner.close()


def bootstrap_remote(settings, session, plugin, config, saved=None, *, worker_owner=None):
    """Validate the saved decision/current host before opening any remote ledger."""
    if not supports_remote_client(plugin):
        return None
    home = session.storage_home if session else settings.home
    budget_id = session.id if session else "default"
    try:
        identity = binding_identity(plugin.storage, config, settings.encryption_key, budget_id, settings.plaid_env)
    except StoreError:
        raise PluginError("Storage plugin could not start. Check its configuration and installation.",
                          code="storage_startup_failed", plugin_id=plugin.id) from None
    initial = {"format": 1, "binding": identity, "origin_plugin": plugin.id,
               "original_source": session._incoming_content if session else None,
               "choice": {"plugin": plugin.id, "adapter": client_record(plugin.storage), "enabled": True, "worker": None},
               "decisions": [], "pending": None, "acknowledged": None}
    owner = RemoteSelectionStore(home, budget_id, settings.plaid_env, None if saved is not None else initial)
    binding = None
    try:
        record = owner.snapshot()
        if saved is not None and record != saved or record["binding"] != identity:
            raise _error(plugin_id=plugin.id)
        if record["choice"]["plugin"] != plugin.id or record["choice"]["adapter"] != client_record(plugin.storage):
            raise _error(plugin_id=plugin.id)
        worker = record["choice"]["worker"]
        if worker is not None and record["choice"]["enabled"]:
            plugin, binding = _prepare_worker(home, plugin, reference=worker, worker_owner=worker_owner)
            if client_record(plugin.storage) != record["choice"]["adapter"]:
                raise _error("remote_recovery_required", plugin.id)
        return RemoteBootstrap(owner, plugin, {plugin.id: binding} if binding else {},
                               RuntimeResource(plugin.id, owner.close))
    except BaseException:
        try:
            if binding is not None:
                _retire(home, binding.worker)
        finally:
            owner.close()
        raise


class RemoteSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pluginId: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$", max_length=64)
    enabled: bool
    expectedRevision: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteFinishBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: str = Field(min_length=1, max_length=200)
    workspace: str
    payload: dict


class RemoteRecoveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authority: str = Field(pattern="^(active|candidate)$")


class RemoteClientLifecycle:
    def __init__(self, settings, session, tracked, runtime, bootstrap, config_for, *, selected=None):
        if type(tracked.store) is not RemoteStore:
            raise _error("restart_required")
        self.settings, self.session, self.tracked, self.runtime = settings, session, tracked, runtime
        self.owner, self.config_for = bootstrap.owner, config_for
        self.home = session.storage_home if session else settings.home
        self.selected = selected
        self.pending = None
        self.tasks = set()
        self.tracked.store.pending_journal = self.owner
        self.tracked.store.operation_scope = self.operation_scope
        self._verify(bootstrap.plugin)

    @contextmanager
    def operation_scope(self):
        with self.runtime.lease() as lease:
            generation = lease.service("remote_client")
            if not generation.enabled:
                raise _error("remote_client_paused", generation.plugin_id)
            yield

    def _verify(self, plugin):
        if not supports_remote_client(plugin):
            raise _error("restart_required", plugin.id)
        config = self.config_for(plugin)
        identity = binding_identity(plugin.storage, config, self.settings.encryption_key,
                                    self.runtime.budget_id, self.settings.plaid_env)
        if (identity != self.owner.snapshot()["binding"]
                or not self.tracked.store.matches_binding(*configuration(plugin.storage, config))):
            raise _error(plugin_id=plugin.id)

    def status(self):
        try:
            record = self.owner.snapshot()
            return {"supported": True, "plugin": record["choice"]["plugin"], "enabled": record["choice"]["enabled"],
                    "revision": self.owner.revision(record), "unknown_outcome": record["pending"] is not None,
                    "worker": copy.deepcopy(record["choice"]["worker"]), "receipt_lookup": False,
                    "automatic_reconciliation": False,
                    "runtime": self.runtime.status(),
                    "cleanup_pending": bool(_CHILDREN.get(str(Path(self.home).resolve()))),
                    "instruction": ("Reads remain available. Finish only the exact original request; do not discard, reconnect or refresh its revision."
                                    if record["pending"] else "The exact remote authority binding is retained.")}
        except EffectError:
            return {"supported": True, "recovery_required": True, "runtime": self.runtime.status(),
                    "instruction": "Preserve the private remote decision and recover its durable outcome before making changes."}

    def _receipt(self, identifier):
        return next((row for row in self.owner.snapshot()["decisions"] if row["id"] == identifier), None)

    async def _change(self, plugin_id, enabled, expected, approvals=None):
        if (not valid_plugin_name(plugin_id) or type(enabled) is not bool or type(expected) is not str
                or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected)):
            raise _error("remote_selection_changed", plugin_id if valid_plugin_name(plugin_id) else None)
        records = [] if approvals is None else _approval_records(approvals, plugin_id)
        identifier = str(uuid5(NAMESPACE_URL, sha256(encode({"scope": self.owner.store.scope,
            "expected": expected, "plugin": plugin_id, "enabled": enabled, "approvals": [list(row) for row in records]}, 32768)).hexdigest()))
        previous = self._receipt(identifier)
        if previous is not None:
            return self.runtime.status()["generation"]

        async def prepare(preparation):
            record = self.owner.snapshot()
            if self.owner.revision(record) != expected:
                raise _error("remote_selection_changed", plugin_id)
            active = preparation.active.services["plugins"]
            current_id = record["choice"]["plugin"]
            plugin = next((row for row in active.descriptors() if row.id == plugin_id), None)
            if plugin is None:
                plugin = load_descriptors((plugin_id,), installed_entrypoints())[0]
            if plugin_id != current_id or enabled or approvals is not None:
                self._verify(plugin)
            workers = dict(active.workers)
            previous_binding = workers.pop(current_id, None)
            new_binding = None
            reference = copy.deepcopy(record["choice"]["worker"]) if plugin_id == current_id else None
            if approvals is not None:
                plugin, new_binding = await run_in_threadpool(partial(_prepare_worker, self.home, plugin, approvals=approvals))
            elif enabled and plugin_id == current_id:
                if previous_binding is not None:
                    new_binding = previous_binding
                elif reference is not None:
                    plugin, new_binding = await run_in_threadpool(partial(_prepare_worker, self.home, plugin, reference=reference))
            if new_binding is not None:
                if new_binding is not previous_binding:
                    # Own a ready child before configuration validation can fail.
                    if enabled:
                        preparation.own(new_binding.resource)
                    else:
                        try:
                            self._verify(plugin)
                        finally:
                            await run_in_threadpool(_retire, self.home, new_binding.worker)
                self._verify(plugin)
                reference = {"environment": new_binding.environment.identifier,
                             "distribution": new_binding.environment.distribution, "version": new_binding.environment.version}
                if enabled:
                    workers[plugin_id] = new_binding
                else:
                    new_binding = None
            choice = {"plugin": plugin_id, "adapter": client_record(plugin.storage), "enabled": enabled, "worker": reference}
            updated = {**record, "choice": choice}
            # Candidate registry resolution occurs before any durable publication.
            registry = PluginRegistry(tuple(row for row in active.descriptors() if row.id != current_id) + (plugin,))
            generation = RegistryGeneration(registry, active.stable, workers, previous=active)
            resources = tuple(resource for resource in preparation.active.resources
                              if previous_binding is None or resource is not previous_binding.resource or new_binding is previous_binding)
            if new_binding is not None and new_binding is not previous_binding:
                resources += (new_binding.resource,)
            return RuntimeCandidate({**preparation.active.services, "plugins": generation,
                "remote_client": RemoteGeneration.from_record(updated)}, resources)

        async def finalize(_preparation, candidate):
            if self.owner.revision(self.owner.snapshot()) != expected:
                raise _error("remote_selection_changed", plugin_id)
            return candidate

        async def commit(candidate, _number):
            choice = candidate.services["remote_client"].choice()
            self.pending = candidate
            await run_in_threadpool(self.owner.publish, {"id": identifier, "expected_revision": expected, "choice": choice}, expected)
            await run_in_threadpool(self._selected, candidate)
            self.pending = None

        return await self.runtime.transition(prepare, finalize=finalize, commit=commit, plugin_id=plugin_id)

    def _selected(self, candidate):
        generation = candidate.services["remote_client"]
        if self.selected is not None:
            self.selected(generation.plugin_id, candidate.services["plugins"].registry)

    async def select(self, plugin_id, enabled, expected_revision):
        return await self._change(plugin_id, enabled, expected_revision)

    async def upgrade(self, plugin_id, approvals):
        record = self.owner.snapshot()
        if record["choice"]["plugin"] != plugin_id:
            raise _error("remote_selection_changed", plugin_id)
        return await self._change(plugin_id, record["choice"]["enabled"], self.owner.revision(record), approvals)

    async def recover(self, authority):
        if self.runtime._uncertain is None:
            # Financial outcome journal publication can be uncertain without a
            # selection transition. Reflush only the observed bounded record;
            # this never clears an unresolved remote command or resends it.
            record = self.owner.recover()
            active = self.runtime._active.services["remote_client"]
            if record["choice"] != active.choice() or record["binding"] != active.binding:
                raise _error("remote_recovery_required", active.plugin_id)
            self._verify(self.runtime._active.services["plugins"].storage_plugin(active.plugin_id))
            return self.runtime.status()["generation"]
        if self.pending is None:
            # A different family owns this uncertain transition. Matching our
            # unchanged remote declaration cannot prove its activation outcome.
            raise _error("remote_recovery_required")

        async def validate(_authority, active, candidate):
            record = self.owner.recover()
            chosen = active if _authority == "active" else candidate
            selected = chosen.services["remote_client"]
            if record["choice"] != selected.choice() or record["binding"] != selected.binding:
                return False
            plugin = chosen.services["plugins"].storage_plugin(selected.plugin_id)
            self._verify(plugin)
            binding = chosen.services["plugins"].workers.get(selected.plugin_id)
            if binding is not None and not binding.worker.healthy:
                return False
            await run_in_threadpool(self._selected, chosen)
            return True
        result = await self.runtime.recover(authority, validate)
        self.pending = None
        return result

    def finish(self, body):
        with self.operation_scope(), self.tracked.lock:
            ledger = self.runtime._active.services["ledger"]
            normalized = ledger.validate_command(body.operation, body.workspace, body.payload)
            result = self.tracked.store.finish_original(body.operation, body.workspace, normalized)
            ledger.invalidate_revision()
            return result

    def router(self):
        router = APIRouter(prefix="/api/storage/remote")

        def personal(request):
            if request.scope.get("whab.workspace") == "demo":
                raise HTTPException(403, "This operation requires your own budget")

        @router.get("/status")
        def status():
            return self.status()

        @router.post("/selection", status_code=202)
        async def selection(request: Request, body: RemoteSelectionBody):
            personal(request)
            async def change():
                return await self.select(body.pluginId, body.enabled, body.expectedRevision)
            task = self.runtime.schedule(change())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return {"accepted": True, "decision": self.status()}

        @router.post("/recover")
        async def recover(request: Request, body: RemoteRecoveryBody):
            personal(request)
            result = await self.recover(body.authority)
            return {"generation": result, "status": self.status()}

        @router.post("/finish")
        async def finish(request: Request, body: RemoteFinishBody):
            personal(request)
            try:
                return await run_in_threadpool(self.finish, body)
            except (PluginError, EffectError) as error:
                return JSONResponse({"error": str(error), "code": error.code}, status_code=409)

        return router
