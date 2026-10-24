"""Host budget callbacks backed by complete authoritative namespace receipts."""

import sys
from contextlib import contextmanager
from dataclasses import replace
from types import MappingProxyType
from uuid import uuid4

from whab.plugins.effect_store import EffectError, decode, encode
from whab.plugins.namespace_effects import (
    NamespaceJournal,
    NamespaceOperation,
    NamespaceScope,
    NamespaceSnapshot,
)


class NamespaceNotifications:
    """Nested callback batches never notify a copy observer about partial files."""

    def __init__(self, lock, notify):
        self.lock, self.notify = lock, notify
        self.depth, self.pending, self.failed = 0, False, False
        self._batch_failed = False

    def changed(self):
        with self.lock:
            if self.depth or self.failed:
                self.pending = True
                return
            self._emit()

    def _emit(self):
        try:
            self.notify()
        except Exception:
            self.pending = True  # Copy failure cannot undo a primary effect.

    @contextmanager
    def materialization(self):
        with self.lock:
            outer = self.depth == 0
            if outer:
                self._batch_failed = False
            self.depth += 1
            try:
                yield
            except BaseException:
                self.failed = self._batch_failed = True
                raise
            finally:
                self.depth -= 1
                if outer:
                    self.failed = self._batch_failed
                    if not self.failed and self.pending:
                        self.pending = False
                        self._emit()
                    elif self.failed and sys.exception() is None:
                        raise EffectError("materialization_pending")


class NamespaceEffects:
    """A stable budget-owned journal, independent of replaceable engine instances."""

    def __init__(self, root, scope, context_factory, files, *, lock, materialization):
        if type(scope) is not NamespaceScope:
            raise EffectError("invalid_effect")
        self.scope, self.context_factory, self.files = scope, context_factory, files
        self.lock, self.materialization = lock, materialization
        self.pending = None
        with lock:
            self.journal = NamespaceJournal(root, scope, self.native_snapshot())
            try:
                self.recover()
            except BaseException:
                self.journal.close()
                raise

    def native_snapshot(self):
        context = self.context_factory()
        if context.budget_id != self.scope.budget_id:
            raise EffectError("invalid_effect")
        return NamespaceSnapshot(dict(context.state), dict(self.files()))

    @property
    def snapshot(self):
        with self.lock:
            return self.journal.snapshot

    def recover(self):
        """Explicit drained/startup recovery returns the original pending result."""
        with self.lock, self.materialization():
            self.journal.recover(self.context_factory(), self.native_snapshot)
            result = None
            if self.pending is not None:
                known = self.journal.lookup(self.pending)
                if known is not None:
                    result = decode(known.receipt.result, 64 * 1024)
                self.pending = None
            return result

    def execute(self, name, payload, invoke, *, mutating):
        """Invoke a staged computation once, then publish one complete effect."""
        with self.lock:
            if self.pending is not None or not self.journal.materialized:
                raise EffectError("materialization_pending")
            before = self.journal.snapshot
            operation = NamespaceOperation(uuid4().hex, name, self.journal.generation, before.digest,
                                           encode(payload, 8 * 1024 * 1024))
            known = self.journal.lookup(operation)
            if known is not None:
                return decode(known.receipt.result, 64 * 1024)
            snapshot, result = invoke(before)
            if type(snapshot) is not NamespaceSnapshot:
                raise EffectError("invalid_effect")
            encoded = encode(result, 64 * 1024 if mutating else 1024 * 1024)
            if not mutating:
                if snapshot.digest != before.digest:
                    raise EffectError("read_only_effect")
                return decode(encoded, 1024 * 1024)
            self.pending = operation
            try:
                with self.materialization():
                    self.journal.commit(operation, snapshot, encoded)
                    self.journal.materialize(self.context_factory(), self.native_snapshot)
            except EffectError as error:
                if error.code == "effect_commit_failed":
                    self.pending = None
                raise
            self.pending = None
            return decode(encoded, 64 * 1024)

    def context(self, *, writable):
        return NamespaceAccess(self, writable=writable)

    def close(self):
        self.journal.close()


class NamespaceAccess:
    """Revocable public callbacks, journalled independently of engine transport."""

    def __init__(self, effects, *, writable):
        self.effects, self.writable, self.closed = effects, writable, False

    def _check(self, write=False):
        if self.closed:
            raise EffectError("effect_closed")
        if write and not self.writable:
            raise EffectError("read_only_effect")

    def context(self):
        with self.effects.lock:
            self._check()
            base = self.effects.context_factory()
            return replace(base, state=MappingProxyType(self.effects.snapshot.state),
                           save_state=self.save, read_file=self.read, write_file=self.write, delete_file=self.delete)

    def read(self, name):
        with self.effects.lock:
            self._check()
            # Validate names even for absent reads, without exposing other files.
            from whab.plugins.namespace_effects import _file_name
            return self.effects.snapshot.files.get(_file_name(name))

    def save(self, state):
        with self.effects.lock:
            self._check(True)
            return self.effects.execute("host-state", {"state": state},
                lambda before: (NamespaceSnapshot(state, dict(before.files)), None), mutating=True)

    def write(self, name, raw):
        with self.effects.lock:
            self._check(True)
            if type(raw) is not bytes:
                raise EffectError("invalid_effect")
            from whab.plugins.effect_store import digest
            return self.effects.execute("host-file-write", {"name": name, "sha256": digest(raw)},
                lambda before: (NamespaceSnapshot(before.state, {**before.files, name: raw}), None), mutating=True)

    def delete(self, name):
        with self.effects.lock:
            self._check(True)
            from whab.plugins.namespace_effects import _file_name
            _file_name(name)
            return self.effects.execute("host-file-delete", {"name": name},
                lambda before: (NamespaceSnapshot(before.state, {key: raw for key, raw in before.files.items() if key != name}), None), mutating=True)

    def activate(self):
        with self.effects.lock:
            self._check()
            self.writable = True

    def close(self):
        with self.effects.lock:
            self.closed = True
