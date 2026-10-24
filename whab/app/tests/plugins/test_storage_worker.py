"""Real private worker/native receipts; fictional data and temporary homes only."""

import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from whab.domain.errors import StoreError
from whab.plugins import PluginError, StorageContext
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json
from whab.plugins.storage_worker import StorageWorker, storage_error
from whab.plugins.storage_worker_codec import HEADER, decode, encode
from whab.plugins.wire import WireError
from whab.services.ledger import LedgerService


@pytest.fixture(params=("sqlite", "json"))
def proxy(request, tmp_path):
    context = StorageContext(tmp_path, "sandbox", {})
    backend = StorageWorkerProxy(StorageWorker(request.param), context, tmp_path / "intents")
    yield backend
    backend.close()


def test_backend_is_owned_by_child_and_reopens_same_authority(proxy):
    evidence = proxy.commit_evidence()
    account = proxy.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 12345})
    assert proxy.accounts("personal")[0]["id"] == account
    assert not hasattr(proxy, "db") and not hasattr(proxy, "state")
    assert proxy.worker._process.pid != __import__("os").getpid()
    assert proxy.commit_evidence().authority_epoch == evidence.authority_epoch
    assert proxy.recover() == {"outcome": "unchanged"}
    assert proxy.accounts("personal")[0]["balance"] == 12345


def test_retained_child_owns_sole_writer_before_absent_receipt_can_prove_noncommit(proxy, tmp_path):
    before = proxy.commit_evidence()
    operation = StorageOperation("not-yet-dispatched", before.authority_epoch, before.generation, "personal", "add_account",
                                 canonical_storage_json({"name": "Fictional pending", "type": "depository", "balance": 1}))
    proxy.intents.begin(operation, before)
    competing = StorageWorker(proxy.worker.plugin_id)
    reader = StorageWorker(proxy.worker.plugin_id)
    try:
        with pytest.raises(PluginError):
            competing.open(proxy.context)
        assert reader.open(replace(proxy.context, read_only=True)) == before
        assert reader.call("operation_receipt", operation.operation_id) is None
        assert proxy.worker.healthy
    finally:
        competing.close()
        reader.close()
    # Reaping the retained writer is a separate required action; an RO reader's
    # absence proof cannot clear its pending intent while that writer is alive.
    proxy.close()
    replacement = StorageWorkerProxy(StorageWorker(proxy.worker.plugin_id), proxy.context, tmp_path / "intents")
    try:
        assert replacement.worker_recovery_required()
        assert replacement.recover() == {"outcome": "not_committed"}
    finally:
        replacement.close()


def test_read_side_child_crash_fences_and_remains_explicitly_recoverable(proxy):
    faults = []
    proxy._on_unknown = faults.append
    proxy.worker._process.kill()
    proxy.worker._process.wait(timeout=5)
    with pytest.raises(PluginError):
        proxy.accounts("personal")
    assert proxy.worker_recovery_required() and faults == [proxy.worker.plugin_id]
    assert proxy.recover() == {"outcome": "unchanged"}
    assert proxy.accounts("personal") == []


def test_authority_control_uncertainty_retains_its_separate_recovery_owner(proxy, monkeypatch):
    faults = []
    proxy._on_unknown = faults.append
    before = proxy.commit_evidence()
    call = proxy.worker.call

    def lose(method, *args, **kwargs):
        result = call(method, *args, **kwargs)
        if method == "set_role":
            raise storage_error(proxy.worker.plugin_id, "storage_write_unknown")
        return result

    monkeypatch.setattr(proxy.worker, "call", lose)
    with pytest.raises(PluginError):
        proxy.set_role("owned-migration", before, "fenced")
    assert proxy.worker_recovery_required() and faults == []
    with pytest.raises(PluginError):
        proxy.recover()
    assert proxy.recover_control(before) == replace(before, role="fenced")
    assert proxy.commit_evidence().role == "fenced"
    proxy.set_role("owned-migration", proxy.commit_evidence(), "primary")
    assert proxy.recover() == {"outcome": "unchanged"}
    assert proxy.commit_evidence() == before


@pytest.mark.parametrize("conflict", ["financial_pending", "changed_generation", "financial_fault"])
def test_authority_control_reopen_never_clears_financial_or_content_uncertainty(proxy, conflict):
    before = proxy.commit_evidence()
    proxy._control_unknown = "set_role"
    if conflict == "financial_pending":
        operation = StorageOperation("pending-financial", before.authority_epoch, before.generation, "personal", "add_account",
                                     canonical_storage_json({"name": "Pending", "type": "depository", "balance": 1}))
        proxy.intents.begin(operation, before)
    elif conflict == "changed_generation":
        before = replace(before, generation=before.generation + 1)
    else:
        proxy._control_unknown = None
    with pytest.raises(PluginError):
        proxy.recover_control(before)
    if conflict != "changed_generation":
        assert proxy.worker.healthy


def test_reopened_native_snapshot_cannot_precede_retained_acknowledged_command(proxy, tmp_path):
    kind, context = proxy.worker.plugin_id, proxy.context
    proxy.close()
    path = context.home / "data" / ("whab-sandbox." + ("sqlite" if kind == "sqlite" else "json"))
    initial = path.read_bytes()
    active = StorageWorkerProxy(StorageWorker(kind), context, tmp_path / "intents")
    active.add_account("personal", {"name": "Acknowledged", "type": "depository", "balance": 77})
    active.close()
    acknowledged = path.read_bytes()
    journal = next((tmp_path / "intents").rglob("storage-intents.json"))
    retained = journal.read_bytes()
    path.write_bytes(initial)
    with pytest.raises(StoreError, match="precedes an acknowledged"):
        StorageWorkerProxy(StorageWorker(kind), context, tmp_path / "intents")
    assert journal.read_bytes() == retained
    path.write_bytes(acknowledged)
    restored = StorageWorkerProxy(StorageWorker(kind), context, tmp_path / "intents")
    try:
        assert restored.accounts("personal")[0]["balance"] == 77
    finally:
        restored.close()


def test_explicit_recovery_never_recreates_a_deleted_native_file(proxy):
    proxy.add_account("personal", {"name": "Acknowledged", "type": "depository", "balance": 5})
    suffix = "sqlite" if proxy.worker.plugin_id == "sqlite" else "json"
    path = proxy.context.home / "data" / ("whab-sandbox." + suffix)
    path.unlink()
    existing = set(path.parent.iterdir())
    with pytest.raises(PluginError):
        proxy.recover()
    assert not path.exists() and set(path.parent.iterdir()) <= existing
    assert proxy.worker_recovery_required()


def test_durable_ledger_replay_survives_host_restart_and_later_writes(proxy, tmp_path):
    ledger = LedgerService(proxy)
    initial = ledger.revision
    first = ledger.command("add_account", "personal", {"name": "Fictional", "type": "depository", "balance": 12},
                           request_id="stable-request", expected_revision=initial)
    second = ledger.command("add_category", "personal", {"name": "Later", "group": "Fictional", "target": 0},
                            request_id="later", expected_revision=ledger.revision)
    kind = proxy.worker.plugin_id
    proxy.close()
    replacement = StorageWorkerProxy(StorageWorker(kind), proxy.context, tmp_path / "intents")
    try:
        reopened = LedgerService(replacement)
        assert reopened.command("add_account", "personal", {"name": "Fictional", "type": "depository", "balance": 12},
                                request_id="stable-request", expected_revision=initial) == first
        assert reopened.revision != first["revision"]
        assert len(replacement.accounts("personal")) == 1
        assert second["data"] in {row["id"] for row in replacement.categories("personal")}
        with pytest.raises(ValueError, match="different"):
            reopened.command("add_account", "personal", {"name": "Changed", "type": "depository", "balance": 12},
                             request_id="stable-request", expected_revision=initial)
    finally:
        replacement.close()


def test_lost_mutating_reply_fences_and_explicit_recovery_returns_original(proxy, monkeypatch):
    ledger = LedgerService(proxy)
    revision = ledger.revision
    call = proxy.worker.call

    def lose(method, *args, **kwargs):
        result = call(method, *args, **kwargs)
        if method == "execute_operation":
            raise storage_error(proxy.worker.plugin_id, "storage_write_unknown")
        return result

    monkeypatch.setattr(proxy.worker, "call", lose)
    with pytest.raises(ValueError, match="unknown"):
        ledger.command("add_account", "personal", {"name": "Fictional", "type": "depository", "balance": 10},
                       request_id="lost", expected_revision=revision)
    assert proxy.uncertain
    with pytest.raises(ValueError):
        proxy.add_category("personal", {"name": "Must wait", "target": 0})
    assert proxy.recover() == {"outcome": "committed"}
    response = ledger.command("add_account", "personal", {"name": "Fictional", "type": "depository", "balance": 10},
                              request_id="lost", expected_revision=revision)
    assert response["data"] == proxy.accounts("personal")[0]["id"]
    assert len(proxy.accounts("personal")) == 1


def test_pre_execution_lost_reply_requires_explicit_proof_without_repeating(proxy, monkeypatch):
    call = proxy.worker.call

    def lose(method, *args, **kwargs):
        if method == "execute_operation":
            raise storage_error(proxy.worker.plugin_id, "storage_write_unknown")
        return call(method, *args, **kwargs)

    monkeypatch.setattr(proxy.worker, "call", lose)
    with pytest.raises(ValueError):
        proxy.add_account("personal", {"name": "Never sent", "type": "depository", "balance": 99})
    assert proxy.recover() == {"outcome": "not_committed"}
    assert proxy.accounts("personal") == []


def test_rejected_native_validation_clears_intent_without_false_success(proxy):
    before = proxy.commit_evidence()
    with pytest.raises(ValueError):
        proxy.add_account("personal", {"name": "Fictional", "type": "depository", "balance": True})
    assert proxy.commit_evidence() == before
    assert proxy.intents.snapshot()["pending"] is None
    assert not proxy.uncertain


def test_lost_host_receipt_publication_reply_reconciles_committed_result(proxy):
    publications = 0

    def fail_finish(boundary):
        nonlocal publications
        if boundary == "after_directory_fsync":
            publications += 1
            if publications == 2:
                raise OSError("fictional lost host completion reply")

    proxy.intents.store.fault = fail_finish
    with pytest.raises(ValueError, match="unknown"):
        proxy.add_account("personal", {"name": "Payload stays native", "type": "depository", "balance": 123})
    assert proxy.uncertain
    proxy.intents.store.fault = None
    assert proxy.recover() == {"outcome": "committed"}
    assert len(proxy.accounts("personal")) == 1
    assert b"Payload stays native" not in proxy.intents.store.path.read_bytes()


def test_explicit_operation_receipt_replay_checks_fingerprint_before_generation(proxy):
    before = proxy.commit_evidence()
    operation = StorageOperation(str(uuid4()), before.authority_epoch, before.generation, "personal", "add_account",
                                 canonical_storage_json({"name": "Fictional", "type": "depository", "balance": 1}))
    result = proxy.execute_operation(operation)
    assert proxy.execute_operation(operation).replayed
    assert proxy.operation_receipt(operation.operation_id) == result.receipt
    assert len(proxy.accounts("personal")) == 1


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_read_only_worker_does_not_contend_with_active_writer(kind, tmp_path):
    context = StorageContext(tmp_path, "sandbox", {})
    writer = StorageWorkerProxy(StorageWorker(kind), context, tmp_path / "intents")
    reader = StorageWorker(kind)
    try:
        assert reader.open(StorageContext(tmp_path, "sandbox", {}, read_only=True)) == writer.commit_evidence()
        assert reader.call("accounts", "personal") == []
        before = writer.commit_evidence()
        command = StorageOperation(str(uuid4()), before.authority_epoch, before.generation, "personal", "add_category",
                                   canonical_storage_json({"name": "Blocked", "target": 0}))
        with pytest.raises(ValueError):
            reader.call("execute_operation", command)
        assert writer.commit_evidence() == before
    finally:
        reader.close()
        writer.close()


def test_storage_codec_retains_binary_and_rejects_untyped_objects():
    value = {"z": b"private ciphertext", "a": (b"receipt", None, True, 123)}
    assert decode(encode(value)) == value
    for unsupported in (Path("/private"), lambda: None, object(), 0.8):
        with pytest.raises((WireError, StoreError)):
            encode(unsupported)
    raw = encode(value)
    with pytest.raises(WireError):
        decode(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(WireError):
        decode(HEADER.pack(b"WHST", 99, 2, 0) + b"[]")


def test_legacy_or_remote_storage_cannot_silently_use_local_worker():
    with pytest.raises(ValueError):
        StorageWorker("client", python=sys.executable)


@pytest.mark.parametrize("changed", ["before", "receipt", "current"])
def test_durable_financial_receipt_cannot_validate_a_nonprimary_authority(proxy, changed):
    from whab.plugins.storage_intents import validate_receipt
    from whab.plugins.storage_transfer import evidence_record, read_evidence

    proxy.add_account("personal", {"name": "Acknowledged primary", "type": "depository", "balance": 9})
    row = proxy.intents.snapshot()["completed"][-1]
    receipt = proxy.operation_receipt(row["operation_id"])
    current = proxy.commit_evidence()
    if changed == "before":
        row["before"] = evidence_record(replace(read_evidence(row["before"]), role="fenced"))
    elif changed == "receipt":
        receipt = replace(receipt, evidence=replace(receipt.evidence, role="fenced"))
    else:
        current = replace(current, role="fenced")
    with pytest.raises(StoreError, match="does not match"):
        validate_receipt(row, receipt, current)


def test_financial_recovery_never_resumes_an_authority_fenced_native_instance(proxy):
    before = proxy.commit_evidence()
    proxy.set_role("explicit-authority-owner", before, "fenced")
    with pytest.raises(PluginError):
        proxy.recover()
    assert proxy.worker_recovery_required()


@pytest.mark.parametrize("kind", ["sqlite", "json"])
@pytest.mark.parametrize("conflict", ["older_generation", "same_generation_other_content"])
def test_preworker_acknowledged_baseline_cannot_regress_during_read_fault_recovery(kind, conflict, tmp_path):
    from whab.plugins import load_plugins
    from whab.plugins.storage import open_storage

    context = StorageContext(tmp_path, "sandbox", {})
    descriptor = load_plugins((kind,)).storage_plugin(kind)
    native = open_storage(descriptor, context)
    native.close()
    path = tmp_path / "data" / ("whab-sandbox." + ("sqlite" if kind == "sqlite" else "json"))
    pristine = path.read_bytes()
    native = open_storage(descriptor, context)
    native.add_account("personal", {"name": "Fictional acknowledged before worker", "type": "depository", "balance": 77})
    expected = native.commit_evidence()
    native.close()
    acknowledged = path.read_bytes()
    replacement = pristine
    if conflict == "same_generation_other_content":
        path.write_bytes(pristine)
        other = open_storage(descriptor, context)
        other.add_account("personal", {"name": "Different fictional branch", "type": "depository", "balance": 88})
        assert other.commit_evidence().generation == expected.generation
        other.close()
        replacement = path.read_bytes()
        path.write_bytes(acknowledged)
    backend = StorageWorkerProxy(StorageWorker(kind), context, tmp_path / "intents")
    try:
        assert backend.commit_evidence() == expected
        assert backend.intents.snapshot()["completed"] == []
        backend.worker._process.kill()
        backend.worker._process.wait(timeout=5)
        with pytest.raises(PluginError):
            backend.accounts("personal")
        path.write_bytes(replacement)
        with pytest.raises(PluginError):
            backend.recover()
        assert backend.worker_recovery_required()
        assert path.read_bytes() == replacement
        path.write_bytes(acknowledged)
        assert backend.recover() == {"outcome": "unchanged"}
        assert backend.commit_evidence() == expected
        assert [row["balance"] for row in backend.accounts("personal")] == [77]
    finally:
        backend.close()
