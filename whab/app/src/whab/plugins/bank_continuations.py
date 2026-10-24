"""Opaque host bank continuations and irreversible-exchange recovery.

No provider is called by recovery. Intent is durable before external HTTP;
authenticated encrypted response capture precedes native storage apply. Missing
capture after an intent is uncertain, including a process death before HTTP.
Keys and the seal/open callbacks stay in the host, outside this private store.
"""

import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

from .effect_store import EffectError, EffectStore, digest, encode, invalid
from .namespace_effects import (
    NamespaceScope,
    _base64,
    _binary,
    _unbase64,
    generation,
    hash_value,
    identifier,
)

MAX_CONTINUATIONS = 128
MAX_SECRET_BYTES = 64 * 1024
MAX_CIPHERTEXT_BYTES = MAX_SECRET_BYTES + 4096
MAX_TTL_SECONDS = 3600
MAX_CONTINUATION_BYTES = 40 * 1024 * 1024
_OPAQUE = re.compile(r"[a-f0-9]{32}\Z")
_STAGES = frozenset({"ready", "exchanging", "exchange_uncertain", "captured", "applying", "apply_uncertain", "complete", "cancelled", "expired"})
_TERMINAL = frozenset({"complete", "cancelled", "expired"})


@dataclass(frozen=True, slots=True)
class ContinuationScope:
    budget_id: str
    environment: str
    provider_id: str
    generation: int

    def __post_init__(self):
        NamespaceScope(self.budget_id, self.environment, self.provider_id)
        generation(self.generation)

    def record(self):
        return NamespaceScope(self.budget_id, self.environment, self.provider_id).record()


@dataclass(frozen=True, slots=True)
class ContinuationStatus:
    identifier: str
    generation: int
    kind: str
    stage: str
    deadline_ms: int


@dataclass(frozen=True, slots=True)
class NativeApplyEvidence:
    """Host assertion backed by atomic native operation receipts/CAS evidence.

    Absence of a receipt alone means ``unknown``; ``not_committed`` requires
    proof that the expected native generation still permits this exact command.
    """

    operation_id: str
    fingerprint: str
    outcome: str
    result: bytes = field(default=b"", repr=False)

    def __post_init__(self):
        identifier(self.operation_id)
        hash_value(self.fingerprint)
        if type(self.outcome) is not str or self.outcome not in {"committed", "not_committed", "unknown"}:
            invalid()
        _binary(self.result, MAX_SECRET_BYTES)
        if self.outcome != "committed" and self.result:
            invalid()


def _format(body):
    if type(body) is not dict:
        invalid()
    if body.keys() == {"records"}:
        return 1
    if body.keys() != {"formatVersion", "records"} or type(body["formatVersion"]) is not int or body["formatVersion"] != 2:
        invalid()
    return 2


def _validate(body):
    version = _format(body)
    if type(body["records"]) is not list or len(body["records"]) > MAX_CONTINUATIONS:
        invalid()
    ids, operations = set(), set()
    for row in body["records"]:
        fields = {"id", "generation", "kind", "created", "deadline", "stage", "input", "response", "result", "operation", "fingerprint", "native_fingerprint"}
        if version == 2:
            fields.add("native_request")
        if type(row) is not dict or row.keys() != fields:
            invalid()
        if type(row["id"]) is not str or _OPAQUE.fullmatch(row["id"]) is None or row["id"] in ids:
            invalid()
        ids.add(row["id"])
        generation(row["generation"])
        if type(row["kind"]) is not str or row["kind"] not in {"link", "update"} or type(row["stage"]) is not str or row["stage"] not in _STAGES:
            invalid()
        created, deadline = generation(row["created"]), generation(row["deadline"])
        if not created <= deadline <= created + MAX_TTL_SECONDS * 1000:
            invalid()
        for name in ("input", "response", "result", *(('native_request',) if version == 2 else ())):
            if row[name] is not None:
                cipher = _unbase64(row[name], MAX_CIPHERTEXT_BYTES)
                if len(cipher) < 16:
                    invalid()
        if row["input"] is None:
            invalid()
        if (row["operation"] is None) != (row["fingerprint"] is None):
            invalid()
        if row["operation"] is not None:
            identifier(row["operation"])
            hash_value(row["fingerprint"])
            if row["operation"] in operations:
                invalid()
            operations.add(row["operation"])
        if row["stage"] in {"exchanging", "exchange_uncertain", "captured", "applying", "apply_uncertain", "complete"} and row["operation"] is None:
            invalid()
        if (row["stage"] in {"captured", "applying", "apply_uncertain", "complete"}) != (row["response"] is not None):
            invalid()
        if (row["stage"] == "complete") != (row["result"] is not None):
            invalid()
        if row["native_fingerprint"] is not None:
            hash_value(row["native_fingerprint"])
        if row["stage"] in {"applying", "apply_uncertain", "complete"} and row["native_fingerprint"] is None:
            invalid()
        if version == 2 and row["native_request"] is not None and (
            row["native_fingerprint"] is None or row["stage"] not in {"captured", "applying", "apply_uncertain", "complete"}
        ):
            invalid()


class BankContinuations:
    """One budget/environment/provider's private generation-scoped continuations.

    The host's ``seal(raw, aad)`` / ``open(ciphertext, aad)`` MUST provide
    authenticated encryption with the supplied scope/identity/stage AAD. These
    callbacks and native receipt/apply callbacks never cross worker IPC.
    """

    def __init__(self, root, budget_id, environment, provider_id, *, seal, open, clock=time.time):
        self.scope = NamespaceScope(budget_id, environment, provider_id)
        if not all(callable(callback) for callback in (seal, open, clock)):
            invalid()
        self.seal, self.open, self.clock = seal, open, clock
        self._running = set()
        self.store = EffectStore(root, "bank-continuations.json", self.scope.record(), {"formatVersion": 2, "records": []},
                                 _validate, limit=MAX_CONTINUATION_BYTES)
        try:
            self._authenticate()
            self._upgrade()
            self._recover_stages()
        except BaseException:
            self.store.close()
            raise

    def _now(self):
        try:
            value = self.clock()
            if type(value) not in {int, float} or not 0 <= value < (2**53 - 1) / 1000:
                invalid()
            return int(value * 1000)
        except Exception:
            raise EffectError("effect_unavailable") from None

    def _scope(self, scope):
        if type(scope) is not ContinuationScope or scope.record() != self.scope.record():
            raise EffectError("continuation_missing")

    def _row(self, body, scope, token):
        self._scope(scope)
        if type(token) is not str or _OPAQUE.fullmatch(token) is None:
            raise EffectError("continuation_missing")
        for row in body["records"]:
            if row["id"] == token and row["generation"] == scope.generation:
                return row
        raise EffectError("continuation_missing")

    @staticmethod
    def _status(row):
        return ContinuationStatus(row["id"], row["generation"], row["kind"], row["stage"], row["deadline"])

    def _aad(self, row, name):
        return encode({"scope": self.scope.record(), "generation": row["generation"], "id": row["id"], "stage": name}, 4096)

    def _seal(self, row, name, raw):
        try:
            _binary(raw, MAX_SECRET_BYTES)
            cipher = self.seal(raw, self._aad(row, name))
            _binary(cipher, MAX_CIPHERTEXT_BYTES)
            if len(cipher) < len(raw) + 16:
                invalid()
            return _base64(cipher)
        except Exception:
            raise EffectError("effect_unavailable") from None

    def _open(self, row, name):
        try:
            raw = self.open(_unbase64(row[name], MAX_CIPHERTEXT_BYTES), self._aad(row, name))
            return _binary(raw, MAX_SECRET_BYTES)
        except Exception:
            raise EffectError("effect_unavailable") from None

    def _upgrade(self):
        """Explicit v1 reader/upgrader: preserve original encrypted captures.

        The EffectStore publication is atomic. Failure leaves either exact v1 or
        complete v2 authoritative, with ordinary uncertain-publication fencing.
        The outer private EffectStore envelope retains its own format version.
        """
        body = self.store.snapshot()
        if _format(body) == 1:
            self.store.commit({"formatVersion": 2, "records": [
                {**row, "native_request": None} for row in body["records"]]})

    def _authenticate(self):
        # Validate every retained field before admitting any external operation.
        # No decrypted payload is cached or included in diagnostic records.
        body = self.store.snapshot()
        for row in body["records"]:
            for name in ("input", "response", "result", *(('native_request',) if _format(body) == 2 else ())):
                if row[name] is not None:
                    self._open(row, name)

    def _recover_stages(self):
        with self.store.lock:
            if self._running:
                raise EffectError("continuation_busy")
            body, changed, now = self.store.snapshot(), False, self._now()
            for row in body["records"]:
                target = {"exchanging": "exchange_uncertain", "applying": "apply_uncertain"}.get(row["stage"])
                if row["stage"] == "ready" and row["deadline"] <= now:
                    target = "expired"
                if target is not None:
                    row["stage"], changed = target, True
            if changed:
                self.store.commit(body)

    def begin(self, scope, kind, *, secret=b"", ttl=900):
        self._scope(scope)
        if type(kind) is not str or kind not in {"link", "update"} or type(ttl) is not int or not 1 <= ttl <= MAX_TTL_SECONDS:
            invalid()
        with self.store.lock:
            body, now = self.store.snapshot(), self._now()
            if len(body["records"]) >= MAX_CONTINUATIONS:
                terminal = next((row for row in body["records"] if row["stage"] in _TERMINAL), None)
                if terminal is None:
                    raise EffectError("continuation_busy")
                body["records"].remove(terminal)
            row = {"id": uuid4().hex, "generation": scope.generation, "kind": kind, "created": now,
                   "deadline": now + ttl * 1000, "stage": "ready", "input": None, "response": None,
                   "result": None, "operation": None, "fingerprint": None, "native_fingerprint": None,
                   "native_request": None}
            row["input"] = self._seal(row, "input", secret)
            body["records"].append(row)
            self.store.commit(body)
            return self._status(row)

    def status(self, scope, token):
        with self.store.lock:
            body = self.store.snapshot()
            row = self._row(body, scope, token)
            if row["stage"] == "ready" and row["deadline"] <= self._now():
                row["stage"] = "expired"
                self.store.commit(body)
            return self._status(row)

    def secret(self, scope, token):
        """Trusted host retrieval only; never include this in status/diagnostics."""
        with self.store.lock:
            status = self.status(scope, token)
            if status.stage != "ready":
                raise EffectError("continuation_expired" if status.stage == "expired" else "continuation_busy")
            return self._open(self._row(self.store.snapshot(), scope, token), "input")

    def statuses(self):
        """Bounded opaque host discovery; no provider payload or secret fields."""
        with self.store.lock:
            return tuple(self._status(row) for row in self.store.snapshot()["records"])

    def scope_for(self, token):
        """Resolve the original authenticated generation after unrelated handovers."""
        if type(token) is not str or _OPAQUE.fullmatch(token) is None:
            raise EffectError("continuation_missing")
        with self.store.lock:
            for row in self.store.snapshot()["records"]:
                if row["id"] == token:
                    return ContinuationScope(self.scope.budget_id, self.scope.environment, self.scope.plugin_id, row["generation"])
        raise EffectError("continuation_missing")

    def captured_response(self, scope, token):
        with self.store.lock:
            row = self._row(self.store.snapshot(), scope, token)
            if row["response"] is None:
                raise EffectError("exchange_uncertain")
            return self._open(row, "response")

    def exchange_request(self, scope, token):
        with self.store.lock:
            row = self._row(self.store.snapshot(), scope, token)
            if row["operation"] is None:
                raise EffectError("continuation_busy")
            return self._open(row, "input")

    def prepare_native(self, scope, token, fingerprint, request):
        """Durably bind exact host-native command bytes before any dispatch.

        The caller validates command/provider scope and its public fingerprint.
        This record owns recovery input; the native receipt alone decides commit.
        """
        hash_value(fingerprint)
        _binary(request, MAX_SECRET_BYTES)
        with self.store.lock:
            body = self.store.snapshot()
            row = self._row(body, scope, token)
            if row["native_request"] is not None:
                if row["native_fingerprint"] != fingerprint or self._open(row, "native_request") != request:
                    raise EffectError("effect_conflict")
                return request
            if row["stage"] != "captured" or row["native_fingerprint"] is not None:
                raise EffectError("continuation_busy")
            row.update(native_request=self._seal(row, "native_request", request), native_fingerprint=fingerprint)
            self.store.commit(body)
            return request

    def native_request(self, scope, token):
        with self.store.lock:
            row = self._row(self.store.snapshot(), scope, token)
            if row["native_request"] is None:
                return None
            return row["native_fingerprint"], self._open(row, "native_request")

    def cancel(self, scope, token):
        with self.store.lock:
            body = self.store.snapshot()
            row = self._row(body, scope, token)
            if row["stage"] not in {"ready", "cancelled", "expired"}:
                raise EffectError("continuation_busy")
            if row["stage"] == "ready":
                row["stage"] = "cancelled"
                self.store.commit(body)
            return self._status(row)

    def _uncertain(self, scope, token, stage):
        body = self.store.snapshot()
        self._row(body, scope, token)["stage"] = stage
        self.store.commit(body)

    def exchange(self, scope, token, operation_id, request, send):
        """Invoke external HTTP at most once per durable continuation intent.

        ``send`` is host-owned and returns response bytes; all persisted inputs
        and responses are encrypted. A retry inspects status and never calls send.
        """
        identifier(operation_id)
        _binary(request, MAX_SECRET_BYTES)
        if not callable(send):
            invalid()
        with self.store.lock:
            body = self.store.snapshot()
            row = self._row(body, scope, token)
            fingerprint = digest(encode({"scope": self.scope.record(), "generation": scope.generation,
                                         "id": token, "request": digest(request)}, 4096))
            if row["operation"] is not None:
                if row["operation"] != operation_id or row["fingerprint"] != fingerprint:
                    raise EffectError("effect_conflict")
                if row["stage"] in {"exchanging", "exchange_uncertain"}:
                    raise EffectError("exchange_uncertain")
                return self._status(row)
            if row["stage"] != "ready" or row["deadline"] <= self._now():
                raise EffectError("continuation_expired" if row["deadline"] <= self._now() else "continuation_busy")
            if any(previous["operation"] == operation_id for previous in body["records"]):
                raise EffectError("effect_conflict")
            row.update(stage="exchanging", operation=operation_id, fingerprint=fingerprint,
                       input=self._seal(row, "input", request))
            # Acknowledged durable intent ALWAYS precedes the external call.
            self.store.commit(body)
            self._running.add(token)
        try:
            response = send(request)
            with self.store.lock:
                body = self.store.snapshot()
                row = self._row(body, scope, token)
                row.update(stage="captured", response=self._seal(row, "response", response))
                self.store.commit(body)
                return self._status(row)
        except BaseException as error:
            try:
                with self.store.lock:
                    self._uncertain(scope, token, "exchange_uncertain")
            except Exception:
                # An uncertain capture may already be durable. Recovery reads
                # it; neither this path nor reopening repeats provider HTTP.
                pass
            if isinstance(error, Exception):
                raise EffectError("exchange_uncertain") from None
            raise
        finally:
            with self.store.lock:
                self._running.discard(token)

    def reconcile_exchange(self, scope, token, *, response=None, confirmed_no_effect=False):
        """Explicit host/provider verification, never a timeout or automatic retry.

        The host must obtain independent proof before supplying a response or
        asserting no effect. This method performs no HTTP and cannot reconnect.
        """
        if type(confirmed_no_effect) is not bool or (response is None) == (not confirmed_no_effect):
            invalid()
        with self.store.lock:
            body = self.store.snapshot()
            row = self._row(body, scope, token)
            if row["stage"] != "exchange_uncertain":
                raise EffectError("continuation_busy")
            if confirmed_no_effect:
                row["stage"] = "cancelled"
            else:
                row.update(stage="captured", response=self._seal(row, "response", response))
            self.store.commit(body)
            return self._status(row)

    @staticmethod
    def _evidence(value, operation_id, fingerprint):
        if type(value) is not NativeApplyEvidence or value.operation_id != operation_id or value.fingerprint != fingerprint:
            invalid()
        return value

    def apply(self, scope, token, *, native_fingerprint, lookup, commit):
        """Reconcile an exact native command; missing receipt is not rollback proof."""
        hash_value(native_fingerprint)
        if not callable(lookup) or not callable(commit):
            invalid()
        with self.store.lock:
            body = self.store.snapshot()
            row = self._row(body, scope, token)
            if row["stage"] == "complete":
                if row["native_fingerprint"] != native_fingerprint:
                    raise EffectError("effect_conflict")
                return self._open(row, "result")
            if row["stage"] not in {"captured", "apply_uncertain"} or token in self._running:
                raise EffectError("continuation_busy")
            if row["native_fingerprint"] is not None and row["native_fingerprint"] != native_fingerprint:
                raise EffectError("effect_conflict")
            operation_id = row["operation"]
            try:
                evidence = self._evidence(lookup(operation_id, native_fingerprint), operation_id, native_fingerprint)
            except Exception:
                raise EffectError("apply_uncertain") from None
            if evidence.outcome == "unknown":
                raise EffectError("apply_uncertain")
            if evidence.outcome == "not_committed":
                row.update(stage="applying", native_fingerprint=native_fingerprint)
                self.store.commit(body)
            self._running.add(token)
        try:
            if evidence.outcome == "not_committed":
                response = self._open(row, "response")
                evidence = self._evidence(commit(operation_id, native_fingerprint, response), operation_id, native_fingerprint)
                if evidence.outcome != "committed":
                    raise EffectError("apply_uncertain")
            with self.store.lock:
                body = self.store.snapshot()
                row = self._row(body, scope, token)
                row.update(stage="complete", native_fingerprint=native_fingerprint,
                           result=self._seal(row, "result", evidence.result))
                self.store.commit(body)
                return evidence.result
        except BaseException as error:
            try:
                with self.store.lock:
                    body = self.store.snapshot()
                    row = self._row(body, scope, token)
                    row.update(stage="apply_uncertain", native_fingerprint=native_fingerprint)
                    self.store.commit(body)
            except Exception:
                pass
            if isinstance(error, Exception):
                raise EffectError("apply_uncertain") from None
            raise
        finally:
            with self.store.lock:
                self._running.discard(token)

    def prepare_close(self):
        """Guard selection/upgrade while any generation still owns unfinished work."""
        with self.store.lock:
            body, changed, now = self.store.snapshot(), False, self._now()
            for row in body["records"]:
                if row["stage"] == "ready" and row["deadline"] <= now:
                    row["stage"], changed = "expired", True
            if changed:
                self.store.commit(body)
            if self._running or any(row["stage"] not in _TERMINAL for row in body["records"]):
                raise EffectError("continuation_busy")

    def recover(self):
        with self.store.lock:
            if self._running:
                raise EffectError("continuation_busy")
            self.store.recover()
            self._authenticate()
            self._upgrade()
            self._recover_stages()

    def close(self):
        """Host shutdown preserves pending decisions; lifecycle uses prepare_close."""
        with self.store.lock:
            if self._running:
                raise EffectError("continuation_busy")
            self.store.close()
