"""Synthetic whole-namespace effects and interrupted private journal commits."""

import json
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import MappingProxyType

import pytest

from whab.plugins import BudgetContext
from whab.plugins.effect_store import EffectError
from whab.plugins.namespace_effects import (
    NamespaceDraft,
    NamespaceJournal,
    NamespaceOperation,
    NamespaceScope,
    NamespaceSnapshot,
)

SCOPE = NamespaceScope("synthetic-budget", "sandbox", "fictional-categorizer")


def initial():
    return NamespaceSnapshot({"active": "original"}, {"model.bin": b"fictional original model", "unknown.bin": b"preserve unknown bytes"})


def operation(journal, identifier="synthetic-operation"):
    return NamespaceOperation(identifier, "train", journal.generation, journal.snapshot.digest, b"fictional reviewed training input")


class Materialized:
    def __init__(self, snapshot):
        self.state, self.files = snapshot.state, dict(snapshot.files)
        self.calls = []
        self.fail = None

    def context(self):
        def write(name, raw):
            self.calls.append(("write", name))
            self.files[name] = raw
            if self.fail == name:
                raise OSError("fictional private materialization failure")

        def delete(name):
            self.calls.append(("delete", name))
            self.files.pop(name, None)

        def save(state):
            self.calls.append(("state",))
            self.state = state

        return BudgetContext(SCOPE.budget_id, MappingProxyType({"threshold": 0.8}), self.state, save,
                             self.files.get, write, delete)

    def snapshot(self):
        return NamespaceSnapshot(self.state, self.files)


def test_complete_namespace_receipt_and_reopen_are_one_durable_decision(tmp_path):
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    request = operation(journal)
    wanted = NamespaceSnapshot({"active": "trained"}, {**journal.snapshot.files, "demo/model.bin": b"independent demo model"})
    result = journal.commit(request, wanted, b'{"model":"synthetic-model"}')
    assert result.replayed is False and result.receipt.before_digest == initial().digest
    assert result.receipt.after_digest == wanted.digest and result.receipt.generation == 1
    assert not journal.materialized
    assert journal.commit(request, initial(), b"must never replace replay result").receipt == result.receipt
    assert journal.lookup(request).replayed
    materialized = Materialized(initial())
    journal.materialize(materialized.context(), materialized.snapshot)
    assert materialized.snapshot().digest == wanted.digest and journal.materialized
    assert journal.lookup(request).receipt == result.receipt
    journal.close()
    reopened = NamespaceJournal(tmp_path, SCOPE, NamespaceSnapshot({}, {}))
    try:
        assert reopened.snapshot.digest == wanted.digest and reopened.generation == 1
        assert not reopened.materialized  # Restart must verify native callbacks again.
        assert reopened.lookup(request).receipt == result.receipt
        assert (tmp_path / "namespace-effects.json").stat().st_mode & 0o777 == 0o600
    finally:
        reopened.close()


def test_partial_materialization_recovers_same_receipt_before_activation(tmp_path):
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    request = operation(journal)
    wanted = NamespaceSnapshot({"active": "new"}, {"a.bin": b"first", "b.bin": b"second"})
    receipt = journal.commit(request, wanted, b"original result").receipt
    materialized = Materialized(initial())
    materialized.fail = "b.bin"
    with pytest.raises(EffectError, match="requires recovery") as raised:
        journal.materialize(materialized.context(), materialized.snapshot)
    assert raised.value.code == "materialization_pending" and not journal.materialized
    assert materialized.files["a.bin"] == b"first" and materialized.state == initial().state
    assert "private" not in str(raised.value)
    journal.close()
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    try:
        materialized.fail = None
        journal.recover(materialized.context(), materialized.snapshot)
        assert journal.materialized and materialized.snapshot().digest == wanted.digest
        assert set(materialized.files) == {"a.bin", "b.bin"}
        assert journal.lookup(request).receipt == receipt
        # Recovery performs no model execution and need not rewrite completed files.
        materialized.calls.clear()
        journal.materialize(materialized.context(), materialized.snapshot)
        assert not materialized.calls
    finally:
        journal.close()


@pytest.mark.parametrize("phase", ("before_replace", "after_replace", "after_directory_fsync"))
def test_controlled_commit_failures_reconcile_exact_receipt_without_reexecution(tmp_path, phase):
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    request = operation(journal)
    wanted = NamespaceSnapshot({"active": "new"}, {"model.bin": b"synthetic replacement"})

    def fail(boundary):
        if boundary == phase:
            raise OSError("fictional disk or lost-response detail")

    journal.store.fault = fail
    with pytest.raises(EffectError) as raised:
        journal.commit(request, wanted, b"durable original result")
    assert raised.value.code == ("effect_commit_failed" if phase == "before_replace" else "effect_commit_uncertain")
    journal.store.fault = None
    if phase != "before_replace":
        with pytest.raises(EffectError) as fenced:
            journal.lookup(request)
        assert fenced.value.code == "effect_commit_uncertain"
    journal.store.recover()
    if phase == "before_replace":
        assert journal.lookup(request) is None and journal.snapshot.digest == initial().digest
    else:
        assert journal.lookup(request).receipt.result == b"durable original result"
        assert journal.snapshot.digest == wanted.digest
    journal.close()


@pytest.mark.parametrize("phase", ("before_replace", "after_replace", "after_directory_fsync"))
def test_sigkill_commit_boundary_reopens_complete_previous_or_new_namespace(tmp_path, phase):
    program = '''import os,signal,sys
from pathlib import Path
from whab.plugins.namespace_effects import NamespaceJournal,NamespaceScope,NamespaceSnapshot,NamespaceOperation
scope=NamespaceScope('synthetic-budget','sandbox','fictional-categorizer')
before=NamespaceSnapshot({'active':'old'},{'old.bin':b'old'})
journal=NamespaceJournal(Path(sys.argv[1]),scope,before)
operation=NamespaceOperation('kill-operation','train',0,before.digest,b'input')
def fault(boundary):
 if boundary==sys.argv[2]:
  os.write(int(sys.argv[3]),b'B')
  os.kill(os.getpid(),signal.SIGSTOP)
journal.store.fault=fault
journal.commit(operation,NamespaceSnapshot({'active':'new'},{'new.bin':b'new'}),b'once-result')
'''
    read, write = os.pipe()
    child = subprocess.Popen([sys.executable, "-c", program, str(tmp_path), phase, str(write)], pass_fds=(write,),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.close(write)
    try:
        import select
        assert select.select([read], [], [], 10)[0], child.poll()
        assert os.read(read, 1) == b"B"
        child.kill()
        assert child.wait(timeout=5) == -signal.SIGKILL
    finally:
        os.close(read)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stderr.close()
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    try:
        old = NamespaceSnapshot({"active": "old"}, {"old.bin": b"old"})
        request = NamespaceOperation("kill-operation", "train", 0, old.digest, b"input")
        if phase == "before_replace":
            assert journal.snapshot.digest == old.digest and journal.lookup(request) is None
        else:
            assert journal.snapshot.digest == NamespaceSnapshot({"active": "new"}, {"new.bin": b"new"}).digest
            assert journal.lookup(request).receipt.result == b"once-result"
    finally:
        journal.close()


def test_writer_lock_and_external_change_never_silently_reset_namespace(tmp_path):
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    with pytest.raises(EffectError) as raised:
        NamespaceJournal(tmp_path, SCOPE, initial())
    assert raised.value.code == "effect_busy"
    raw = (tmp_path / "namespace-effects.json").read_bytes()
    (tmp_path / "namespace-effects.json").write_bytes(raw + b" ")
    with pytest.raises(EffectError) as changed:
        journal.commit(operation_id := NamespaceOperation("external", "train", 0, initial().digest, b"input"), initial(), b"result")
    assert changed.value.code == "effect_changed" and operation_id.operation_id == "external"
    journal.close()
    (tmp_path / "namespace-effects.json").write_bytes(b'{"format":1,"format":1}')
    corrupt = (tmp_path / "namespace-effects.json").read_bytes()
    with pytest.raises(EffectError):
        NamespaceJournal(tmp_path, SCOPE, initial())
    assert (tmp_path / "namespace-effects.json").read_bytes() == corrupt


@pytest.mark.parametrize("mutation", ("future", "digest", "duplicate", "unicode", "version", "permissions", "scope"))
def test_corrupt_private_journal_is_preserved_and_rejected(tmp_path, mutation):
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    journal.commit(operation(journal), initial(), b"result")
    journal.close()
    path = tmp_path / "namespace-effects.json"
    value = json.loads(path.read_bytes())
    if mutation == "future":
        value["body"]["receipts"][0]["generation"] += 1
    elif mutation == "digest":
        value["body"]["receipts"][0]["after"] = "0" * 64
    elif mutation == "duplicate":
        value["body"]["receipts"] *= 2
    elif mutation == "unicode":
        value["body"]["namespace"]["files"]["invalid\ud800"] = ""
    elif mutation == "version":
        value["format"] = 999
    elif mutation == "scope":
        value["scope"]["environment"] = "production"
    else:
        path.chmod(0o644)
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    with pytest.raises(EffectError):
        NamespaceJournal(tmp_path, SCOPE, initial())
    assert path.read_bytes() == raw


def test_stale_cas_reused_operation_and_pruned_receipt_reject(tmp_path, monkeypatch):
    import whab.plugins.namespace_effects as module
    monkeypatch.setattr(module, "MAX_EFFECT_RECEIPTS", 2)
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    first = operation(journal, "first")
    journal.commit(first, initial(), b"one")
    with pytest.raises(EffectError):
        journal.commit(replace(first, input=b"changed training input"), initial(), b"bad")
    for name in ("second", "third"):
        journal.commit(operation(journal, name), initial(), name.encode())
    with pytest.raises(EffectError) as raised:
        journal.lookup(first)
    assert raised.value.code == "effect_conflict" and journal.generation == 3
    journal.close()


def test_detached_context_preserves_unknown_files_and_revokes_every_callback():
    materialized = Materialized(initial())
    draft = NamespaceDraft(materialized.context(), initial(), writable=True)
    context = draft.context()
    context.write_file("demo/model.bin", b"demo-only")
    context.save_state({"active": "new"})
    assert draft.snapshot.files["unknown.bin"] == b"preserve unknown bytes"
    assert materialized.snapshot().digest == initial().digest
    context.delete_file("demo/model.bin")
    assert "demo/model.bin" not in draft.snapshot.files
    draft.close()
    for call in (lambda: context.read_file("model.bin"), lambda: context.write_file("model.bin", b"new"),
                 lambda: context.delete_file("model.bin"), lambda: context.save_state({}), draft.context):
        with pytest.raises(EffectError) as raised:
            call()
        assert raised.value.code == "effect_closed"
    readonly = NamespaceDraft(materialized.context(), initial())
    with pytest.raises(EffectError) as raised:
        readonly.context().write_file("model.bin", b"must not publish")
    assert raised.value.code == "read_only_effect"


@pytest.mark.parametrize("state,files", (({}, {"../escape": b"bad"}), ({"accessToken": "forbidden"}, {}),
                                        ({}, {"state.json": b"bad"}), ({}, {"x": bytearray(b"custom")}),
                                        ({"score": float("nan")}, {})))
def test_snapshot_bounds_types_and_reserved_paths_are_enforced(state, files):
    with pytest.raises(EffectError):
        NamespaceSnapshot(state, files)


def test_budget_and_environment_scope_are_bound_before_opening_existing_payload(tmp_path):
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    journal.close()
    for scope in (replace(SCOPE, budget_id="other-budget"), replace(SCOPE, environment="production"), replace(SCOPE, plugin_id="other-plugin")):
        with pytest.raises(EffectError):
            NamespaceJournal(tmp_path, scope, initial())


def test_competing_commands_share_one_generation_cas_and_never_mix_namespace_outputs(tmp_path):
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    barrier = Barrier(2)
    operations = (operation(journal, "one"), operation(journal, "two"))

    def commit(request):
        snapshot = NamespaceSnapshot({"active": request.operation_id}, {request.operation_id: request.operation_id.encode()})
        barrier.wait(timeout=5)
        try:
            return journal.commit(request, snapshot, request.operation_id.encode())
        except EffectError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(commit, operations))
    assert outcomes.count("effect_conflict") == 1 and journal.generation == 1
    winner, = (value for value in outcomes if not isinstance(value, str))
    assert journal.snapshot.state == {"active": winner.receipt.operation_id}
    assert set(journal.snapshot.files) == {winner.receipt.operation_id}
    journal.close()


def test_limits_reject_before_publication_and_namespace_bytes_are_detached(tmp_path, monkeypatch):
    import whab.plugins.namespace_effects as module
    files = {"unknown": b"preserved"}
    state = {"parameters": [1]}
    snapshot = NamespaceSnapshot(state, files)
    files.clear()
    state["parameters"].append(2)
    assert snapshot.files["unknown"] == b"preserved" and snapshot.state == {"parameters": [1]}
    with pytest.raises(EffectError):
        NamespaceSnapshot({}, {str(index): b"" for index in range(module.MAX_CONTAINER_FILES + 1)})
    journal = NamespaceJournal(tmp_path, SCOPE, initial())
    raw = (tmp_path / "namespace-effects.json").read_bytes()
    request = operation(journal)
    monkeypatch.setattr(module, "MAX_RESULT_BYTES", 2)
    with pytest.raises(EffectError):
        journal.commit(request, initial(), b"too long")
    assert (tmp_path / "namespace-effects.json").read_bytes() == raw
    monkeypatch.setattr(module, "MAX_STATE_BYTES", 8)
    with pytest.raises(EffectError):
        NamespaceSnapshot({"long": "value"}, {})
    journal.close()


def test_sigkill_during_native_materialization_recovers_full_namespace_from_original_receipt(tmp_path):
    program = '''import json,os,signal,sys
from pathlib import Path
from whab.plugins import BudgetContext
from whab.plugins.namespace_effects import NamespaceJournal,NamespaceScope,NamespaceSnapshot,NamespaceOperation
root=Path(sys.argv[1]); native=root/'native'; native.mkdir()
def save(name,raw):
 with (native/name).open('wb') as target:
  target.write(raw); target.flush(); os.fsync(target.fileno())
 directory=os.open(native,os.O_RDONLY); os.fsync(directory); os.close(directory)
save('state.json',b'{}'); save('old.bin',b'old')
def snapshot():
 return NamespaceSnapshot(json.loads((native/'state.json').read_bytes()),{path.name:path.read_bytes() for path in native.iterdir() if path.name!='state.json'})
scope=NamespaceScope('synthetic-budget','sandbox','fictional-categorizer')
journal=NamespaceJournal(root/'private',scope,snapshot())
before=journal.snapshot
request=NamespaceOperation('materialize-once','train',0,before.digest,b'input')
journal.commit(request,NamespaceSnapshot({'active':'new'},{'a.bin':b'first','b.bin':b'second'}),b'original-model-result')
def write(name,raw):
 save(name,raw)
 os.write(int(sys.argv[2]),b'B'); os.kill(os.getpid(),signal.SIGSTOP)
context=BudgetContext(scope.budget_id,{}, {},lambda state:save('state.json',json.dumps(state).encode()),
                      lambda name:(native/name).read_bytes(),write,lambda name:(native/name).unlink())
journal.materialize(context,snapshot)
'''
    read, write = os.pipe()
    child = subprocess.Popen([sys.executable, "-c", program, str(tmp_path), str(write)], pass_fds=(write,),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.close(write)
    try:
        import select
        assert select.select([read], [], [], 10)[0], child.poll()
        assert os.read(read, 1) == b"B"
        child.kill()
        assert child.wait(timeout=5) == -signal.SIGKILL
    finally:
        os.close(read)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stderr.close()
    native = tmp_path / "native"
    assert (native / "a.bin").read_bytes() == b"first" and not (native / "b.bin").exists()

    def snapshot():
        return NamespaceSnapshot(json.loads((native / "state.json").read_bytes()), {
            path.name: path.read_bytes() for path in native.iterdir() if path.name != "state.json"
        })

    context = BudgetContext(SCOPE.budget_id, {}, {}, lambda state: (native / "state.json").write_text(json.dumps(state)),
                            lambda name: (native / name).read_bytes(),
                            lambda name, raw: (native / name).write_bytes(raw), lambda name: (native / name).unlink())
    journal = NamespaceJournal(tmp_path / "private", SCOPE, initial())
    try:
        old = NamespaceSnapshot({}, {"old.bin": b"old"})
        request = NamespaceOperation("materialize-once", "train", 0, old.digest, b"input")
        receipt = journal.lookup(request).receipt
        assert receipt.result == b"original-model-result" and not journal.materialized
        journal.recover(context, snapshot)
        assert snapshot().state == {"active": "new"} and set(snapshot().files) == {"a.bin", "b.bin"}
        assert journal.materialized and journal.lookup(request).receipt == receipt
    finally:
        journal.close()
