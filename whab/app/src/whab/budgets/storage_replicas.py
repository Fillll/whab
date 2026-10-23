"""Same-device copies, immutable plugin namespaces and explicit promotion."""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import os
import tempfile
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from whab.domain.errors import StoreError
from whab.plugins import PluginError, StorageContext, load_descriptors
from whab.plugins.contracts import contract_for
from whab.plugins.live import RegistryGeneration
from whab.plugins.management import installed_entrypoints
from whab.plugins.runtime import PluginRegistry
from whab.plugins.storage import close_storage, open_storage
from whab.plugins.storage_replica import replica_record
from whab.plugins.storage_transfer import CommitEvidence, read_storage_json

from .container import MAX_CONTAINER_FILES
from .document import MAX_BUNDLE_BYTES, MAX_MANIFEST_BYTES, digest, validate_manifest
from .runtime import RuntimeCandidate
from .session import _read_bytes
from .storage_authority import AuthorityJournal, _durable_directory, _fsync_directory
from .storage_runtime import (
    DestinationBody,
    ExecuteBody,
    RecoveryBody,
    _hash,
    _uuid,
    _write_metadata,
)


def _failure(message="This storage copy needs validated recovery before it can be used."):
    return PluginError(message, code="storage_copy_failed")


def _bounded_json(value, limit):
    try:
        with io.BytesIO() as output:
            for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).iterencode(value):
                raw = chunk.encode("utf-8")
                if output.tell() + len(raw) > limit:
                    raise _failure("Opaque plugin state exceeds the supported storage copy size.")
                output.write(raw)
            return output.getvalue()
    except (UnicodeError, TypeError, ValueError):
        raise _failure("Plugin namespaces contain invalid portable data.") from None


class NamespaceArchive:
    """One durable namespace counter and immutable private artifacts per budget.

    The counter is separate from financial generation: a model/settings save can
    change an opaque namespace without changing native financial storage.
    """

    def __init__(self, storage):
        self.storage = storage
        self.directory = storage.locations.directory / "namespaces"
        self.path = storage.locations.directory / "namespace-index.json"
        self.lock = RLock()

    def _read(self):
        if not self.path.exists():
            return None
        try:
            value = read_storage_json(_read_bytes(self.path, 65536), limit=65536)
            if (type(value) is not dict or value.keys() != {"formatVersion", "budget_id", "environment", "generation", "digest"}
                    or type(value["formatVersion"]) is not int or value["formatVersion"] != 1
                    or value["budget_id"] != self.storage.runtime.budget_id or value["environment"] != self.storage.settings.plaid_env
                    or type(value["generation"]) is not int or not 0 <= value["generation"] <= 9007199254740991
                    or not _hash(value["digest"])):
                raise ValueError
            return value
        except (OSError, StoreError, ValueError):
            raise _failure("The storage copy namespace index is invalid.") from None

    def _payload(self):
        storage = self.storage
        generated = {f"plugins/{entry['plugin_id']}/{name}" for entry in storage.locations.data["instances"].values()
                     for name in entry["payload_paths"]}
        files = {}
        budget = MAX_BUNDLE_BYTES - MAX_MANIFEST_BYTES - 512
        count = 0
        def reserve(name):
            nonlocal budget, count
            if type(name) is not str or len(name) > 1024:
                raise _failure("A plugin namespace file name exceeds the storage copy limit.")
            count += 1
            budget -= len(_bounded_json(name, 8192)) + 32
            if count > MAX_CONTAINER_FILES or budget <= 0:
                raise _failure("Plugin namespaces exceed the storage copy file count or size limit.")
            return budget * 3 // 4
        def add(name, raw):
            nonlocal budget
            if type(raw) is not bytes or 4 * ((len(raw) + 2) // 3) > budget:
                raise _failure("Opaque plugin state exceeds the supported storage copy size.")
            budget -= 4 * ((len(raw) + 2) // 3)
            files[name] = base64.b64encode(raw).decode("ascii")
        if storage.session:
            manifest_bytes = _bounded_json(storage.session.manifest, MAX_MANIFEST_BYTES)
            manifest = validate_manifest(json.loads(manifest_bytes))
            records = {"manifest": manifest}
            for visited, (name, raw) in enumerate(storage.session.files.items(), 1):
                if visited > MAX_CONTAINER_FILES:
                    raise _failure("Plugin namespaces exceed the storage copy file count limit.")
                if name not in generated:
                    reserve(name)
                    add(name, raw)
        else:
            directory = storage.settings.home / "data" / "plugin-state" / storage.settings.plaid_env
            if directory.exists():
                nodes = 0
                for path in directory.rglob("*"):
                    nodes += 1
                    if nodes > MAX_CONTAINER_FILES * 4:
                        raise _failure("Plugin namespaces contain too many filesystem entries for storage copies.")
                    if path.is_symlink():
                        raise _failure("Storage copies require plugin files without symbolic links.")
                    if path.is_file():
                        name = f"plugin-state/{path.relative_to(directory)}"
                        limit = reserve(name)
                        add(name, _read_bytes(path, limit))
            selection = storage.settings.home / "plugins.json"
            if selection.exists():
                name = "host/plugin-selection.json"
                limit = reserve(name)
                add(name, _read_bytes(selection, limit))
            records = {"selection": list(storage.manager.settings.plugins if storage.manager else storage.settings.plugins)}
            _bounded_json(records, MAX_MANIFEST_BYTES)
        value = {"formatVersion": 1, "budget_id": storage.runtime.budget_id, "environment": storage.settings.plaid_env,
                 **records, "files": files}
        return _bounded_json(value, MAX_BUNDLE_BYTES)

    def artifact(self, generation, fingerprint):
        if type(generation) is not int or generation < 0 or not _hash(fingerprint):
            raise _failure()
        return self.directory / f"{generation}-{fingerprint}.namespace"

    def validate(self, generation, fingerprint):
        try:
            if digest(_read_bytes(self.artifact(generation, fingerprint))) != fingerprint:
                raise ValueError
        except (OSError, StoreError, ValueError):
            raise _failure("The immutable storage copy namespace artifact is missing or changed.") from None

    def _stage(self, generation, fingerprint, raw):
        target = self.artifact(generation, fingerprint)
        _durable_directory(self.directory)
        if target.exists():
            self.validate(generation, fingerprint)
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".namespace-", dir=self.directory)
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
            try:
                os.link(temporary, target)
            except FileExistsError:
                self.validate(generation, fingerprint)
            _fsync_directory(self.directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def reconcile(self):
        """Called under the tracked storage/session lock for a coherent capture."""
        with self.lock:
            previous = self._read()
            raw = self._payload()
            fingerprint = digest(raw)
            generation = previous["generation"] if previous and previous["digest"] == fingerprint else (previous["generation"] + 1 if previous else 0)
            self._stage(generation, fingerprint, raw)
            if previous is None or (previous["generation"], previous["digest"]) != (generation, fingerprint):
                _write_metadata(self.path, {"formatVersion": 1, "budget_id": self.storage.runtime.budget_id,
                                          "environment": self.storage.settings.plaid_env,
                                          "generation": generation, "digest": fingerprint})
            self.validate(generation, fingerprint)
            return generation, fingerprint


class StorageReplicas:
    """Owned copy tasks; a primary save never awaits or fails because of a copy."""

    def __init__(self, storage):
        self.storage = storage
        self.path = storage.locations.directory / "replicas.json"
        self.archive = NamespaceArchive(storage)
        self.entries, self.previews, self.operations, self.errors = {}, {}, {}, {}
        self.watermarks = {}
        self._native_lock = RLock()
        self._control_lock = RLock()
        self._owner = AuthorityJournal(storage.locations.directory / "replica-control.json",
                                       storage.runtime.budget_id, storage.settings.plaid_env)
        self._owner_descriptor = None
        self.tasks = set()
        self._serial = asyncio.Lock()
        self._loop = None
        self._poller = None
        self._stop = asyncio.Event()
        self._scheduled = False
        self._notifications = self._observed = 0
        self._closed = False
        self.supported_primary_ids = set()
        identifier = storage.tracked.storage_plugin_id
        if identifier is not None:
            try:
                storage._supported(storage.registry.storage_plugin(identifier))
                self.supported_primary_ids.add(identifier)
            except (PluginError, AttributeError):
                pass
        self._load()
        storage.replicas = self
        storage.tracked.after_commit = self.nudge
        if storage.session:
            storage.session.after_checkpoint = self.nudge

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = read_storage_json(_read_bytes(self.path, 65536), limit=65536)
            if (type(data) is not dict or data.keys() != {"formatVersion", "budget_id", "environment", "copies"}
                    or type(data["formatVersion"]) is not int or data["formatVersion"] != 1
                    or data["budget_id"] != self.storage.runtime.budget_id or data["environment"] != self.storage.settings.plaid_env
                    or type(data["copies"]) is not list or len(data["copies"]) > 8):
                raise ValueError
            for entry in data["copies"]:
                if (type(entry) is not dict or entry.keys() != {"id", "plugin_id", "instance_id", "source_instance_id", "source_epoch", "state"}
                        or entry["state"] not in {"active", "promoting", "promoted", "disabled"}):
                    raise ValueError
                for name in ("id", "instance_id", "source_instance_id", "source_epoch"):
                    _uuid(entry[name])
                location = self.storage.locations.data["instances"].get(entry["instance_id"])
                if entry["id"] in self.entries or location is None or location["plugin_id"] != entry["plugin_id"]:
                    raise ValueError
                self.entries[entry["id"]] = entry
        except (OSError, StoreError, TypeError, KeyError, ValueError):
            # Copy configuration cannot invalidate an acknowledged primary save.
            self.entries.clear()
            self.errors["configuration"] = {"code": "invalid_copy_configuration"}

    def _save(self):
        with self._control_lock:
            self._ensure_writer()
            _write_metadata(self.path, {"formatVersion": 1, "budget_id": self.storage.runtime.budget_id,
                                      "environment": self.storage.settings.plaid_env,
                                      "copies": list(self.entries.values())})

    def _ensure_writer(self):
        with self._control_lock:
            try:
                if self._owner_descriptor is None:
                    self._owner_descriptor = self._owner.acquire_writer()
                self._owner._check_writer()
            except StoreError:
                raise _failure("Another copy coordinator owns this budget, or its ownership lock changed.") from None

    def _plugin(self, identifier):
        plugin = self.storage.descriptor_for(load_descriptors((identifier,), installed_entrypoints())[0])
        self.storage._supported(plugin)
        if not any(cap.name == "storage.replica" and cap.version == 1 for cap in contract_for(plugin).provides):
            raise _failure("This storage plugin does not support native synchronized copies.")
        return plugin

    def _factory(self, entry):
        plugin = self._plugin(entry["plugin_id"])
        location = self.storage.locations.data["instances"][entry["instance_id"]]
        return open_storage(plugin, StorageContext(self.storage.locations.home(location), self.storage.settings.plaid_env,
            self.storage.config_for(plugin), self.storage.session.context(plugin.id) if self.storage.session else None,
            initial_role="copy", expected_instance_id=entry["instance_id"]))

    def _entry(self, identifier):
        _uuid(identifier)
        entry = self.entries.get(identifier)
        if entry is None or entry["state"] not in {"active", "promoting"}:
            raise _failure("Select an active storage copy.")
        return entry

    def disable(self, identifier):
        entry = self._entry(identifier)
        if entry["state"] != "active" or any(operation["copy_id"] == identifier and operation["state"] in {"queued", "running"}
                                                for operation in tuple(self.operations.values())):
            raise _failure("Finish or recover this promotion before disabling its copy attachment.")
        entry["state"] = "disabled"
        self._save()
        return {"id": identifier, "state": "disabled"}

    def _capture(self):
        with self.storage.tracked.lock:
            self._ensure_writer()
            self.storage.locations.check_key(self.storage.settings.encryption_key)
            if self.entries and not self.archive.path.is_file():
                raise _failure("Restore the namespace index before retrying this storage copy.")
            snapshot = self.storage.tracked.export_local()
            generation, fingerprint = self.archive.reconcile()
            return snapshot, generation, fingerprint

    def preview(self, destination):
        if "configuration" in self.errors:
            raise _failure("Repair the saved copy configuration before adding another copy.")
        source = self.storage._source()
        self._ensure_writer()
        if destination == source.id or len(self.entries) >= 8:
            raise _failure("Choose another local backend (at most eight retained copy attachments).")
        target = self._plugin(destination)
        snapshot = self.storage.tracked.export_local()
        # The migration preview performs the same encrypted-connection key guard.
        from whab.plugins.storage_transfer import decode_local_state
        states = decode_local_state(snapshot.financial, snapshot.protected)
        if any(row.get("token") for state in states.values() for row in state["items"].values()) and not self.storage.settings.encryption_key:
            raise _failure("Configure the existing runtime encryption key before copying saved bank connections.")
        self.storage.locations.initialize(snapshot.source, source.id, self.storage.settings.encryption_key,
            self.storage.session.path if self.storage.session else None, payload_paths=source.storage_payload_paths)
        identifier = str(uuid4())
        relative = f"storage-instances/{identifier}"
        context = StorageContext(self.storage.locations.base / relative, self.storage.settings.plaid_env,
            self.storage.config_for(target), self.storage.session.context(target.id) if self.storage.session else None,
            initial_role="copy")
        backend = open_storage(target, context)
        try:
            preview = backend.preview_local(snapshot)
            if not preview.empty or not preview.supported:
                raise _failure("A synchronized copy requires a supported empty native destination.")
            evidence = backend.commit_evidence()
            self.storage.locations.register(evidence, target.id, relative, target.storage_payload_paths)
        finally:
            close_storage(backend)
        with self.storage.tracked.lock:
            self.archive.reconcile()
        self.previews[identifier] = {"id": identifier, "plugin_id": target.id, "instance_id": evidence.instance_id,
                                    "source_instance_id": snapshot.source.instance_id,
                                    "source_epoch": snapshot.source.authority_epoch, "state": "active"}
        while len(self.previews) > 32:
            self.previews.pop(next(iter(self.previews)))
        return {"preview_id": identifier, "destination": target.id, "source_generation": snapshot.source.generation, "supported": True}

    def arm(self, preview_id):
        with self._control_lock:
            return self._arm(preview_id)

    def _arm(self, preview_id):
        _uuid(preview_id)
        plan = self.previews.get(preview_id)
        if plan is None or len(self.entries) >= 8:
            raise _failure("Preview this copy destination before attaching it.")
        current = self.storage.tracked.commit_evidence()
        if (current.instance_id, current.authority_epoch) != (plan["source_instance_id"], plan["source_epoch"]):
            raise _failure("The primary authority changed; preview another copy destination.")
        self.entries[preview_id] = copy.deepcopy(plan)
        try:
            self._save()
        except BaseException:
            self.entries.pop(preview_id, None)
            raise
        self.previews.pop(preview_id)
        self.nudge()
        return {"id": preview_id, "state": "pending"}

    def nudge(self):
        # Called only after the native primary mutation has already committed.
        # Scheduling is deliberately best effort; startup compares durable state.
        try:
            self._notifications += 1
            if self._loop and not self._closed:
                self._loop.call_soon_threadsafe(self._queue)
        except Exception:
            self.errors["scheduler"] = {"code": "copy_scheduling_failed"}

    def _queue(self, *, force=False):
        if (self._closed or self._scheduled or (not force and self._observed == self._notifications)
                or not any(entry["state"] == "active" and self.errors.get(entry["id"], {}).get("code") != "copy_source_changed"
                           for entry in tuple(self.entries.values()))):
            return
        try:
            self._scheduled = True
            task = self.storage.runtime.schedule(self._synchronize())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        except Exception:
            self._scheduled = False
            self.errors["scheduler"] = {"code": "copy_scheduling_failed"}

    def _apply(self, entry, backend, capture):
        snapshot, namespace_generation, namespace_digest = capture
        if (entry["source_instance_id"], entry["source_epoch"]) != (snapshot.source.instance_id, snapshot.source.authority_epoch):
            raise PluginError("The primary authority changed; attach a new copy explicitly.", code="copy_source_changed")
        self.archive.validate(namespace_generation, namespace_digest)
        evidence = backend.commit_evidence()
        if evidence.instance_id != entry["instance_id"] or evidence.role != "copy":
            raise _failure()
        return backend.apply_replica(entry["id"], snapshot, namespace_digest, evidence,
                                     namespace_generation=namespace_generation)

    def _copy_one(self, entry, capture):
        # Open/read/write/close share one host lock, so status never competes
        # for a native writer lock with an in-progress copy replacement.
        with self._native_lock:
            backend = self._factory(entry)
            try:
                self.watermarks[entry["id"]] = self._apply(entry, backend, capture)
            finally:
                close_storage(backend)

    async def _synchronize(self):
        observed = self._notifications
        try:
            async with self._serial:
                if self.storage.runtime.status()["health"] == "recovery_required":
                    return
                async with self.storage.runtime.background():
                    capture = await asyncio.to_thread(self._capture)
                    self.errors.pop("capture", None)
                    for entry in tuple(self.entries.values()):
                        if entry["state"] != "active":
                            continue
                        try:
                            await asyncio.to_thread(self._copy_one, entry, capture)
                            self.errors.pop(entry["id"], None)
                        except Exception as error:
                            code = "copy_source_changed" if isinstance(error, PluginError) and error.code == "copy_source_changed" else "copy_apply_failed"
                            self.errors[entry["id"]] = {"code": code, "plugin_id": entry["plugin_id"]}
        except Exception:
            self.errors["capture"] = {"code": "copy_capture_failed"}
        finally:
            self._observed = max(self._observed, observed)
            self._scheduled = False
            if self._observed < self._notifications:
                self._queue()

    def _namespace_changed(self):
        with self.storage.tracked.lock:
            index = self.archive._read()
            return index is None or digest(self.archive._payload()) != index["digest"]

    async def _poll(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1)
            except TimeoutError:
                if not any(entry["state"] == "active" for entry in tuple(self.entries.values())):
                    continue
                try:
                    changed = await asyncio.to_thread(self._namespace_changed)
                except Exception:
                    changed = True
                self._queue(force=changed or bool(self.errors))

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self.authority_recovered(queue=False)
        if any(entry["state"] == "active" for entry in self.entries.values()):
            await self._synchronize()
        self._poller = asyncio.create_task(self._poll())

    def authority_recovered(self, *, queue=True):
        current = self.storage.tracked.commit_evidence() if self.entries else None
        changed = False
        for entry in self.entries.values():
            if current.instance_id == entry["instance_id"] and entry["state"] != "promoted":
                entry["state"], changed = "promoted", True
            elif entry["state"] == "promoting":
                entry["state"], changed = "active", True
        if changed:
            try:
                self._save()
            except Exception:
                self.errors["configuration"] = {"code": "copy_state_update_failed"}
        for operation in self.operations.values():
            if operation["state"] == "recovery_required":
                operation["state"] = "recovered"
        if queue:
            self.nudge()

    async def close(self):
        self._closed = True
        self._stop.set()
        if self._poller:
            await self._poller
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self._owner_descriptor is not None:
            self._owner.release_writer(self._owner_descriptor)
            self._owner_descriptor = None

    def status(self):
        runtime = self.storage.runtime.status()
        primary = {"supported": False, "plugin_id": self.storage.tracked.storage_plugin_id,
                   "health": runtime["health"], "admission": runtime["admission"],
                   "role": None, "authoritative": False}
        # This control endpoint remains available while admission is gated. Read
        # retained capability metadata without taking another request lease.
        if primary["plugin_id"] not in self.supported_primary_ids:
            return {"primary": primary, "copies": [], "errors": {"unsupported": {"code": "copy_unsupported"}},
                    "operations": copy.deepcopy(list(self.operations.values()))}
        primary["supported"] = True
        try:
            current = self.storage.tracked.commit_evidence()
            if (type(current) is not CommitEvidence or current.budget_id != self.storage.runtime.budget_id
                    or current.environment != self.storage.settings.plaid_env):
                raise _failure()
        except Exception:
            primary["error"] = {"code": "native_commit_evidence_unavailable"}
            return {"primary": primary, "copies": [{"id": entry["id"], "destination": entry["plugin_id"],
                        "state": "unavailable", "watermark": None} for entry in tuple(self.entries.values())],
                    "errors": copy.deepcopy(self.errors), "operations": copy.deepcopy(list(self.operations.values()))}
        primary.update(instance_id=current.instance_id, generation=current.generation,
                       authority_epoch=current.authority_epoch, role=current.role,
                       authoritative=current.role == "primary" and runtime["health"] != "recovery_required" and runtime["admission"] == "open")
        index = None
        namespace_current = False
        try:
            index = self.archive._read()
            if index:
                with self.storage.tracked.lock:
                    namespace_current = digest(self.archive._payload()) == index["digest"]
        except Exception:
            self.errors["namespace"] = {"code": "invalid_namespace_index"}
        rows = []
        for entry in self.entries.values():
            row = {"id": entry["id"], "destination": entry["plugin_id"], "state": entry["state"],
                   "watermark": None, "error": self.errors.get(entry["id"])}
            if entry["state"] == "active":
                backend = None
                try:
                    with self._native_lock:
                        backend = self._factory(entry)
                        try:
                            watermark = backend.replica_watermark()
                        finally:
                            close_storage(backend)
                            backend = None
                    self.watermarks[entry["id"]] = watermark
                    if watermark:
                        self.archive.validate(watermark.namespace_generation, watermark.namespace_digest)
                    row["watermark"] = replica_record(watermark) if watermark else None
                    row["state"] = "saved" if (watermark and index and namespace_current
                        and (watermark.source_instance_id, watermark.source_authority_epoch, watermark.source_generation,
                             watermark.namespace_generation, watermark.namespace_digest)
                        == (current.instance_id, current.authority_epoch, current.generation, index["generation"], index["digest"])) else "pending"
                    if row["error"]:
                        row["state"] = "failed"
                    if (entry["source_instance_id"], entry["source_epoch"]) != (current.instance_id, current.authority_epoch):
                        row["state"] = "reattach_required"
                except Exception:
                    row.update(state="failed", error={"code": "copy_status_unavailable"})
                finally:
                    if backend:
                        close_storage(backend)
            elif entry["id"] in self.watermarks:
                row["watermark"] = replica_record(self.watermarks[entry["id"]])
            rows.append(row)
        return {"primary": primary, "copies": rows,
                "errors": copy.deepcopy(self.errors), "operations": copy.deepcopy(list(self.operations.values()))}

    async def promote(self, identifier):
        with self._control_lock:
            entry = self._entry(identifier)
            if entry["state"] != "active" or any(row["state"] in {"queued", "running"} for row in tuple(self.operations.values())):
                raise _failure("A storage promotion is already pending.")
            operation_id = str(uuid4())
            self.operations[operation_id] = {"id": operation_id, "copy_id": identifier, "state": "queued", "error": None}
            task = self.storage.runtime.schedule(self._promote(operation_id, entry))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return {"id": operation_id, "state": "queued"}

    async def _promote(self, identifier, entry):
        operation = self.operations[identifier]
        operation["state"] = "running"
        try:
            target = self._plugin(entry["plugin_id"])
            source_id = self.storage._source().id
        except Exception:
            operation.update(state="failed", error={"code": "promotion_preparation_failed"})
            return
        async with self._serial:
            def build(active, prepared):
                upgrades = getattr(self.storage.app.state, "storage_upgrade", None) if self.storage.app else None
                if upgrades is not None:
                    return upgrades.build_generation(active, prepared, target, source_id)
                descriptors = tuple(target if plugin.id == source_id else plugin for plugin in active.services["plugins"].descriptors())
                registry = RegistryGeneration(PluginRegistry(descriptors), stable=active.services["plugins"],
                                              workers=active.services["plugins"].workers)
                return RuntimeCandidate({**active.services, "storage": prepared, "plugins": registry}, active.resources)
            async def catch_up(source, destination):
                capture = await asyncio.to_thread(self._capture)
                return await asyncio.to_thread(self._apply, entry, destination, capture)
            async def flush():
                await asyncio.to_thread(self.storage.locations.freeze_original, self.storage.session)
            async def activate(prepared, record):
                await asyncio.to_thread(self.storage._activate, prepared, target.id)
                entry["state"] = "promoted"
                await asyncio.to_thread(self._save)
            try:
                entry["state"] = "promoting"
                await asyncio.to_thread(self._save)
                await self.storage.coordinator.promote(source_id, target.id, lambda: self._factory(entry),
                    lambda: self._factory(entry), build, catch_up=catch_up, flush=flush, activate=activate)
                if self.storage.session:
                    await asyncio.to_thread(self.storage.session.checkpoint)
                operation["state"] = "complete"
            except Exception:
                recovery = self.storage.runtime.status()["health"] == "recovery_required"
                operation.update(state="recovery_required" if recovery else "failed", error={"code": "promotion_failed"})
                if not recovery:
                    entry["state"] = "active"
                    await asyncio.to_thread(self._save)

    def router(self):
        router = APIRouter()
        def personal(request):
            if request.scope.get("whab.workspace") == "demo" or request.query_params.get("workspace") == "demo":
                raise HTTPException(403, "Select the personal budget before changing storage copies.")
        @router.get("/api/storage/copies")
        def status():
            return self.status()
        @router.post("/api/storage/copies/preview")
        def preview(body: DestinationBody, request: Request):
            personal(request)
            return self.preview(body.destination)
        @router.post("/api/storage/copies/transition", status_code=202)
        def arm(body: ExecuteBody, request: Request):
            personal(request)
            return self.arm(body.preview_id)
        @router.post("/api/storage/copies/{identifier}/retry", status_code=202)
        def retry(identifier: str, body: RecoveryBody, request: Request):
            personal(request)
            self._entry(identifier)
            self.nudge()
            return {"id": identifier, "state": "pending"}
        @router.post("/api/storage/copies/{identifier}/disable")
        def disable(identifier: str, body: RecoveryBody, request: Request):
            personal(request)
            return self.disable(identifier)
        @router.post("/api/storage/copies/{identifier}/promote", status_code=202)
        async def promote(identifier: str, body: RecoveryBody, request: Request):
            personal(request)
            return await self.promote(identifier)
        return router
