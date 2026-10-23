"""Durable local storage handover, layered on the per-budget runtime barrier.

The journal holds identities and validation hashes only. Original backend files,
saved containers, plugin namespaces and runtime keys remain with their owners.
Application integration supplies coherent service construction and includes all
background work in BudgetRuntime leases before enabling this coordinator.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import uuid4

from whab.domain.errors import StoreError
from whab.plugins.sdk import valid_plugin_name
from whab.plugins.storage_replica import read_replica_watermark, replica_record
from whab.plugins.storage_transfer import (
    CommitEvidence,
    canonical_storage_json,
    evidence_record,
    read_evidence,
    read_storage_json,
    storage_identifier,
)

from .runtime import RuntimeCandidate, RuntimeResource

_STAGES = ("prepared", "imported", "validated", "source_fenced", "committed", "active", "retired", "aborted")
_COMMITTED_STAGES = frozenset({"committed", "active", "retired"})
_JOURNAL_FIELDS = {
    "formatVersion", "budget_id", "environment", "transition_id", "source_plugin_id",
    "destination_plugin_id", "source", "destination", "stage", "authority_instance_id",
    "target_epoch", "namespace", "transfer_digest",
}
_JOURNAL_V2_FIELDS = _JOURNAL_FIELDS | {"kind", "replica"}


class StorageAuthorityError(StoreError):
    """A fixed recovery diagnostic that never incorporates private exceptions."""

    def __init__(self, code):
        messages = {
            "invalid_journal": "Storage authority metadata is invalid; this budget must remain paused.",
            "identity_mismatch": "Storage identity does not match this budget or environment.",
            "destination_not_empty": "Storage migration requires a supported empty staged destination.",
            "validation_failed": "Storage transfer validation failed; validate authority before resuming.",
            "preserved_state_changed": "Preserved plugin, container or key-reference evidence changed during migration.",
            "recovery_required": "Storage authority could not be confirmed; this budget must remain paused.",
            "cleanup_failed": "The previous storage instance remains fenced but its cleanup needs attention.",
        }
        self.code = code
        super().__init__(messages[code])


def _hash(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


@dataclass(frozen=True, slots=True)
class NamespaceEvidence:
    """Host-owned opaque state remains in place; no key material is accepted.

    The key reference is a host-assigned UUID stored beside runtime configuration,
    never the encryption key itself. Container hashes refer to preserved original
    bytes. ``digest`` covers every plugin's settings/state/files, including unknown
    and disabled plugins, with names and bytes bound into the hash.
    """

    digest: str
    original_container_digest: str | None
    key_reference: str

    def __post_init__(self):
        from uuid import UUID
        if not _hash(self.digest) or (self.original_container_digest is not None and not _hash(self.original_container_digest)):
            raise StorageAuthorityError("invalid_journal")
        try:
            if type(self.key_reference) is not str or str(UUID(self.key_reference)) != self.key_reference:
                raise ValueError
        except ValueError:
            raise StorageAuthorityError("invalid_journal") from None


def namespace_record(value):
    if type(value) is not NamespaceEvidence:
        raise StorageAuthorityError("invalid_journal")
    return {name: getattr(value, name) for name in NamespaceEvidence.__slots__}


def _read_namespace(value):
    if type(value) is not dict or value.keys() != set(NamespaceEvidence.__slots__):
        raise StorageAuthorityError("invalid_journal")
    return NamespaceEvidence(**value)


def _journal_record(value, budget_id, environment):
    try:
        if type(value) is not dict or type(value.get("formatVersion")) is not int:
            raise ValueError
        if value["formatVersion"] == 1 and value.keys() == _JOURNAL_FIELDS:
            value = {**value, "formatVersion": 2, "kind": "migration", "replica": None}
        if value["formatVersion"] != 2 or value.keys() != _JOURNAL_V2_FIELDS or value["kind"] not in {"migration", "promotion"}:
            raise ValueError
        if value["budget_id"] != budget_id or value["environment"] != environment:
            raise ValueError
        storage_identifier(value["transition_id"])
        if any(not valid_plugin_name(value[key]) for key in ("source_plugin_id", "destination_plugin_id")):
            raise ValueError
        source, destination = read_evidence(value["source"]), read_evidence(value["destination"])
        for evidence in (source, destination):
            if evidence.budget_id != budget_id or evidence.environment != environment:
                raise ValueError
        if source.instance_id == destination.instance_id or value["stage"] not in _STAGES:
            raise ValueError
        from uuid import UUID
        if type(value["target_epoch"]) is not str or str(UUID(value["target_epoch"])) != value["target_epoch"] or value["target_epoch"] == source.authority_epoch:
            raise ValueError
        committed = value["stage"] in _COMMITTED_STAGES
        expected_id = destination.instance_id if committed else source.instance_id
        if value["authority_instance_id"] != expected_id:
            raise ValueError
        if not _hash(value["transfer_digest"]):
            raise ValueError
        _read_namespace(value["namespace"])
        if value["kind"] == "promotion":
            watermark = read_replica_watermark(value["replica"])
            if (watermark.source_instance_id != source.instance_id
                    or watermark.source_authority_epoch != source.authority_epoch
                    or watermark.source_generation != source.generation
                    or watermark.source_operation_id != source.operation_id
                    or watermark.state_digest != source.state_digest
                    or watermark.transfer_digest != value["transfer_digest"]):
                raise ValueError
        elif value["replica"] is not None:
            raise ValueError
    except (KeyError, TypeError, ValueError, StoreError):
        raise StorageAuthorityError("invalid_journal") from None
    return value


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_directory(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise StorageAuthorityError("invalid_journal")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        _fsync_directory(directory)
        _fsync_directory(directory.parent)


class AuthorityJournal:
    """One exact-schema durable decision record, never a payload/event log.

    The host owns its runtime directory. An uncertain replace/fsync result must
    be resolved by reopening this record; callers must not infer rollback from
    the exception. No fallback is allowed when an existing record is invalid.
    """

    def __init__(self, path, budget_id, environment):
        self.path = Path(path)
        storage_identifier(budget_id)
        if environment not in {"sandbox", "production"}:
            raise StorageAuthorityError("identity_mismatch")
        self.budget_id, self.environment = budget_id, environment
        self._lock = RLock()
        self._writer_descriptor = None

    def acquire_writer(self):
        """Hold a stable control-plane lock through the whole handover."""
        with self._lock:
            if self._writer_descriptor is not None:
                raise StorageAuthorityError("recovery_required")
            _durable_directory(self.path.parent)
            lock_path = self.path.with_name(self.path.name + ".lock")
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(descriptor).st_size == 0:
                        os.write(descriptor, b"\x00")
                        os.fsync(descriptor)
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fsync(descriptor)
                _fsync_directory(self.path.parent)
            except BaseException:
                os.close(descriptor)
                raise StorageAuthorityError("recovery_required") from None
            self._writer_descriptor = descriptor
            return descriptor

    def release_writer(self, descriptor):
        with self._lock:
            if self._writer_descriptor != descriptor:
                raise StorageAuthorityError("recovery_required")
            self._writer_descriptor = None
            os.close(descriptor)

    @contextmanager
    def writer(self):
        descriptor = self.acquire_writer()
        try:
            yield
        finally:
            self.release_writer(descriptor)

    def _check_writer(self):
        if self._writer_descriptor is None:
            raise StorageAuthorityError("recovery_required")
        try:
            current = self.path.with_name(self.path.name + ".lock").stat()
            held = os.fstat(self._writer_descriptor)
        except OSError:
            raise StorageAuthorityError("recovery_required") from None
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise StorageAuthorityError("recovery_required")

    def read(self):
        with self._lock:
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(65537)
            except FileNotFoundError:
                return None
            except OSError:
                raise StorageAuthorityError("recovery_required") from None
            try:
                return _journal_record(read_storage_json(raw, limit=65536), self.budget_id, self.environment)
            except StoreError:
                raise StorageAuthorityError("invalid_journal") from None

    def write(self, record):
        raw = canonical_storage_json(_journal_record(record, self.budget_id, self.environment), limit=65536)
        with self._lock:
            self._check_writer()
            _durable_directory(self.path.parent)
            descriptor, temporary = tempfile.mkstemp(prefix=".authority-", dir=self.path.parent)
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
                os.replace(temporary, self.path)
                _fsync_directory(self.path.parent)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


class PreparedStorage:
    """Private prepared handle that stays coherent through validation reopen.

    Candidate services must retain this handle rather than cache its underlying
    backend. The handle never exposes a retired bound method.
    """

    def __init__(self, backend):
        self._backend, self._lock, self._closed = backend, RLock(), False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        with self._lock:
            if self._closed:
                raise StorageAuthorityError("recovery_required")
            if not callable(getattr(self._backend, name)):
                raise AttributeError(name)

        def invoke(*args, **kwargs):
            with self._lock:
                if self._closed:
                    raise StorageAuthorityError("recovery_required")
                return getattr(self._backend, name)(*args, **kwargs)
        return invoke

    def reopen(self, factory):
        with self._lock:
            if not self._closed:
                self._backend.close()
                self._closed = True
            backend = factory()
            self._backend, self._closed = backend, False

    def close(self):
        with self._lock:
            if not self._closed:
                self._backend.close()
                self._closed = True

    async def aclose(self):
        await asyncio.to_thread(self.close)


def _identity(evidence, journal):
    if (type(evidence) is not CommitEvidence or evidence.budget_id != journal.budget_id
            or evidence.environment != journal.environment):
        raise StorageAuthorityError("identity_mismatch")
    return evidence


def _same_content(snapshot, other):
    return (snapshot.source.state_digest == other.source.state_digest
            and snapshot.financial == other.financial and snapshot.protected == other.protected
            and snapshot.receipt_bytes == other.receipt_bytes)


def resolve_authority(journal, source, destination, namespace):
    """Validate a persisted decision after reopen, then finish only that decision.

    This runs while admission remains gated. Return ``source`` or ``destination``
    as the authoritative side; never activate a destination without a committed
    journal decision. A destination may contain later valid writes after commit.
    """
    with journal.writer():
        return _resolve_authority(journal, source, destination, namespace)


def _resolve_authority(journal, source, destination, namespace):
    record = journal.read()
    if record is None:
        raise StorageAuthorityError("recovery_required")
    committed = record["stage"] in _COMMITTED_STAGES
    preserved = _read_namespace(record["namespace"])
    # After activation, legitimate plugin writes can change active namespaces.
    # Original container bytes and the external key reference remain preserved.
    if (namespace.key_reference != preserved.key_reference
            or (record["stage"] != "aborted" and namespace.original_container_digest != preserved.original_container_digest)
            or (not committed and record["stage"] != "aborted" and namespace != preserved)):
        raise StorageAuthorityError("preserved_state_changed")
    current_source = _identity(source.commit_evidence(), journal)
    current_destination = _identity(destination.commit_evidence(), journal)
    original_source, original_destination = read_evidence(record["source"]), read_evidence(record["destination"])
    promotion = record["kind"] == "promotion"
    if current_source.instance_id != original_source.instance_id or current_destination.instance_id != original_destination.instance_id:
        raise StorageAuthorityError("identity_mismatch")
    if (current_source.authority_epoch != original_source.authority_epoch
            or current_source.generation < original_source.generation):
        raise StorageAuthorityError("recovery_required")
    if not committed:
        later_aborted_write = record["stage"] == "aborted" and current_source.generation > original_source.generation
        if ((not later_aborted_write and (current_source.generation != original_source.generation
                                         or current_source.state_digest != original_source.state_digest))
                or current_source.role not in {"primary", "fenced"}
                or current_destination.role != ("copy" if promotion else "staged")):
            raise StorageAuthorityError("recovery_required")
        if promotion:
            if record["stage"] != "aborted" and destination.replica_watermark() != read_replica_watermark(record["replica"]):
                raise StorageAuthorityError("recovery_required")
            destination.abort_promotion(record["transition_id"], current_destination)
        if current_source.role == "fenced":
            source.set_role(record["transition_id"], current_source, "primary")
        if record["stage"] != "aborted":
            journal.write({**record, "stage": "aborted"})
        return "source"
    if (current_source.role not in {"fenced", "retired"}
            or current_source.generation != original_source.generation
            or current_source.state_digest != original_source.state_digest):
        raise StorageAuthorityError("recovery_required")
    if current_destination.role == ("copy" if promotion else "staged"):
        if (current_destination.generation != original_source.generation
                or current_destination.state_digest != original_source.state_digest
                or current_destination.authority_epoch != original_source.authority_epoch):
            raise StorageAuthorityError("recovery_required")
        if promotion and destination.replica_watermark() != read_replica_watermark(record["replica"]):
            raise StorageAuthorityError("recovery_required")
        current_destination = destination.set_role(record["transition_id"], current_destination, "primary",
                                                   authority_epoch=record["target_epoch"])
    if (current_destination.role != "primary" or current_destination.authority_epoch != record["target_epoch"]
            or current_destination.generation < original_source.generation
            or (current_destination.generation == original_source.generation
                and current_destination.state_digest != original_source.state_digest)):
        raise StorageAuthorityError("recovery_required")
    if record["stage"] == "committed":
        journal.write({**record, "stage": "active", "destination": evidence_record(current_destination)})
    return "destination"


async def _invoke(callback, *args):
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


class StorageAuthorityCoordinator:
    """One budget's explicit preview/transition/recovery control plane.

    Destination factories must create/open an isolated staged backend; their
    context binds this budget/environment explicitly. The candidate builder
    retains unrelated services and constructs every storage consumer against the
    supplied PreparedStorage handle. The application owns those integration
    details; this class never changes routes, provider connections or key files.
    """

    def __init__(self, runtime, journal, namespace_evidence):
        if runtime.budget_id != journal.budget_id or not callable(namespace_evidence):
            raise StorageAuthorityError("identity_mismatch")
        self.runtime, self.journal, self.namespace_evidence = runtime, journal, namespace_evidence
        self._pending = None
        self._cleanup_errors = []
        self._serial = asyncio.Lock()
        self._tasks = set()

    async def _owned(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def finished(result):
            self._tasks.discard(result)
            if not result.cancelled():
                result.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    def status(self):
        record = self.journal.read()
        return {"budget_id": self.journal.budget_id,
                "transition_id": record["transition_id"] if record else None,
                "stage": record["stage"] if record else "unconfigured",
                "authority_instance_id": record["authority_instance_id"] if record else None,
                "tasks_pending": len(self._tasks),
                "cleanup_errors": list(self._cleanup_errors)}

    async def migrate(self, source_plugin_id, destination_plugin_id, prepare_destination,
                      reopen_destination, build_candidate, *, flush=None, activate=None, drain_timeout=30):
        """Schedule outside an admitted request, or await outside its lease.

        BudgetRuntime owns cancellation and serializes this transition with other
        plugin transitions. I/O runs in threads, and a timeout never cancels an
        admitted financial mutation. Preparation failure leaves source unchanged.
        """
        return await self._owned(self._serialized_migrate(
            source_plugin_id, destination_plugin_id, prepare_destination, reopen_destination,
            build_candidate, flush=flush, activate=activate, drain_timeout=drain_timeout, promotion=None))

    async def promote(self, source_plugin_id, destination_plugin_id, prepare_destination,
                      reopen_destination, build_candidate, *, catch_up, flush=None, activate=None, drain_timeout=30):
        """Catch up an explicit copy while drained, then commit one authority decision."""
        if not callable(catch_up):
            raise StorageAuthorityError("validation_failed")
        return await self._owned(self._serialized_migrate(
            source_plugin_id, destination_plugin_id, prepare_destination, reopen_destination,
            build_candidate, flush=flush, activate=activate, drain_timeout=drain_timeout, promotion=catch_up))

    async def _serialized_migrate(self, *args, **kwargs):
        async with self._serial:
            descriptor = await asyncio.to_thread(self.journal.acquire_writer)
            try:
                return await self._migrate(*args, **kwargs)
            finally:
                await asyncio.to_thread(self.journal.release_writer, descriptor)

    async def _migrate(self, source_plugin_id, destination_plugin_id, prepare_destination,
                       reopen_destination, build_candidate, *, flush, activate, drain_timeout, promotion):
        if not valid_plugin_name(source_plugin_id) or not valid_plugin_name(destination_plugin_id):
            raise StorageAuthorityError("identity_mismatch")
        pending = {}

        async def prepare(preparation):
            source = preparation.active.services["storage"]
            source_evidence = _identity(await asyncio.to_thread(source.commit_evidence), self.journal)
            if source_evidence.role != "primary":
                raise StorageAuthorityError("recovery_required")
            destination = PreparedStorage(await asyncio.to_thread(prepare_destination))
            preparation.own(RuntimeResource(destination_plugin_id, destination.aclose))
            destination_evidence = _identity(await asyncio.to_thread(destination.commit_evidence), self.journal)
            if destination_evidence.role != ("copy" if promotion else "staged") or destination_evidence.instance_id == source_evidence.instance_id:
                raise StorageAuthorityError("destination_not_empty")
            if promotion:
                if await asyncio.to_thread(destination.replica_watermark) is None:
                    raise StorageAuthorityError("validation_failed")
            else:
                snapshot = await asyncio.to_thread(source.export_local)
                preview = await asyncio.to_thread(destination.preview_local, snapshot)
                if preview.empty is not True or preview.supported is not True:
                    raise StorageAuthorityError("destination_not_empty")
            services = await _invoke(build_candidate, preparation.active, destination)
            candidate = services if type(services) is RuntimeCandidate else RuntimeCandidate(services, preparation.active.resources)
            if candidate.services.get("storage") is not destination:
                raise StorageAuthorityError("validation_failed")
            # Previous coordinator-owned storage resources retire after resume;
            # unrelated resources retain their existing identity and lifetime.
            candidate = RuntimeCandidate(candidate.services, tuple(
                resource for resource in candidate.resources
                if getattr(resource.close, "__self__", None) is not source
            ))
            pending.update(source=source, destination=destination, source_evidence=source_evidence,
                           initial_destination=destination_evidence, transition_id=str(uuid4()),
                           reopen_destination=reopen_destination, activate=activate, promotion=promotion)
            self._pending = pending
            return candidate

        async def commit(candidate, number):
            source, destination = pending["source"], pending["destination"]
            if flush is not None:
                await _invoke(flush)
            namespace = await asyncio.to_thread(self.namespace_evidence)
            namespace_record(namespace)
            watermark = await _invoke(promotion, source, destination) if promotion else None
            snapshot = await asyncio.to_thread(source.export_local)
            _identity(snapshot.source, self.journal)
            if snapshot.source.role != "primary":
                raise StorageAuthorityError("recovery_required")
            destination_evidence = _identity(await asyncio.to_thread(destination.commit_evidence), self.journal)
            if promotion:
                if (watermark != await asyncio.to_thread(destination.replica_watermark)
                        or watermark.source_instance_id != snapshot.source.instance_id
                        or watermark.source_authority_epoch != snapshot.source.authority_epoch
                        or watermark.source_generation != snapshot.source.generation
                        or watermark.state_digest != snapshot.source.state_digest or watermark.transfer_digest != snapshot.digest):
                    raise StorageAuthorityError("validation_failed")
            else:
                preview = await asyncio.to_thread(destination.preview_local, snapshot)
                if preview.empty is not True or preview.supported is not True:
                    raise StorageAuthorityError("destination_not_empty")
            previous = await asyncio.to_thread(self.journal.read)
            if previous is not None and (previous["authority_instance_id"] != snapshot.source.instance_id
                                         or previous["stage"] not in {"active", "retired", "aborted"}):
                raise StorageAuthorityError("recovery_required")
            record = {
                "formatVersion": 2, "kind": "promotion" if promotion else "migration",
                "replica": replica_record(watermark) if promotion else None,
                "budget_id": self.journal.budget_id, "environment": self.journal.environment,
                "transition_id": pending["transition_id"], "source_plugin_id": source_plugin_id,
                "destination_plugin_id": destination_plugin_id, "source": evidence_record(snapshot.source),
                "destination": evidence_record(destination_evidence), "stage": "prepared",
                "authority_instance_id": snapshot.source.instance_id, "target_epoch": str(uuid4()),
                "namespace": namespace_record(namespace), "transfer_digest": snapshot.digest,
            }
            pending.update(record=record, snapshot=snapshot, namespace=namespace)
            await asyncio.to_thread(self.journal.write, record)
            if promotion:
                destination_evidence = await asyncio.to_thread(destination.prepare_promotion, record["transition_id"], destination_evidence, watermark)
            else:
                destination_evidence = await asyncio.to_thread(destination.import_local, record["transition_id"], snapshot)
            record = {**record, "stage": "imported", "destination": evidence_record(destination_evidence)}
            await asyncio.to_thread(self.journal.write, record)
            await asyncio.to_thread(destination.reopen, reopen_destination)
            reopened = await asyncio.to_thread(destination.export_local)
            if not _same_content(snapshot, reopened) or reopened.source != destination_evidence:
                raise StorageAuthorityError("validation_failed")
            if await asyncio.to_thread(source.commit_evidence) != snapshot.source:
                raise StorageAuthorityError("validation_failed")
            if await asyncio.to_thread(self.namespace_evidence) != namespace:
                raise StorageAuthorityError("preserved_state_changed")
            record = {**record, "stage": "validated"}
            await asyncio.to_thread(self.journal.write, record)
            fenced = await asyncio.to_thread(source.set_role, record["transition_id"], snapshot.source, "fenced")
            record = {**record, "stage": "source_fenced"}
            await asyncio.to_thread(self.journal.write, record)
            record = {**record, "stage": "committed", "authority_instance_id": destination_evidence.instance_id}
            await asyncio.to_thread(self.journal.write, record)
            activated = await asyncio.to_thread(destination.set_role, record["transition_id"], destination_evidence,
                                                "primary", authority_epoch=record["target_epoch"])
            if activate is not None:
                await _invoke(activate, destination, record)
            if await asyncio.to_thread(self.namespace_evidence) != namespace:
                raise StorageAuthorityError("preserved_state_changed")
            record = {**record, "stage": "active", "destination": evidence_record(activated)}
            await asyncio.to_thread(self.journal.write, record)
            try:
                await asyncio.to_thread(source.set_role, record["transition_id"], fenced, "retired")
                source_retired = True
            except Exception:
                # The durable source fence remains sufficient for authority.
                # Native retirement cleanup cannot undo committed destination.
                source_retired = False
            pending.update(record=record, fenced=fenced, source_retired=source_retired)

        generation = await self.runtime.transition(prepare, commit=commit, plugin_id=destination_plugin_id,
                                                   drain_timeout=drain_timeout)
        # Authority has already committed and resumed. Cleanup cannot undo it.
        try:
            record, source = pending["record"], pending["source"]
            if not pending["source_retired"]:
                raise StorageAuthorityError("cleanup_failed")
            await asyncio.to_thread(source.close)
            await asyncio.to_thread(self.journal.write, {**record, "stage": "retired"})
        except Exception:
            self._cleanup_errors.append({"code": "cleanup_failed", "plugin_id": source_plugin_id})
        self._pending = None
        return generation

    async def recover(self):
        """Resolve an interrupted in-process handover using durable evidence."""
        return await self._owned(self._serialized_recover())

    async def _serialized_recover(self):
        async with self._serial:
            descriptor = await asyncio.to_thread(self.journal.acquire_writer)
            try:
                return await self._recover()
            finally:
                await asyncio.to_thread(self.journal.release_writer, descriptor)

    async def _recover(self):
        pending = self._pending
        if not pending:
            raise StorageAuthorityError("recovery_required")
        record = await asyncio.to_thread(self.journal.read)
        new_journal = record is not None and record["transition_id"] == pending["transition_id"]
        if not new_journal:
            side = "active"
        else:
            side = "candidate" if record["authority_instance_id"] == pending["initial_destination"].instance_id else "active"

        async def validate(authority, active, candidate):
            await asyncio.to_thread(pending["destination"].reopen, pending["reopen_destination"])
            if not new_journal:
                current = await asyncio.to_thread(pending["source"].commit_evidence)
                destination = await asyncio.to_thread(pending["destination"].commit_evidence)
                return (current.role == "primary" and destination.role == ("copy" if pending["promotion"] else "staged")
                        and current.instance_id == pending["source_evidence"].instance_id
                        and (record is None or record["authority_instance_id"] == current.instance_id))
            before_namespace = await asyncio.to_thread(self.namespace_evidence)
            chosen = await asyncio.to_thread(_resolve_authority, self.journal, pending["source"],
                                              pending["destination"], await asyncio.to_thread(self.namespace_evidence))
            if chosen == "destination" and pending["activate"] is not None:
                await _invoke(pending["activate"], pending["destination"], await asyncio.to_thread(self.journal.read))
            if chosen == "destination":
                if await asyncio.to_thread(self.namespace_evidence) != before_namespace:
                    raise StorageAuthorityError("preserved_state_changed")
                try:
                    source_evidence = await asyncio.to_thread(pending["source"].commit_evidence)
                    if source_evidence.role == "fenced":
                        await asyncio.to_thread(pending["source"].set_role, record["transition_id"], source_evidence, "retired")
                    pending["source_retired"] = True
                except Exception:
                    pending["source_retired"] = False
            return chosen == ("destination" if authority == "candidate" else "source")

        result = await self.runtime.recover(side, validate)
        if side == "candidate":
            try:
                if not pending.get("source_retired"):
                    raise StorageAuthorityError("cleanup_failed")
                await asyncio.to_thread(pending["source"].close)
                completed = await asyncio.to_thread(self.journal.read)
                await asyncio.to_thread(self.journal.write, {**completed, "stage": "retired"})
            except Exception:
                self._cleanup_errors.append({"code": "cleanup_failed", "plugin_id": record["source_plugin_id"]})
        self._pending = None
        return result
