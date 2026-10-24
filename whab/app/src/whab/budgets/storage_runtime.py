"""Application adapter for explicit same-device local storage transitions.

The stable TrackedStorage, LedgerService, provider services and namespaces retain
their identity. This module owns device-private instance locations and key-binding
proof; the authority journal continues to contain identities and hashes only.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from whab.domain.errors import StoreError
from whab.plugins import PluginError, StorageContext, load_descriptors
from whab.plugins.contracts import contract_for
from whab.plugins.live import RegistryGeneration
from whab.plugins.management import installed_entrypoints
from whab.plugins.runtime import PluginRegistry
from whab.plugins.sdk import API_VERSION, valid_plugin_name
from whab.plugins.storage import close_storage, open_storage
from whab.plugins.storage_transfer import (
    canonical_storage_json,
    decode_local_state,
    read_storage_json,
)

from .document import MAX_BUNDLE_BYTES, digest, validate_manifest
from .runtime import RuntimeCandidate, RuntimeTransitionError
from .storage_authority import (
    AuthorityJournal,
    NamespaceEvidence,
    StorageAuthorityCoordinator,
    _durable_directory,
    _fsync_directory,
    resolve_authority,
)

_LOCAL_CAPABILITIES = frozenset({"storage.authority", "storage.commands", "transfer.local"})
_COMMITTED = frozenset({"committed", "active", "retired"})


def _error(message="Storage authority metadata requires validated recovery."):
    return PluginError(message, code="storage_recovery_required")


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise _error() from None
    return value


def _hash(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _relative(value):
    if type(value) is not str or not 1 <= len(value) <= 512 or "\\" in value or "\x00" in value:
        raise _error()
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise _error()
    return value


def _write_metadata(path, value):
    raw = canonical_storage_json(value, limit=65536)
    _durable_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".storage-metadata-", dir=path.parent)
    try:
        try:
            stream = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def storage_manifest(manifest, plugin_id):
    """Change selection bookkeeping while retaining every plugin-owned record."""
    updated = copy.deepcopy(manifest)
    previous = updated["storage"]
    updated["storage"] = plugin_id
    for record in updated["plugins"]:
        if record["id"] == previous:
            record["enabled"] = False
        if record["id"] == plugin_id:
            record["enabled"] = True
    if not any(record["id"] == plugin_id for record in updated["plugins"]):
        record = {"id": plugin_id, "enabled": True, "apiVersion": API_VERSION, "settings": {}, "state": {}}
        if updated["formatVersion"] == 3:
            record["stateVersion"] = 1
        updated["plugins"].append(record)
    return validate_manifest(updated)


class StorageLocations:
    """Private host ownership map; contains no encryption key or financial data."""

    def __init__(self, base, budget_id, environment):
        self.base, self.budget_id, self.environment = Path(base), budget_id, environment
        self.directory = self.base / "storage-authority"
        self.path = self.directory / "instances.json"
        self.journal = AuthorityJournal(self.directory / "authority.json", budget_id, environment)
        self.lock = RLock()
        self.data = None
        if self.path.exists():
            try:
                with self.path.open("rb") as stream:
                    self.data = self._validate(read_storage_json(stream.read(65537), limit=65536))
            except (OSError, StoreError, ValueError, TypeError):
                raise _error() from None

    def _validate(self, value):
        if type(value) is not dict or value.keys() != {"formatVersion", "budget_id", "environment", "key_reference", "key_binding", "committed", "original", "instances"}:
            raise _error()
        if (type(value["formatVersion"]) is not int or value["formatVersion"] != 1
                or value["budget_id"] != self.budget_id or value["environment"] != self.environment
                or type(value["committed"]) is not bool or not _hash(value["key_binding"])):
            raise _error()
        _uuid(value["key_reference"])
        original = value["original"]
        if original is not None:
            if (type(original) is not dict or original.keys() != {"path", "digest"}
                    or type(original["path"]) is not str or not Path(original["path"]).is_absolute()
                    or not _hash(original["digest"])):
                raise _error()
        if type(value["instances"]) is not dict or not 1 <= len(value["instances"]) <= 100:
            raise _error()
        for instance_id, entry in value["instances"].items():
            _uuid(instance_id)
            if (type(entry) is not dict or entry.keys() != {"plugin_id", "home", "container", "payload_paths"}
                    or not valid_plugin_name(entry["plugin_id"])):
                raise _error()
            if entry["home"] != ".":
                _relative(entry["home"])
            if entry["container"] is not None:
                _relative(entry["container"])
            if type(entry["payload_paths"]) is not list or not 1 <= len(entry["payload_paths"]) <= 32:
                raise _error()
            for name in entry["payload_paths"]:
                _relative(name)
            if len(set(entry["payload_paths"])) != len(entry["payload_paths"]):
                raise _error()
        return copy.deepcopy(value)

    @staticmethod
    def key_binding(reference, key):
        if type(key) is not str:
            raise _error("The current runtime encryption-key reference cannot be verified.")
        return sha256(b"whab-runtime-key-reference-v1\x00" + reference.encode() + b"\x00" + key.encode()).hexdigest()

    def initialize(self, evidence, plugin_id, key, original_path=None, *, payload_paths):
        with self.lock:
            if self.data is None:
                reference = str(uuid4())
                original = None
                if original_path and Path(original_path).exists():
                    original = {"path": str(Path(original_path).resolve()), "digest": digest(Path(original_path).read_bytes())}
                self.data = {
                    "formatVersion": 1, "budget_id": self.budget_id, "environment": self.environment,
                    "key_reference": reference, "key_binding": self.key_binding(reference, key),
                    "committed": False, "original": original,
                    "instances": {evidence.instance_id: {"plugin_id": plugin_id, "home": ".", "container": None,
                                                         "payload_paths": list(payload_paths)}},
                }
                self.save()
            self.check_key(key)
            entry = self.data["instances"].get(evidence.instance_id)
            if entry is None or entry["plugin_id"] != plugin_id:
                raise _error()

    def check_key(self, key):
        if self.data is None or self.key_binding(self.data["key_reference"], key) != self.data["key_binding"]:
            raise _error("The current runtime encryption key does not match this budget's saved key reference.")

    def save(self):
        validated = self._validate(self.data)
        _write_metadata(self.path, validated)
        self.data = validated

    def home(self, entry):
        target = (self.base / entry["home"]).resolve()
        if not target.is_relative_to(self.base.resolve()):
            raise _error()
        return target

    def selected(self):
        record = self.journal.read()
        if record is None:
            if self.data and self.data["committed"]:
                raise _error()
            return None
        if self.data is None:
            raise _error()
        entry = self.data["instances"].get(record["authority_instance_id"])
        side = "destination" if record["stage"] in _COMMITTED else "source"
        if entry is None or entry["plugin_id"] != record[f"{side}_plugin_id"]:
            raise _error()
        return record, entry

    def register(self, evidence, plugin_id, relative_home, payload_paths):
        with self.lock:
            if self.data is None:
                raise _error()
            self.data["instances"][evidence.instance_id] = {
                "plugin_id": plugin_id, "home": relative_home,
                "container": f"{relative_home}/budget.whab",
                "payload_paths": list(payload_paths),
            }
            self.save()

    def freeze_original(self, session):
        with self.lock:
            if session and (session.status != "saved" or session.path is None or not session.path.is_file()
                            or any(not (session.storage_home / name).is_file()
                                   for name in ("source.json", "manifest.json", "plugin-data.whab"))):
                raise _error("Save or recover this budget's plugin state before migrating storage.")
            if not self.data["committed"] and session and session.path and session.path.exists():
                self.data["original"] = {"path": str(session.path.resolve()), "digest": digest(session.path.read_bytes())}
                self.save()


def saved_storage_selection(base, budget_id, environment):
    """Metadata-only startup selection, before manifest storage-name checks."""
    locations = StorageLocations(base, budget_id, environment)
    selected = locations.selected()
    return (locations, *selected) if selected else None


def prepare_session_authority(session):
    selected = saved_storage_selection(session.storage_home, session.id, session.environment)
    session.storage_authority = selected
    session.backend_home = session.storage_home
    if not selected:
        return False
    locations, record, entry = selected
    session.manifest = storage_manifest(session.manifest, entry["plugin_id"])
    session.backend_home = locations.home(entry)
    original = locations.data["original"]
    known_original = bool(original and session._file_digest == original["digest"])
    return known_original


def namespace_evidence(locations, settings, session):
    locations.check_key(settings.encryption_key)
    generated = {f"plugins/{entry['plugin_id']}/{name}" for entry in locations.data["instances"].values()
                 for name in entry["payload_paths"]}
    if session:
        with session.lock:
            manifest = validate_manifest(session.manifest)
            records = [{key: value for key, value in record.items() if key != "enabled"}
                       for record in manifest["plugins"] if record["settings"] or record["state"]]
            files = [{"name": name, "digest": digest(raw)} for name, raw in sorted(session.files.items())
                     if name not in generated]
        opaque = {"records": sorted(records, key=lambda record: record["id"]), "files": files}
    else:
        directory = settings.home / "data" / "plugin-state" / settings.plaid_env
        files = []
        if directory.exists():
            for path in sorted(directory.rglob("*")):
                if path.is_symlink():
                    raise _error("Preserve plugin state without symbolic links before migrating storage.")
                if path.is_file():
                    if path.stat().st_size > MAX_BUNDLE_BYTES:
                        raise _error("Plugin state exceeds supported local transfer limits.")
                    files.append({"name": str(path.relative_to(directory)), "digest": digest(path.read_bytes())})
        opaque = {"files": files}
    original = locations.data["original"]
    original_digest = None
    if original:
        try:
            original_digest = digest(Path(original["path"]).read_bytes())
        except OSError:
            raise _error("The preserved original budget container is unavailable.") from None
        if locations.data["committed"] and original_digest != original["digest"]:
            raise _error("The preserved original budget container changed outside this budget runtime.")
    # Plugin settings/state have the portable manifest contract, including finite
    # floats. Financial transfer JSON deliberately has a stricter integer codec.
    namespace_bytes = json.dumps(opaque, sort_keys=True, ensure_ascii=False,
                                 allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(namespace_bytes) > MAX_BUNDLE_BYTES:
        raise _error("Plugin state exceeds supported local transfer limits.")
    return NamespaceEvidence(sha256(namespace_bytes).hexdigest(),
                             original_digest, locations.data["key_reference"])


def open_saved_storage(settings, session, config_for):
    """Open and resolve only the journal's explicit native instances at startup."""
    base, budget_id = (session.storage_home, session.id) if session else (settings.home, "default")
    selected = saved_storage_selection(base, budget_id, settings.plaid_env)
    if not selected:
        return None
    locations, record, entry = selected
    locations.check_key(settings.encryption_key)
    descriptors = {plugin.id: plugin for plugin in load_descriptors(tuple(dict.fromkeys(
        (record["source_plugin_id"], record["destination_plugin_id"]))))}
    opened = {}
    try:
        for side in ("source", "destination"):
            instance_id = record[side]["instance_id"]
            location = locations.data["instances"].get(instance_id)
            if location is None or location["plugin_id"] != record[f"{side}_plugin_id"]:
                raise _error()
            plugin = descriptors[location["plugin_id"]]
            opened[side] = open_storage(plugin, StorageContext(
                locations.home(location), settings.plaid_env, config_for(plugin),
                session.context(plugin.id) if session else None,
            ))
        side = resolve_authority(locations.journal, opened["source"], opened["destination"],
                                 namespace_evidence(locations, settings, session))
        backend = opened[side]
        if side == "destination":
            locations.data["committed"] = True
            locations.save()
        if session:
            session.storage_authority = (locations, locations.journal.read(), locations.data["instances"][backend.commit_evidence().instance_id])
            session.manifest = storage_manifest(session.manifest, descriptors[record[f"{side}_plugin_id"]].id)
            destination = session.storage_authority[2]["container"]
            if destination:
                session.path = locations.base / destination
                session._file_digest = digest(session.path.read_bytes()) if session.path.exists() else None
        opened.pop(side)
        return backend
    finally:
        for backend in opened.values():
            close_storage(backend)


class DestinationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: str = Field(min_length=1, max_length=64)


class ExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preview_id: str = Field(min_length=36, max_length=36)


class RecoveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StorageRuntime:
    """Explicit preview/ticket controls; no financial payloads enter responses."""

    def __init__(self, settings, session, tracked, runtime, registry, config_for):
        self.settings, self.session, self.tracked = settings, session, tracked
        self.runtime, self.registry, self.config_for = runtime, registry, config_for
        base = session.storage_home if session else settings.home
        self.locations = StorageLocations(base, runtime.budget_id, settings.plaid_env)
        self.coordinator = StorageAuthorityCoordinator(runtime, self.locations.journal,
            lambda: namespace_evidence(self.locations, self.settings, self.session))
        self.previews, self.operations, self.tasks = {}, {}, set()
        self.app = self.manager = self.live = None

    def attach(self, app, manager, live):
        self.app, self.manager, self.live = app, manager, live

    def _supported(self, plugin):
        contract = contract_for(plugin)
        capabilities = {capability.name for capability in contract.provides if capability.version == 1}
        if (contract.authority != "local" or not _LOCAL_CAPABILITIES <= capabilities
                or plugin.bind is not None or not plugin.storage_payload_paths):
            raise PluginError("This storage plugin requires a restart or does not support complete same-device transfer.",
                              code="restart_required", plugin_id=plugin.id)

    def _source(self):
        identifier = self.tracked.storage_plugin_id
        if identifier is None:
            raise PluginError("Injected storage has no declared migration contract.", code="restart_required")
        plugin = self.registry.storage_plugin(identifier)
        self._supported(plugin)
        for provider in self.registry.bank_plugins():
            capabilities = contract_for(provider).provides
            if not any(cap.name == "lifecycle.background" and cap.version == 1 for cap in capabilities):
                raise PluginError("Disable or update this bank plugin before live storage migration; its background work cannot be drained.",
                                  code="restart_required", plugin_id=provider.id)
        return plugin

    def preview(self, destination):
        source = self._source()
        if not valid_plugin_name(destination) or destination == source.id:
            raise PluginError("Choose a different installed local storage plugin.", code="invalid_configuration")
        target = load_descriptors((destination,), installed_entrypoints())[0]
        self._supported(target)
        snapshot = self.tracked.export_local()
        protected = decode_local_state(snapshot.financial, snapshot.protected)
        if any(item.get("token") for state in protected.values() for item in state["items"].values()) and not self.settings.encryption_key:
            raise _error("Configure the existing runtime encryption key before transferring saved bank connections.")
        self.locations.initialize(snapshot.source, source.id, self.settings.encryption_key,
                                  self.session.path if self.session else None, payload_paths=source.storage_payload_paths)
        namespace_evidence(self.locations, self.settings, self.session)
        identifier = str(uuid4())
        relative_home = f"storage-instances/{identifier}"
        context = StorageContext(self.locations.base / relative_home, self.settings.plaid_env,
                                 self.config_for(target), self.session.context(target.id) if self.session else None,
                                 initial_role="staged")
        backend = open_storage(target, context)
        try:
            preview = backend.preview_local(snapshot)
            if not preview.empty or not preview.supported:
                raise PluginError("The destination cannot preserve this complete budget in an empty instance.",
                                  code="storage_transfer_unsupported", plugin_id=target.id)
            self.locations.register(backend.commit_evidence(), target.id, relative_home, target.storage_payload_paths)
        finally:
            close_storage(backend)
        self.previews[identifier] = {"source": source.id, "destination": target.id, "context": context,
                                     "plugin": target, "epoch": snapshot.source.authority_epoch,
                                     "instance": snapshot.source.instance_id, "used": False}
        while len(self.previews) > 32:
            self.previews.pop(next(iter(self.previews)))
        return {"preview_id": identifier, "source": source.id, "destination": target.id,
                "source_generation": snapshot.source.generation, "supported": True,
                "workspaces": {name: {key: len(state[key]) for key in ("accounts", "categories", "transactions", "receipts")}
                               for name, state in protected.items()}}

    def _transition_plan(self, preview_id):
        _uuid(preview_id)
        plan = self.previews.get(preview_id)
        if plan is None or plan["used"]:
            raise PluginError("Preview this storage destination again before executing migration.", code="invalid_configuration")
        if self.runtime.status()["health"] == "recovery_required":
            raise _error()
        self.locations.check_key(self.settings.encryption_key)
        if self._source().id != plan["source"]:
            raise PluginError("The active storage changed. Preview this destination again.", code="invalid_configuration")
        current = self.tracked.commit_evidence()
        if current.instance_id != plan["instance"] or current.authority_epoch != plan["epoch"]:
            raise PluginError("The storage authority changed. Preview this destination again.", code="invalid_configuration")
        return plan

    async def schedule(self, preview_id):
        plan = await asyncio.to_thread(self._transition_plan, preview_id)
        if plan["used"]:
            raise PluginError("This storage preview already belongs to a transition.", code="invalid_configuration")
        if any(operation["state"] in {"queued", "running"} for operation in self.operations.values()):
            raise PluginError("A storage migration is already pending.", code="transition_busy")
        plan["used"] = True
        self.operations[preview_id] = {"id": preview_id, "source": plan["source"], "destination": plan["destination"],
                                       "state": "queued", "error": None}
        task = self.runtime.schedule(self._execute(preview_id, plan))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return preview_id

    async def _execute(self, identifier, plan):
        operation = self.operations[identifier]
        operation["state"] = "running"
        target = plan["plugin"]
        def factory():
            return open_storage(target, plan["context"])
        def build(active, prepared):
            descriptors = tuple(target if plugin.id == plan["source"] else plugin for plugin in active.services["plugins"].descriptors())
            registry = RegistryGeneration(PluginRegistry(descriptors), stable=active.services["plugins"],
                                          workers=active.services["plugins"].workers)
            return RuntimeCandidate({**active.services, "storage": prepared, "plugins": registry}, active.resources)
        async def flush():
            await asyncio.to_thread(self.locations.freeze_original, self.session)
        async def activate(prepared, record):
            await asyncio.to_thread(self._activate, prepared, target.id)
        try:
            await self.coordinator.migrate(plan["source"], target.id, factory, factory, build,
                                            flush=flush, activate=activate)
            if self.session:
                await asyncio.to_thread(self.session.checkpoint)
            operation["state"] = "complete"
        except (StoreError, PluginError) as error:
            operation.update(state="recovery_required" if self.runtime.status()["health"] == "recovery_required" else "failed",
                             error={"code": getattr(error, "code", "storage_transition_failed"),
                                    "message": str(error), "plugin_id": target.id})
            raise

    def _activate(self, prepared, plugin_id):
        self.locations.check_key(self.settings.encryption_key)
        evidence = prepared.commit_evidence()
        entry = self.locations.data["instances"].get(evidence.instance_id)
        if entry is None or entry["plugin_id"] != plugin_id:
            raise _error()
        self.locations.data["committed"] = True
        self.locations.save()
        if self.session:
            with self.session.lock:
                self.session.manifest = storage_manifest(self.session.manifest, plugin_id)
                self.session.storage_payload_paths = tuple(entry["payload_paths"])
                self.session.backend_home = self.locations.home(entry)
                self.session.path = self.locations.base / entry["container"]
                self.session._file_digest = digest(self.session.path.read_bytes()) if self.session.path.exists() else None
        previous_storage = self.tracked.storage_plugin_id
        self.tracked.retarget(prepared, self.runtime, plugin_id)
        self.settings = replace(self.settings, storage=plugin_id,
                                plugins=tuple(name for name in self.settings.plugins if name != previous_storage))
        if self.manager:
            previous = self.manager.settings.storage
            self.manager.settings = replace(self.manager.settings, storage=plugin_id)
            self.manager.required = tuple(plugin_id if name == previous else name for name in self.manager.required)
            self.live.required = frozenset(self.manager.required)
            self.live.nonlive = frozenset((set(self.live.nonlive) - {previous}) | {plugin_id})
        if self.app:
            self.app.state.storage_plugin = plugin_id
            self.app.state.store = prepared
        replicas = getattr(self, "replicas", None)
        if replicas:
            replicas.supported_primary_ids.add(plugin_id)

    async def recover(self):
        self.locations.check_key(self.settings.encryption_key)
        generation = await self.coordinator.recover()
        for operation in self.operations.values():
            if operation["state"] == "recovery_required":
                operation["state"] = "recovered"
        if self.session:
            await asyncio.to_thread(self.session.checkpoint)
        replicas = getattr(self, "replicas", None)
        if replicas:
            await asyncio.to_thread(replicas.authority_recovered)
        return generation

    def status(self):
        return {"runtime": self.runtime.status(), "storage": self.tracked.storage_plugin_id,
                "authority": self.coordinator.status(), "operations": [copy.deepcopy(value) for value in self.operations.values()]}

    async def close(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    def router(self):
        router = APIRouter()
        def personal(request):
            if request.scope.get("whab.workspace") == "demo" or request.query_params.get("workspace") == "demo":
                raise HTTPException(403, "Select the personal budget before changing storage authority.")
        @router.get("/api/storage/status")
        def status():
            return self.status()
        @router.post("/api/storage/preview")
        def preview(body: DestinationBody, request: Request):
            personal(request)
            return self.preview(body.destination)
        @router.post("/api/storage/transition", status_code=202)
        async def transition(body: ExecuteBody, request: Request):
            personal(request)
            return {"id": await self.schedule(body.preview_id), "state": "queued"}
        @router.post("/api/storage/recover")
        async def recover(body: RecoveryBody, request: Request):
            personal(request)
            try:
                return {"generation": await self.recover(), "state": "recovered"}
            except RuntimeTransitionError as error:
                raise HTTPException(409, str(error)) from None
        return router
