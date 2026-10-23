"""Host-owned, complete namespace effects with durable, replayable receipts.

This private journal contains actual model/state payloads. It is NOT the
payload-free storage-authority journal and is never portable or diagnostic data.
Workers receive detached bytes and identities, never this journal or callbacks.
"""

import base64
import re
from dataclasses import dataclass, field, replace
from threading import RLock
from types import MappingProxyType

from whab.budgets.container import MAX_CONTAINER_FILES, validate_file_name
from whab.budgets.document import MAX_BUNDLE_BYTES, portable_values
from whab.plugins.sdk import BudgetContext, valid_plugin_name

from .effect_store import EffectError, EffectStore, decode, digest, encode, invalid

MAX_STATE_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_EFFECT_INPUT = 8 * 1024 * 1024
MAX_EFFECT_RECEIPTS = 256
MAX_JOURNAL_BYTES = 100 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        invalid()
    return value


def hash_value(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        invalid()
    return value


def generation(value):
    if type(value) is not int or not 0 <= value < 2**53 - 1:
        invalid()
    return value


def _file_name(value):
    if type(value) is not str or value in {"settings.json", "state.json"}:
        invalid()
    try:
        validate_file_name("plugins/namespace/" + value)
        value.encode("utf-8")
    except Exception:
        invalid()
    return value


def _binary(value, limit):
    if type(value) is not bytes or len(value) > limit:
        invalid()
    return value


def _base64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unbase64(value, limit):
    try:
        if type(value) is not str or len(value) > (limit + 2) // 3 * 4:
            invalid()
        raw = base64.b64decode(value, validate=True)
        if _base64(raw) != value:
            invalid()
        return _binary(raw, limit)
    except Exception:
        invalid()


@dataclass(frozen=True, slots=True)
class NamespaceScope:
    budget_id: str
    environment: str
    plugin_id: str

    def __post_init__(self):
        if type(self.budget_id) is not str or not self.budget_id or len(self.budget_id) > 200:
            invalid()
        if not valid_plugin_name(self.plugin_id) or type(self.environment) is not str or self.environment not in {"sandbox", "production"}:
            invalid()
        encode(self.record(), 4096)

    def record(self):
        return {"budget": self.budget_id, "environment": self.environment, "plugin": self.plugin_id}


@dataclass(frozen=True, slots=True, init=False)
class NamespaceSnapshot:
    state_json: bytes = field(repr=False)
    files: object = field(repr=False)

    def __init__(self, state, files):
        try:
            if type(state) is not dict or type(files) is not dict or len(files) > MAX_CONTAINER_FILES:
                invalid()
            state_json = encode(portable_values(state), MAX_STATE_BYTES)
            copied = {_file_name(name): _binary(raw, MAX_BUNDLE_BYTES) for name, raw in files.items()}
            if len(state_json) + sum(map(len, copied.values())) > MAX_BUNDLE_BYTES:
                invalid()
            object.__setattr__(self, "state_json", state_json)
            object.__setattr__(self, "files", MappingProxyType(copied))
        except Exception:
            invalid()

    @property
    def state(self):
        return decode(self.state_json, MAX_STATE_BYTES)

    @property
    def digest(self):
        # Content hashes avoid serializing large binary files merely to compare
        # a namespace; exact filenames, lengths and every byte remain bound.
        return digest(encode({"state": self.state, "files": [
            {"name": name, "size": len(raw), "sha256": digest(raw)}
            for name, raw in sorted(self.files.items())
        ]}, MAX_STATE_BYTES + 1024 * MAX_CONTAINER_FILES))

    def record(self):
        return {"state": self.state, "files": {name: _base64(raw) for name, raw in self.files.items()}}

    @classmethod
    def read(cls, value):
        if type(value) is not dict or value.keys() != {"state", "files"} or type(value["files"]) is not dict:
            invalid()
        if len(value["files"]) > MAX_CONTAINER_FILES:
            invalid()
        return cls(value["state"], {name: _unbase64(raw, MAX_BUNDLE_BYTES) for name, raw in value["files"].items()})


@dataclass(frozen=True, slots=True)
class NamespaceOperation:
    operation_id: str
    name: str
    expected_generation: int
    expected_digest: str
    input: bytes = field(repr=False)

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.name)
        generation(self.expected_generation)
        hash_value(self.expected_digest)
        _binary(self.input, MAX_EFFECT_INPUT)

    @property
    def fingerprint(self):
        return digest(encode({"name": self.name, "generation": self.expected_generation,
                              "digest": self.expected_digest, "input": digest(self.input)}, 4096))


@dataclass(frozen=True, slots=True)
class NamespaceReceipt:
    operation_id: str
    fingerprint: str
    generation: int
    before_digest: str
    after_digest: str
    result: bytes = field(repr=False)

    def __post_init__(self):
        identifier(self.operation_id)
        hash_value(self.fingerprint)
        if generation(self.generation) == 0:
            invalid()
        hash_value(self.before_digest)
        hash_value(self.after_digest)
        _binary(self.result, MAX_RESULT_BYTES)

    def record(self):
        return {"operation": self.operation_id, "fingerprint": self.fingerprint, "generation": self.generation,
                "before": self.before_digest, "after": self.after_digest, "result": _base64(self.result)}

    @classmethod
    def read(cls, value):
        if type(value) is not dict or value.keys() != {"operation", "fingerprint", "generation", "before", "after", "result"}:
            invalid()
        return cls(value["operation"], value["fingerprint"], value["generation"], value["before"], value["after"],
                   _unbase64(value["result"], MAX_RESULT_BYTES))


@dataclass(frozen=True, slots=True)
class NamespaceResult:
    receipt: NamespaceReceipt
    replayed: bool


def _validate(body):
    if type(body) is not dict or body.keys() != {"generation", "materialized", "namespace", "receipts"}:
        invalid()
    current = generation(body["generation"])
    if generation(body["materialized"]) > current:
        invalid()
    snapshot = NamespaceSnapshot.read(body["namespace"])
    if type(body["receipts"]) is not list or len(body["receipts"]) > MAX_EFFECT_RECEIPTS:
        invalid()
    receipts = tuple(NamespaceReceipt.read(row) for row in body["receipts"])
    if len({row.operation_id for row in receipts}) != len(receipts):
        invalid()
    if current == 0:
        if receipts:
            invalid()
    elif not receipts or receipts[-1].generation != current or receipts[-1].after_digest != snapshot.digest:
        invalid()
    for before, after in zip(receipts, receipts[1:]):
        if after.generation != before.generation + 1 or after.before_digest != before.after_digest:
            invalid()


class NamespaceJournal:
    """One atomic state/files/result receipt decision, followed by materialization.

    The host must hold its budget mutation/admission barrier while recovering or
    materializing. Existing BudgetContext writes are individually durable only:
    the journal remains authoritative until the whole namespace verifies. Never
    activate a worker or publish a copied namespace before ``materialize`` returns.
    """

    def __init__(self, root, scope, initial):
        if type(scope) is not NamespaceScope or type(initial) is not NamespaceSnapshot:
            invalid()
        self.scope = scope
        self._verified_generation = None
        self.store = EffectStore(root, "namespace-effects.json", scope.record(),
                                 {"generation": 0, "materialized": 0, "namespace": initial.record(), "receipts": []},
                                 _validate, limit=MAX_JOURNAL_BYTES)

    @property
    def snapshot(self):
        return NamespaceSnapshot.read(self.store.snapshot()["namespace"])

    @property
    def generation(self):
        return self.store.snapshot()["generation"]

    @property
    def materialized(self):
        body = self.store.snapshot()
        return body["generation"] == body["materialized"] == self._verified_generation

    @staticmethod
    def _previous(body, operation):
        if type(operation) is not NamespaceOperation:
            invalid()
        for row in body["receipts"]:
            if row["operation"] == operation.operation_id:
                receipt = NamespaceReceipt.read(row)
                if receipt.fingerprint != operation.fingerprint:
                    raise EffectError("effect_conflict")
                return NamespaceResult(receipt, True)
        if body["generation"] != operation.expected_generation or NamespaceSnapshot.read(body["namespace"]).digest != operation.expected_digest:
            raise EffectError("effect_conflict")
        return None

    def lookup(self, operation):
        """Reconcile before execution. A pruned identity with stale CAS rejects."""
        return self._previous(self.store.snapshot(), operation)

    def commit(self, operation, snapshot, result):
        if type(snapshot) is not NamespaceSnapshot:
            invalid()
        _binary(result, MAX_RESULT_BYTES)
        with self.store.lock:
            body = self.store.snapshot()
            previous = self._previous(body, operation)
            if previous is not None:
                return previous
            receipt = NamespaceReceipt(operation.operation_id, operation.fingerprint, body["generation"] + 1,
                                       operation.expected_digest, snapshot.digest, result)
            candidate = {**body, "generation": receipt.generation, "namespace": snapshot.record(),
                         "receipts": (body["receipts"] + [receipt.record()])[-MAX_EFFECT_RECEIPTS:]}
            self.store.commit(candidate)
            self._verified_generation = None
            return NamespaceResult(receipt, False)

    def materialize(self, context, read_snapshot):
        """Recover full native files/state idempotently, retaining original receipt."""
        if type(context) is not BudgetContext or context.budget_id != self.scope.budget_id:
            invalid()
        if not all(callable(callback) for callback in (context.read_file, context.write_file, context.delete_file, read_snapshot)):
            invalid()
        with self.store.lock:
            self._verified_generation = None
            body = self.store.snapshot()
            wanted = NamespaceSnapshot.read(body["namespace"])
            try:
                actual = read_snapshot()
                if type(actual) is not NamespaceSnapshot:
                    invalid()
                for name, raw in wanted.files.items():
                    if actual.files.get(name) != raw:
                        context.write_file(name, raw)
                for name in actual.files:
                    if name not in wanted.files:
                        context.delete_file(name)
                if actual.state_json != wanted.state_json:
                    context.save_state(wanted.state)
                confirmed = read_snapshot()
                if type(confirmed) is not NamespaceSnapshot or confirmed.digest != wanted.digest:
                    invalid()
            except Exception:
                raise EffectError("materialization_pending") from None
            # Metadata-only publication never changes namespace generation or its
            # original operation/result receipt, including after interrupted save.
            if body["materialized"] != body["generation"]:
                self.store.commit({**body, "materialized": body["generation"]})
            self._verified_generation = body["generation"]
            return wanted

    def recover(self, context, read_snapshot):
        self.store.recover()
        return self.materialize(context, read_snapshot)

    def close(self):
        self.store.close()


class NamespaceDraft:
    """Revocable public BudgetContext callbacks for one detached worker command."""

    def __init__(self, base, snapshot, *, writable=False):
        if type(base) is not BudgetContext or type(snapshot) is not NamespaceSnapshot or type(writable) is not bool:
            invalid()
        self.base, self._snapshot, self.writable, self.closed = base, snapshot, writable, False
        self.lock = RLock()

    def _check(self, write=False):
        if self.closed:
            raise EffectError("effect_closed")
        if write and not self.writable:
            raise EffectError("read_only_effect")

    def _save(self, state):
        with self.lock:
            self._check(True)
            self._snapshot = NamespaceSnapshot(state, dict(self._snapshot.files))

    def _read(self, name):
        with self.lock:
            self._check()
            return self._snapshot.files.get(_file_name(name))

    def _write(self, name, raw):
        with self.lock:
            self._check(True)
            self._snapshot = NamespaceSnapshot(self._snapshot.state, {**self._snapshot.files, _file_name(name): raw})

    def _delete(self, name):
        with self.lock:
            self._check(True)
            _file_name(name)
            self._snapshot = NamespaceSnapshot(self._snapshot.state, {key: raw for key, raw in self._snapshot.files.items() if key != name})

    def context(self):
        with self.lock:
            self._check()
            return replace(self.base, settings=MappingProxyType(portable_values(dict(self.base.settings))),
                           state=MappingProxyType(self._snapshot.state), save_state=self._save,
                           read_file=self._read, write_file=self._write, delete_file=self._delete)

    @property
    def snapshot(self):
        with self.lock:
            self._check()
            return self._snapshot

    def close(self):
        with self.lock:
            self.closed = True
