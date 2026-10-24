"""Compatible local storage code handover, distinct from backend authority moves.

The native instance, authority epoch and financial generation do not change.
The approved candidate code decision precedes its first writable constructor;
an uncertain decision never falls back to an earlier backend implementation.
"""

import hashlib
import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path
from threading import RLock
from time import monotonic

from starlette.concurrency import run_in_threadpool

from whab.plugins.effect_store import EffectStore, _mkdir, fsync_directory
from whab.plugins.environments import prepare_environment
from whab.plugins.live import RegistryGeneration
from whab.plugins.runtime import PluginError, PluginRegistry
from whab.plugins.sdk import StorageContext, valid_plugin_name
from whab.plugins.storage import close_storage
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_transfer import evidence_record, read_evidence
from whab.plugins.storage_worker import StorageWorker, storage_error, supports_storage_worker
from whab.plugins.worker_selection import PreparationWorkers, reopen_environment

from .runtime import RuntimeCandidate, RuntimeResource


def _reference(prepared):
    return {"identifier": prepared.identifier, "distribution": prepared.distribution, "version": prepared.version}


def _validate_reference(record):
    if type(record) is not dict or record.keys() != {"identifier", "distribution", "version"}:
        raise ValueError()
    if type(record["identifier"]) is not str or len(record["identifier"]) != 64 or any(c not in "0123456789abcdef" for c in record["identifier"]):
        raise ValueError()
    if any(type(record[key]) is not str or not 1 <= len(record[key]) <= 200 for key in ("distribution", "version")):
        raise ValueError()


def _validate(record):
    if (type(record) is not dict or type(record.get("format")) is not int
            or record["format"] not in {1, 2} or record.get("kind") != "storage-code"
            or record.keys() != ({"format", "kind", "active", "native", "pending"}
                                 | ({"repair"} if record["format"] == 2 else set()))):
        raise ValueError()
    if record["active"] is not None:
        _validate_reference(record["active"])
        read_evidence(record["native"])
    elif record["native"] is not None:
        raise ValueError()
    pending = record["pending"]
    if pending is not None:
        if type(pending) is not dict or pending.keys() != {"source", "candidate", "evidence"}:
            raise ValueError()
        if pending["source"] is not None:
            _validate_reference(pending["source"])
        _validate_reference(pending["candidate"])
        if record["active"] != pending["candidate"]:
            raise ValueError()
        read_evidence(pending["evidence"])
    repair = record.get("repair")
    if repair is not None:
        if type(repair) is not dict or repair.keys() != {"expected_revision", "environment", "native"}:
            raise ValueError()
        _hash(repair["expected_revision"])
        _validate_reference(repair["environment"])
        read_evidence(repair["native"])
        if record["active"] != repair["environment"] or pending is not None:
            raise ValueError()


def _current(record):
    # Exact format-1 reader above; upgrade only as part of an authorized new
    # metadata decision. Unrecognized newer formats never become empty state.
    return {**record, "format": 2, "repair": record.get("repair")}


def _hash(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError()


def _journal(home, budget_id, environment, plugin_id):
    return EffectStore(Path(home) / "plugin-workers" / "storage" / plugin_id, "storage-upgrade.json",
                       {"budget_id": budget_id, "environment": environment, "plugin_id": plugin_id},
                       {"format": 2, "kind": "storage-code", "active": None, "native": None, "pending": None, "repair": None},
                       _validate, limit=64 * 1024)


def _prepared(home, plugin_id, reference):
    _validate_reference(reference)
    prepared = reopen_environment(Path(home) / "plugin-workers" / "environments", plugin_id, reference["identifier"])
    if _reference(prepared) != reference:
        raise storage_error(plugin_id)
    return prepared


def _worker(prepared, *, on_create=None):
    return StorageWorker(prepared.plugin_id, python=prepared.python,
                         expected_distribution=prepared.distribution, expected_version=prepared.version, on_create=on_create)


def _readiness(reader, context, before, *, opened=False):
    deadline = monotonic() + 60

    def call(method, *args):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise storage_error(reader.plugin_id)
        reader.timeout = min(30, remaining)
        return reader.call(method, *args)

    if not opened and reader.open(replace(context, read_only=True)) != before:
        raise storage_error(reader.plugin_id)
    # Check the complete public history against this bounded transport before
    # choosing worker code. A previously usable large host backend must never
    # become an unusable worker merely because its digest fitted in a frame.
    for workspace in ("personal", "demo"):
        for method in ("accounts", "categories", "transactions", "workspace"):
            call(method, workspace)
    for item in call("items"):
        call("get_item", item["id"])
    if call("commit_evidence") != before:
        raise storage_error(reader.plugin_id)


def _native_matches(current, before):
    if (current.budget_id != before.budget_id or current.environment != before.environment
            or current.instance_id != before.instance_id or current.authority_epoch != before.authority_epoch
            or current.role != "primary" or current.generation < before.generation
            or current.generation == before.generation and current != before):
        raise storage_error(None)


def _retained_matches(current, before):
    # A metadata-only repair may read a fenced/staged/copy/retired instance.
    # It preserves that role and cannot itself choose authority or change epoch.
    normalized = replace(current, role=before.role)
    if (normalized.budget_id != before.budget_id or normalized.environment != before.environment
            or normalized.instance_id != before.instance_id or normalized.authority_epoch != before.authority_epoch
            or normalized.generation < before.generation
            or normalized.generation == before.generation and normalized != before):
        raise storage_error(None)


def _retained_or_authority(home, plugin_id, context, current, before):
    if current.instance_id == before.instance_id and current.authority_epoch == before.authority_epoch:
        _retained_matches(current, before)
        return
    if current.instance_id == before.instance_id and current.generation < before.generation:
        raise storage_error(plugin_id)
    from .storage_runtime import validated_native_binding
    if not validated_native_binding(home, context, plugin_id, current):
        raise storage_error(plugin_id)


def _saved_owner(home, budget_id, environment, plugin_id):
    if not valid_plugin_name(plugin_id):
        raise ValueError()
    path = Path(home)
    for name in ("plugin-workers", "storage", plugin_id):
        path /= name
        if path.is_symlink():
            raise ValueError()
    if not (path / "storage-upgrade.json").is_file():
        raise ValueError()
    return _journal(home, budget_id, environment, plugin_id)


def _repair_error(plugin_id):
    return PluginError("The approved storage repair could not be validated or published. Stop this budget, preserve its files and inspect its saved decision before retrying.",
                       code="storage_worker_repair_failed", plugin_id=plugin_id)


def storage_worker_revision(home, budget_id, environment, plugin_id):
    """Opaque CAS revision of a stopped budget's exact saved code decision.

    Reads no native payload and executes no saved interpreter. A running owner
    keeps its journal locked, including while recovery has gated admission.
    """
    try:
        journal = _saved_owner(home, budget_id, environment, plugin_id)
        try:
            journal.snapshot()
            return hashlib.sha256(journal._raw).hexdigest()
        finally:
            journal.close()
    except Exception:
        raise _repair_error(plugin_id) from None


def _history_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_mode & 0o077 or information.st_size > 64 * 1024:
            raise ValueError()
        raw = os.read(descriptor, 64 * 1024 + 1)
        if not raw or len(raw) > 64 * 1024:
            raise ValueError()
        return raw
    finally:
        os.close(descriptor)


def _preserve_decision(journal, revision, raw):
    directory = journal.root / "history"
    _mkdir(directory)
    descriptor, temporary = tempfile.mkstemp(prefix=".storage-code-", dir=directory)
    try:
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as target:
                target.write(raw)
                target.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        target = directory / (revision + ".json")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _history_bytes(target) != raw:
                raise ValueError()
        fsync_directory(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _existing_native_owner(context, plugin_id):
    """Probe an existing child lease without creating/rebinding owner metadata."""
    root = context.home / "plugin-workers" / "native-storage" / plugin_id / context.environment
    path = root / "storage-writer.json"
    lock = root / "storage-writer.json.lock"
    if not path.exists() and not path.is_symlink():
        if lock.exists() or lock.is_symlink():
            raise ValueError()
        return None
    from whab.plugins.storage_worker import _writer_owner
    return _writer_owner(context, plugin_id)


_REPAIR_PENDING, _REPAIR_LOCK = {}, RLock()


class _RepairResources:
    """Retain a stopped repair's leases until its read-only probes are reaped."""

    def __init__(self, journal, plugin_id):
        self.journal, self.plugin_id, self.lease, self.workers = journal, plugin_id, None, {}

    def own(self, worker):
        self.workers[id(worker)] = worker

    def retired(self, worker):
        if worker.healthy or worker.cleanup_pending:
            raise storage_error(self.plugin_id, "storage_cleanup_pending")
        self.workers.pop(id(worker), None)

    def close(self):
        failed = False
        for worker in tuple(self.workers.values()):
            try:
                worker.close()
            except Exception:
                failed = True
            if worker.healthy or worker.cleanup_pending:
                failed = True
            else:
                self.workers.pop(id(worker), None)
        if failed:
            raise storage_error(self.plugin_id, "storage_cleanup_pending")
        if self.lease is not None:
            self.lease.close()
            self.lease = None
        self.journal.close()


def replace_saved_storage_worker(home, plugin_id, approvals, context, *, expected_revision):
    """Explicit current-host repair with the host STOPPED or fully drained.

    The caller owns admission and all native work must have finished. Retained
    journal ownership refuses a still-running controller; this helper never
    closes it, executes the prior environment, opens a writer, upgrades native
    schema, or resolves financial uncertainty. Native intents and receipts are
    untouched. Only a readable approved replacement and payload-free selection
    decision are published; exact prior selection bytes remain in history.
    """
    try:
        _hash(expected_revision)
        if type(context) is not StorageContext or context.read_only or context.initial_role != "primary":
            raise ValueError()
        budget_id = context.budget.budget_id if context.budget else "default"
        key = str(Path(home).absolute()), budget_id, context.environment, plugin_id
        with _REPAIR_LOCK:
            previous = _REPAIR_PENDING.get(key)
            if previous is not None:
                previous.close()
                _REPAIR_PENDING.pop(key)
        journal = _saved_owner(home, budget_id, context.environment, plugin_id)
        resources = _RepairResources(journal, plugin_id)
        try:
            record = journal.snapshot()
            raw = journal._raw
            current_revision = hashlib.sha256(raw).hexdigest()
            retry = current_revision != expected_revision
            repair = record.get("repair")
            if record["active"] is None or record["native"] is None:
                raise ValueError()
            if retry:
                if repair is None or repair["expected_revision"] != expected_revision:
                    raise PluginError("The saved storage decision changed. Inspect its current revision before replacing it.",
                                      code="storage_worker_repair_conflict", plugin_id=plugin_id)
                if (journal.root / "history").is_symlink():
                    raise ValueError()
                original = _history_bytes(journal.root / "history" / (expected_revision + ".json"))
                if hashlib.sha256(original).hexdigest() != expected_revision:
                    raise ValueError()
                journal._validated(original)
            resources.lease = _existing_native_owner(context, plugin_id)
            # A new approved closure is checked without executing any path or
            # interpreter from record['active'], even after a host SDK update.
            prepared = prepare_environment(Path(home) / "plugin-workers" / "environments", plugin_id, approvals,
                family="storage", worker_owner=resources.own, worker_retired=resources.retired)
            reference = _reference(prepared)
            if retry and (repair["environment"] != reference or record["active"] != reference):
                raise PluginError("The saved storage decision changed. Inspect its current revision before replacing it.",
                                  code="storage_worker_repair_conflict", plugin_id=plugin_id)
            before = read_evidence(record["native"])
            reader = _worker(prepared, on_create=resources.own)
            try:
                current = reader.open(replace(context, read_only=True))
                _retained_or_authority(home, plugin_id, context, current, before)
                if retry:
                    _retained_or_authority(home, plugin_id, context, current, read_evidence(repair["native"]))
                _readiness(reader, context, current, opened=True)
            finally:
                reader.close()
                resources.retired(reader)
            if not retry:
                _preserve_decision(journal, expected_revision, raw)
                journal.commit({**_current(record), "active": reference, "native": evidence_record(current), "pending": None,
                                "repair": {"expected_revision": expected_revision, "environment": reference,
                                           "native": evidence_record(current)}})
            return prepared
        finally:
            try:
                resources.close()
            except BaseException:
                with _REPAIR_LOCK:
                    _REPAIR_PENDING[key] = resources
                raise
    except PluginError:
        raise
    except Exception:
        raise _repair_error(plugin_id) from None


def storage_worker_descriptor(home, budget_id, environment, template, *, worker_owner=None, storage_owner=None):
    """Startup descriptor lookup BEFORE any legacy native factory may run."""
    path = Path(home) / "plugin-workers" / "storage" / template.id / "storage-upgrade.json"
    if not path.exists() and not path.is_symlink():
        return template
    journal = _journal(home, budget_id, environment, template.id)
    try:
        record = journal.snapshot()
        if record["active"] is None:
            return template
        prepared = _prepared(home, template.id, record["active"])
    finally:
        journal.close()
    probe = _worker(prepared, on_create=worker_owner)
    try:
        descriptor = replace(template, contract=probe.contract, storage_payload_paths=probe.info["payload_paths"])
        if not supports_storage_worker(descriptor):
            raise storage_error(template.id)
    finally:
        probe.close()

    bound = {"owner": None, "record": record}

    def bind_owner(owner):
        latest = owner.snapshot()
        if latest["active"] != _reference(prepared):
            raise storage_error(template.id)
        bound.update(owner=owner, record=latest)

    def open_approved(context):
        # Bootstrap owns this decision across both native opens. Once the live
        # controller retains it, factories borrow that exact owner. A retired
        # owner's closed handle is never replaced by reopening its journal.
        borrowed = bound["owner"]
        owner = borrowed if borrowed is not None else _journal(home, budget_id, environment, template.id)
        backend = None
        try:
            latest = owner.snapshot()
            if latest != bound["record"] or latest["active"] != _reference(prepared):
                raise ValueError()
            if context.initial_role == "primary":
                reader = _worker(prepared, on_create=worker_owner)
                try:
                    current = reader.open(replace(context, read_only=True))
                    _retained_or_authority(home, template.id, context, current, read_evidence(latest["native"]))
                    if current.role != "primary":
                        from .storage_runtime import validated_native_binding
                        if not validated_native_binding(home, context, template.id, current):
                            raise ValueError()
                    if latest["pending"] is not None and current != read_evidence(latest["pending"]["evidence"]):
                        raise ValueError()
                finally:
                    reader.close()
                context = replace(context, expected_instance_id=current.instance_id)
            backend = StorageWorkerProxy(_worker(prepared, on_create=worker_owner), context,
                Path(home) / "plugin-workers" / "storage-intents", on_create=storage_owner)
            # A durable code decision is authoritative even when activation's
            # reply was lost. Finish its materialization before other app
            # factories can acquire resources, never select earlier host code.
            current = backend.commit_evidence()
            if latest["pending"] is not None:
                before = read_evidence(latest["pending"]["evidence"])
                if current != before:
                    raise ValueError()
            if context.initial_role == "primary" and current.role == "primary":
                if latest["pending"] is not None or latest["native"] != evidence_record(current):
                    owner.commit({**_current(latest), "native": evidence_record(current), "pending": None})
            bound["record"] = owner.snapshot()
            backend.prepared_environment = prepared
            backend._bind_storage_owner = bind_owner
            return backend
        except BaseException:
            if backend is not None:
                backend.close()
            raise storage_error(template.id) from None
        finally:
            if borrowed is None:
                owner.close()

    return replace(descriptor, storage=open_approved)


class _Target:
    """Generation service populated only inside its gated durable commit."""

    def __init__(self):
        self.backend = None

    def __getattr__(self, name):
        if self.backend is None:
            raise AttributeError(name)
        return getattr(self.backend, name)

    def close(self):
        if self.backend is not None:
            self.backend.close()


class StorageBinding:
    def __init__(self, environment, target):
        self.environment, self.target = environment, target

        async def close():
            await run_in_threadpool(target.close)
            cleanup = getattr(target, "worker_cleanup_pending", None)
            if callable(cleanup) and cleanup():
                raise storage_error(environment.plugin_id)

        self.resource = RuntimeResource(environment.plugin_id, close)

    @property
    def worker(self):
        target = self.target

        class Health:
            @property
            def healthy(self):
                return target.worker_health()

            @property
            def cleanup_pending(self):
                return target.worker_cleanup_pending()

            def close(self):
                target.close()

        return Health()


class StorageUpgrade:
    """One budget's code selection and explicit uncertain-write recovery."""

    def __init__(self, home, runtime, tracked, context, registry, *, activated=None, check=None, binding=None, on_create=None):
        self.home, self.runtime, self.tracked, self.context = Path(home), runtime, tracked, context
        self.registry, self.activated = registry, activated
        self.check = check
        self.plugin_id = tracked.storage_plugin_id
        self.journal = None
        self._workers, self._proxies, self._worker_lock = {}, {}, RLock()
        self.pending = None
        self.closed = False
        self.binding = binding
        if on_create is not None:
            on_create(self)
        self.journal = _journal(home, runtime.budget_id, context.environment, self.plugin_id)
        backend = tracked.store
        prepared = getattr(backend, "prepared_environment", None)
        if prepared is not None:
            self.own_worker(backend.worker)
            self.own_proxy(backend)
            self.binding = binding or StorageBinding(prepared, backend)
            record = self.journal.snapshot()
            try:
                if record["active"] != _reference(prepared):
                    raise storage_error(self.plugin_id)
                current = backend.commit_evidence()
                try:
                    _native_matches(current, read_evidence(record["native"]))
                except PluginError:
                    from .storage_runtime import validated_native_binding
                    _retained_or_authority(home, self.plugin_id, context, current, read_evidence(record["native"]))
                    if record["pending"] is not None or not validated_native_binding(home, context, self.plugin_id, current, resolved=True):
                        raise
                    self.journal.commit({**_current(record), "native": evidence_record(current), "repair": None})
                bind_owner = getattr(backend, "_bind_storage_owner", None)
                if bind_owner is not None:
                    bind_owner(self.journal)
            except BaseException:
                raise
            backend._on_unknown = runtime.fence_storage
            if backend.uncertain:
                runtime.fence_storage(self.plugin_id)

    def own_worker(self, worker):
        with self._worker_lock:
            self._workers = {identity: previous for identity, previous in self._workers.items()
                             if getattr(previous, "_starting", False) or previous.healthy or previous.cleanup_pending}
            self._workers[id(worker)] = worker

    def own_proxy(self, backend):
        with self._worker_lock:
            self._proxies = {identity: previous for identity, previous in self._proxies.items()
                             if not previous._closed}
            self._proxies[id(backend)] = backend

    def supported(self):
        try:
            plugin = self.runtime._active.services["plugins"].registry.storage_plugin(self.plugin_id)
            return supports_storage_worker(plugin)
        except Exception:
            return False

    def status(self):
        backend = self.tracked.store
        return {"supported": self.supported(), "plugin": self.plugin_id,
                "mode": "worker" if self.binding is not None else "host",
                "uncertain": bool(getattr(backend, "uncertain", False)),
                "active": self.journal.snapshot()["active"], "runtime": self.runtime.status()}

    async def upgrade(self, plugin_id, approvals):
        if plugin_id != self.plugin_id or not self.supported():
            raise PluginError("This backend requires explicit migration or a supported local storage worker contract.",
                              code="restart_required", plugin_id=plugin_id)
        if self.check is not None:
            self.check()
        active_state = {}

        async def prepare(preparation):
            probes = PreparationWorkers(preparation, plugin_id)
            prepared = await run_in_threadpool(prepare_environment,
                self.home / "plugin-workers" / "environments", plugin_id, approvals, family="storage",
                worker_owner=probes.own, worker_retired=probes.retired)
            target = _Target()
            binding = StorageBinding(prepared, target)
            preparation.own(binding.resource)
            active_state.update(prepared=prepared, target=target, binding=binding)

            def check():
                with self.tracked.lock:
                    before = self.tracked.commit_evidence()
                    if before.role != "primary":
                        raise storage_error(plugin_id)
                    reader = _worker(prepared, on_create=probes.own)
                    try:
                        _readiness(reader, self.context, before)
                    finally:
                        reader.close()
                        probes.retired(reader)
            await run_in_threadpool(check)
            active = preparation.active
            descriptors = tuple(active.services["plugins"].descriptors())
            probe = _worker(prepared, on_create=probes.own)
            try:
                contract, payload_paths = probe.contract, probe.info["payload_paths"]
            finally:
                probe.close()
                probes.retired(probe)

            owner = self.journal

            def factory(context):
                try:
                    if owner.snapshot()["active"] != _reference(prepared):
                        raise ValueError()
                except Exception:
                    raise storage_error(plugin_id) from None
                backend = StorageWorkerProxy(_worker(prepared, on_create=self.own_worker), context,
                    self.home / "plugin-workers" / "storage-intents", on_create=self.own_proxy)
                backend.prepared_environment = prepared
                return backend

            descriptors = tuple(replace(plugin, storage=factory, contract=contract, storage_payload_paths=payload_paths)
                                if plugin.id == plugin_id else plugin for plugin in descriptors)
            if not supports_storage_worker(next(plugin for plugin in descriptors if plugin.id == plugin_id)):
                raise storage_error(plugin_id)
            generation = active.services["plugins"]
            workers = {**generation.workers, plugin_id: binding}
            registry = RegistryGeneration(PluginRegistry(descriptors), stable=generation.stable, workers=workers, previous=generation)
            resources = tuple(resource for resource in active.resources
                              if not (self.binding is not None and resource is self.binding.resource)) + (binding.resource,)
            return RuntimeCandidate({**active.services, "storage": target, "plugins": registry}, resources)

        async def finalize(_preparation, candidate):
            probes = PreparationWorkers(_preparation, plugin_id)
            def check_latest():
                with self.tracked.lock:
                    before = self.tracked.commit_evidence()
                    reader = _worker(active_state["prepared"], on_create=probes.own)
                    try:
                        _readiness(reader, self.context, before)
                    finally:
                        reader.close()
                        probes.retired(reader)
                    active_state["before"] = before
            await run_in_threadpool(check_latest)
            return candidate

        async def commit(candidate, _number):
            self.pending = (active_state, candidate)

            def select_and_open():
                with self.tracked.lock:
                    before = active_state["before"]
                    if self.tracked.commit_evidence() != before:
                        raise storage_error(plugin_id)
                    record = self.journal.snapshot()
                    reference = _reference(active_state["prepared"])
                    self.journal.commit({**_current(record), "active": reference, "native": evidence_record(before), "repair": None, "pending": {
                        "source": record["active"], "candidate": reference, "evidence": evidence_record(before),
                    }})
                    self._materialize(active_state, candidate)
            await run_in_threadpool(select_and_open)

        return await self.runtime.transition(prepare, finalize=finalize, commit=commit, plugin_id=plugin_id)

    def _materialize(self, state, candidate):
        # Also used after a lost durable-selection response. Old native work is
        # drained; close its sole writer before acquiring JSON's stable lock.
        prepared, target, before = state["prepared"], state["target"], state["before"]
        if target.backend is None:
            close_storage(self.tracked.store)
            backend = StorageWorkerProxy(_worker(prepared, on_create=self.own_worker), replace(self.context, expected_instance_id=before.instance_id),
                                         self.home / "plugin-workers" / "storage-intents",
                                         on_unknown=self.runtime.fence_storage, on_create=self.own_proxy)
            try:
                if backend.commit_evidence() != before:
                    raise storage_error(self.plugin_id)
            except BaseException:
                backend.close()
                raise
            backend.prepared_environment = prepared
            target.backend = backend
        self.tracked.retarget(target, self.runtime, self.plugin_id)
        self.binding = state["binding"]
        if self.activated is not None:
            self.activated(candidate.services["plugins"].registry)
        record = self.journal.snapshot()
        self.journal.commit({**_current(record), "native": evidence_record(self.tracked.commit_evidence()), "pending": None})
        self.pending = None

    async def recover(self, authority="candidate"):
        if self.pending is not None:
            state, candidate = self.pending
            if self.runtime._uncertain is None or self.runtime._uncertain[1] is not candidate:
                raise storage_error(self.plugin_id)

            def validate(_authority, _active, _candidate):
                self.journal.recover()
                record = self.journal.snapshot()
                selected_candidate = record["active"] == _reference(state["prepared"])
                if (_authority == "candidate") != selected_candidate:
                    return False
                with self.tracked.lock:
                    if selected_candidate:
                        self._materialize(state, candidate)
                    elif self.tracked.commit_evidence() != state["before"]:
                        return False
                return True

            result = await self.runtime.recover(authority, lambda *args: run_in_threadpool(validate, *args))
            self.pending = None
            return result
        backend = self.tracked.store
        recover = getattr(backend, "recover", None)
        if not callable(recover):
            raise storage_error(self.plugin_id)

        async def validate():
            def reconcile():
                with self.tracked.lock:
                    record = self.journal.snapshot()
                    if record["active"] is None or record["native"] is None or record["pending"] is not None:
                        raise storage_error(self.plugin_id)
                    recover(minimum=read_evidence(record["native"]))
                    _native_matches(self.tracked.commit_evidence(), read_evidence(record["native"]))
                    if self.tracked.session is not None:
                        self.tracked.session.bind(self.tracked.store)
                    if self.tracked.on_mutation is not None:
                        self.tracked.on_mutation()
                return True
            return await run_in_threadpool(reconcile)

        return await self.runtime.recover_storage(validate, plugin_id=self.plugin_id)

    def retarget(self, plugin_id, context):
        """Called by explicit backend migration/promotion while gated/drained."""
        if self.runtime.status()["admission"] != "gated" or self.runtime.status()["active_leases"]:
            raise storage_error(plugin_id)
        self.journal.close()
        self.plugin_id, self.context = plugin_id, context
        self.journal = _journal(self.home, self.runtime.budget_id, context.environment, plugin_id)
        record = self.journal.snapshot()
        if record["active"] is not None:
            self.journal.commit({**_current(record), "native": evidence_record(self.tracked.commit_evidence()), "repair": None})
            record = self.journal.snapshot()
        bind_owner = getattr(self.tracked.store, "_bind_storage_owner", None)
        if bind_owner is not None:
            bind_owner(self.journal)
        self.binding = getattr(self.tracked.store, "_storage_worker_binding", None)
        if self.binding is None and record["active"]:
            self.binding = StorageBinding(_prepared(self.home, plugin_id, record["active"]), self.tracked.store)

    def build_generation(self, active, prepared, target, source_id):
        generation = active.services["plugins"]
        workers = dict(generation.workers)
        previous = workers.pop(source_id, None)
        resources = tuple(resource for resource in active.resources
                          if previous is None or resource is not previous.resource)
        environment = getattr(prepared, "worker_environment", None)
        if callable(environment):
            binding = StorageBinding(_prepared(self.home, target.id, environment()), prepared)
            prepared._storage_worker_binding = binding
            workers[target.id] = binding
            resources += (binding.resource,)
        descriptors = tuple(target if plugin.id == source_id else plugin for plugin in generation.descriptors())
        registry = RegistryGeneration(PluginRegistry(descriptors), stable=generation, workers=workers, previous=generation)
        return RuntimeCandidate({**active.services, "storage": prepared, "plugins": registry}, resources)

    async def close(self):
        await run_in_threadpool(self.close_sync)

    def close_sync(self):
        with self._worker_lock:
            workers = tuple(self._workers.values())
            proxies = tuple(self._proxies.values())
        pending = False
        for backend in proxies:
            try:
                backend.close()
            except Exception:
                pending = True
        for worker in workers:
            try:
                worker.close()
            except Exception:
                pending = True
            pending = pending or worker.cleanup_pending or worker.healthy
        if pending:
            raise storage_error(self.plugin_id, "storage_cleanup_pending")
        self.release()

    def release(self):
        """Also release an app constructed only to create a portable budget."""
        if not self.closed:
            with self._worker_lock:
                if (any(worker.healthy or worker.cleanup_pending for worker in self._workers.values())
                        or any(backend.worker_owned() or not backend._closed for backend in self._proxies.values())):
                    raise storage_error(self.plugin_id, "storage_cleanup_pending")
            self.closed = True
            if self.journal is not None:
                self.journal.close()
