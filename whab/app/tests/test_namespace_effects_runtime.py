"""Authoritative host effects recover before observers or workers see updates."""

import base64
from dataclasses import replace
from threading import RLock

import pytest

from whab.plugins.effect_store import EffectError, decode
from whab.plugins.namespace_effects import NamespaceScope, NamespaceSnapshot
from whab.services.categorization import local_context, local_files
from whab.services.namespace_effects import NamespaceEffects, NamespaceNotifications


@pytest.fixture
def effects(tmp_path):
    lock = RLock()
    observed = []
    def snapshot():
        context = local_context(tmp_path / "native", "fictional", "categorizer")
        return NamespaceSnapshot(dict(context.state), local_files(tmp_path / "native", "fictional", "categorizer"))
    notifications = NamespaceNotifications(lock, lambda: observed.append(snapshot()))
    def context():
        return local_context(tmp_path / "native", "fictional", "categorizer", lock=lock,
                             on_change=notifications.changed)
    context().write_file("unknown.bin", b"unknown synthetic data")
    observed.clear()
    manager = NamespaceEffects(tmp_path / "journal", NamespaceScope("fictional", "sandbox", "categorizer"),
                               context, lambda: dict(snapshot().files), lock=lock,
                               materialization=notifications.materialization)
    try:
        yield manager, context, notifications, observed, snapshot
    finally:
        manager.close()


def test_complete_state_model_index_and_original_result_precede_one_observer(effects):
    manager, _, _, observed, native = effects
    invocations = []
    def save(before):
        invocations.append(True)
        return NamespaceSnapshot({"active": "model"}, {**before.files, "model.bin": b"synthetic model",
                                 "models.json": b'{"model":"model"}'}), {"id": "model"}
    assert manager.execute("save", {"model": "opaque"}, save, mutating=True) == {"id": "model"}
    assert len(invocations) == len(observed) == 1
    assert observed[0].digest == native().digest == manager.snapshot.digest
    record = manager.journal.store.snapshot()
    assert record["generation"] == record["materialized"] == 1
    assert decode(base64.b64decode(record["receipts"][-1]["result"]), 65536) == {"id": "model"}
    assert native().files["unknown.bin"] == b"unknown synthetic data"


def test_partial_materialization_never_notifies_and_recovery_returns_original_result(effects):
    manager, context, notifications, observed, native = effects
    original = manager.snapshot
    base = context()
    def broken(name, raw):
        base.write_file(name, raw)
        if name == "models.json":
            raise OSError("synthetic private disk failure")
    manager.context_factory = lambda: replace(base, write_file=broken)
    calls = []
    def save(before):
        calls.append(True)
        return NamespaceSnapshot({"active": "new"}, {**before.files, "model.bin": b"new model",
                                 "models.json": b"new index"}), {"id": "original-result"}
    with pytest.raises(EffectError) as error:
        manager.execute("save", {}, save, mutating=True)
    assert error.value.code == "materialization_pending"
    assert calls == [True] and not observed and notifications.failed
    assert native().digest != original.digest and native().digest != manager.snapshot.digest
    with pytest.raises(EffectError, match="requires recovery"):
        manager.execute("save", {}, save, mutating=True)
    receipt = manager.journal.store.snapshot()["receipts"][-1]
    manager.context_factory = context
    assert manager.recover() == {"id": "original-result"}
    assert calls == [True] and manager.pending is None and manager.journal.materialized
    assert manager.journal.store.snapshot()["receipts"][-1] == receipt
    assert len(observed) == 1 and observed[0].digest == manager.snapshot.digest == native().digest


@pytest.mark.parametrize("phase", ["before_replace", "after_replace", "after_directory_fsync"])
def test_lost_commit_reply_is_reconciled_without_reinvoking_computation(effects, phase):
    manager, _, _, observed, native = effects
    calls = []
    def fail(boundary):
        if boundary == phase:
            raise OSError("synthetic lost effect reply")
    def save(before):
        calls.append(True)
        return NamespaceSnapshot({}, {**before.files, "model.bin": b"new"}), {"saved": True}
    manager.journal.store.fault = fail
    with pytest.raises(EffectError) as error:
        manager.execute("save", {}, save, mutating=True)
    assert error.value.code == ("effect_commit_failed" if phase == "before_replace" else "effect_commit_uncertain")
    assert not observed and calls == [True]
    manager.journal.store.fault = None
    assert manager.recover() == (None if phase == "before_replace" else {"saved": True})
    assert calls == [True]
    assert ("model.bin" in native().files) is (phase != "before_replace")
    assert manager.journal.materialized


def test_readonly_effect_and_revoked_candidate_cannot_publish(effects):
    manager, _, _, observed, _ = effects
    candidate = manager.context(writable=False)
    with pytest.raises(EffectError) as error:
        candidate.write("candidate.bin", b"candidate")
    assert error.value.code == "read_only_effect"
    with pytest.raises(EffectError) as error:
        manager.execute("index", {}, lambda _: (NamespaceSnapshot({}, {}), []), mutating=False)
    assert error.value.code == "read_only_effect"
    assert manager.journal.generation == 0 and not observed
    candidate.activate()
    candidate.write("active.bin", b"active")
    candidate.context().save_state({"active": True})
    assert manager.snapshot.state == {"active": True}
    candidate.close()
    for callback in (lambda: candidate.read("active.bin"), lambda: candidate.write("late", b"late")):
        with pytest.raises(EffectError) as error:
            callback()
        assert error.value.code == "effect_closed"


def test_notification_batch_caught_nested_failure_is_fenced_until_explicit_recovery(effects):
    manager, context, notifications, observed, _ = effects
    with pytest.raises(EffectError) as error:
        with notifications.materialization():
            try:
                with notifications.materialization():
                    context().write_file("partial.bin", b"partial")
                    raise OSError("fictional interrupted nested effect")
            except OSError:
                pass
    assert error.value.code == "materialization_pending" and notifications.failed and not observed
    context().write_file("still-partial.bin", b"partial")
    assert not observed
    manager.recover()
    assert len(observed) == 1 and "partial.bin" not in manager.snapshot.files


def test_native_state_persists_across_callbacks_and_unknown_files_survive_reopen(effects, tmp_path):
    manager, context, notifications, _, native = effects
    access = manager.context(writable=True)
    access.context().save_state({"nested": {"generation": 1}})
    access.write("demo/model.bin", b"independent demo")
    wanted = manager.snapshot
    manager.close()
    reopened = NamespaceEffects(tmp_path / "journal", manager.scope, context, lambda: dict(native().files),
                                lock=manager.lock, materialization=notifications.materialization)
    try:
        assert reopened.snapshot.digest == wanted.digest == native().digest
        assert reopened.journal.materialized and reopened.snapshot.state == {"nested": {"generation": 1}}
        assert reopened.snapshot.files["unknown.bin"] == b"unknown synthetic data"
    finally:
        reopened.close()
