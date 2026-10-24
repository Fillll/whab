"""SQLite commits bind fictional financial data, durable receipts and authority."""

import base64
import json
import os
import signal
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from whab_plugin_devkit import receipt_png
from whab_plugin_sqlite import Store, authority

from whab.plugins import StoreError, WorkspaceImport
from whab.plugins.storage_transfer import (
    CommitEvidence,
    StorageOperation,
    canonical_storage_json,
    decode_local_state,
    read_storage_json,
)


@pytest.fixture
def authority_store(tmp_path):
    backend = Store(tmp_path / "fictional.sqlite", month="2026-10")
    yield backend
    backend.close()


def operation(backend, name, payload, *, identifier="fictional-operation", workspace="personal"):
    evidence = backend.commit_evidence()
    return StorageOperation(identifier, evidence.authority_epoch, evidence.generation,
                            workspace, name, canonical_storage_json(payload))


def add_account(backend):
    return operation(backend, "add_account", {"name": "Fictional wallet", "type": "depository", "balance": 10000})


def test_durable_result_retry_conflict_generation_and_reopen(authority_store):
    original = authority_store.commit_evidence()
    assert type(original) is CommitEvidence and original.role == "primary"
    request = add_account(authority_store)
    result = authority_store.execute_operation(request)
    assert not result.replayed
    identifier = read_storage_json(result.receipt.result)
    assert authority_store.accounts("personal")[0]["id"] == identifier
    assert result.receipt.evidence.generation == original.generation + 1
    assert result.receipt.evidence.operation_id == request.operation_id
    assert authority_store.operation_receipt("unknown") is None
    assert authority_store.execute_operation(request).replayed
    assert authority_store.operation_receipt(request.operation_id) == result.receipt
    assert authority_store.commit_evidence() == result.receipt.evidence
    with pytest.raises(StoreError, match="different"):
        authority_store.execute_operation(replace(request, payload=canonical_storage_json({"name": "Changed", "type": "depository", "balance": 0})))
    with pytest.raises(StoreError, match="unknown"):
        authority_store.execute_operation(replace(request, operation_id="absent-old-request"))
    with pytest.raises(StoreError, match="validated"):
        authority_store.execute_operation({"name": "add_account"})
    authority_store.close()
    reopened = Store(authority_store.database_path)
    try:
        assert reopened.execute_operation(request).receipt == result.receipt
        assert len(reopened.accounts("personal")) == 1
        assert reopened.commit_evidence() == result.receipt.evidence
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", ["invalid-money", "receipt-insert", "authority-update", "transaction-rollback"])
def test_invalid_command_and_native_record_failures_roll_back_money_receipt_and_generation(authority_store, failure):
    request = add_account(authority_store)
    if failure == "invalid-money":
        request = replace(request, payload=canonical_storage_json({"name": "Fictional", "type": "depository", "balance": True}))
    else:
        table, verb = ("storage_operations", "INSERT") if failure in {"receipt-insert", "transaction-rollback"} else ("storage_authority", "UPDATE")
        kind = "ROLLBACK" if failure == "transaction-rollback" else "ABORT"
        authority_store.db.execute(f"CREATE TRIGGER fail_write BEFORE {verb} ON {table} BEGIN SELECT RAISE({kind},'fictional failure'); END")
    before = authority_store.commit_evidence()
    with pytest.raises((StoreError, sqlite3.IntegrityError)):
        authority_store.execute_operation(request)
    assert authority_store.commit_evidence() == before
    assert authority_store.accounts("personal") == []
    assert authority_store.operation_receipt(request.operation_id) is None
    assert not authority_store.db.in_transaction
    assert authority_store._writing is None


class CommitFailure:
    def __init__(self, connection, *, committed, error=OSError):
        self.connection, self.committed, self.error = connection, committed, error
        self.armed = True

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, statement, parameters=()):
        if statement == "COMMIT" and self.armed:
            self.armed = False
            if self.committed:
                self.connection.execute(statement, parameters)
            raise self.error("fictional commit response loss")
        return self.connection.execute(statement, parameters)


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_commit_fault_never_separates_data_from_receipt_and_fences_uncertain_handle(authority_store, committed, error):
    request = add_account(authority_store)
    before = authority_store.commit_evidence()
    authority_store.db = CommitFailure(authority_store.db, committed=committed, error=error)
    expected = authority.CommitUncertain if committed and error is OSError else error
    with pytest.raises(expected):
        authority_store.execute_operation(request)
    assert not authority_store.db.in_transaction and authority_store._writing is None
    if committed:
        for read_or_write in (authority_store.commit_evidence, authority_store.export_local,
                              lambda: authority_store.operation_receipt(request.operation_id),
                              lambda: authority_store.execute_operation(request)):
            with pytest.raises(authority.CommitUncertain) as uncertain:
                read_or_write()
            assert uncertain.value.operation_id == request.operation_id
            assert uncertain.value.generation == before.generation + 1
    else:
        assert authority_store.commit_evidence() == before
        assert authority_store.operation_receipt(request.operation_id) is None
    authority_store.close()
    recovered = Store(authority_store.database_path)
    try:
        assert bool(recovered.accounts("personal")) is committed
        assert (recovered.operation_receipt(request.operation_id) is not None) is committed
        assert recovered.execute_operation(request).replayed is committed
        assert len(recovered.accounts("personal")) == 1
    finally:
        recovered.close()


@pytest.mark.parametrize("committed", [False, True])
def test_process_death_at_native_commit_recovers_matching_data_and_operation_receipt(authority_store, committed):
    request = add_account(authority_store)
    path = authority_store.database_path
    authority_store.close()
    program = """
import json,os,signal,sys
from whab_plugin_sqlite import Store
from whab.plugins.storage_transfer import StorageOperation,canonical_storage_json
data=json.load(sys.stdin)
store=Store(data['path'])
connection=store.db
class Boundary:
    def __getattr__(self,name): return getattr(connection,name)
    def execute(self,sql,args=()):
        if sql=='COMMIT':
            if data['committed']: connection.execute(sql,args)
            os.kill(os.getpid(),signal.SIGKILL)
        return connection.execute(sql,args)
store.db=Boundary()
request=StorageOperation(data['id'],data['epoch'],data['generation'],'personal','add_account',canonical_storage_json(data['payload']))
store.execute_operation(request)
"""
    process = subprocess.run([sys.executable, "-I", "-c", program],
                             input=json.dumps({"path": path, "id": request.operation_id, "epoch": request.authority_epoch,
                                               "generation": request.expected_generation, "payload": read_storage_json(request.payload),
                                               "committed": committed}), text=True, capture_output=True,
                             env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"}, timeout=15)
    assert process.returncode == -signal.SIGKILL, process.stderr
    recovered = Store(path)
    try:
        assert bool(recovered.accounts("personal")) is committed
        assert (recovered.operation_receipt(request.operation_id) is not None) is committed
        assert recovered.execute_operation(request).replayed is committed
        assert len(recovered.accounts("personal")) == 1
    finally:
        recovered.close()


def test_fence_is_persistent_visible_to_old_handles_and_compares_complete_expected_evidence(authority_store):
    second = Store(authority_store.database_path)
    original = authority_store.commit_evidence()
    try:
        with pytest.raises(StoreError, match="changed"):
            authority_store.set_role("transition", replace(original, state_digest="1" * 64), "fenced")
        with pytest.raises(StoreError, match="complete"):
            authority_store.set_role("transition", None, "fenced")
        fenced = authority_store.set_role("transition", original, "fenced")
        assert fenced == replace(original, role="fenced")
        assert authority_store.set_role("transition", original, "fenced") == fenced
        assert authority_store.set_role("transition", fenced, "fenced") == fenced
        assert second.commit_evidence() == fenced
        with pytest.raises(StoreError, match="fenced"):
            second.execute_operation(add_account(second))
        with pytest.raises(StoreError):
            second.set_role("different", fenced, "primary")
        resumed = second.set_role("transition", fenced, "primary")
        assert resumed == original
        fenced = authority_store.set_role("retire", resumed, "fenced")
        retired = authority_store.set_role("retire", fenced, "retired")
        assert second.commit_evidence() == retired
        with pytest.raises(StoreError):
            second.set_role("retire", retired, "primary")
    finally:
        second.close()
    authority_store.close()
    reopened = Store(authority_store.database_path, initial_role="primary")
    try:
        assert reopened.commit_evidence() == retired
    finally:
        reopened.close()


def test_every_legacy_mutation_rejects_fenced_authority_without_changing_ledger(authority_store):
    account = authority_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 10000})
    transaction = authority_store.add_transaction("personal", {"accountId": account, "date": "2026-10-01", "merchant": "Fictional", "amount": 1})
    authority_store.save_item("item", "fictional-ciphertext", "Fictional")
    portable = authority_store.export_budget()
    files = authority_store.export_budget_files()
    authority_store.set_role("transition", authority_store.commit_evidence(), "fenced")
    before = authority_store.commit_evidence()
    for mutate in (
        lambda: authority_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 0}),
        lambda: authority_store.update_account("personal", account, {"name": "Changed"}),
        lambda: authority_store.add_category("personal", {"name": "Fictional", "target": 0}),
        lambda: authority_store.add_transaction("personal", {"accountId": account, "date": "2026-10-01", "merchant": "Fictional", "amount": 1}),
        lambda: authority_store.edit_transaction("personal", transaction, reviewed=True),
        lambda: authority_store.review_transactions("personal", [transaction], True),
        lambda: authority_store.assign("personal", "groceries", "2026-10", 1),
        lambda: authority_store.assign_next_month("personal", "2026-11", "USD", 0),
        lambda: authority_store.set_rollover_mode("personal", "2026-10", "USD", "category"),
        lambda: authority_store.save_item("other", "fictional-ciphertext", "Fictional"),
        lambda: authority_store.set_item_status("item", "connected", None),
        lambda: authority_store.disconnect_item("item"),
        lambda: authority_store.apply_sync("item", {"added": [], "modified": [], "removed": []}, "next"),
        lambda: authority_store.save_receipt("personal", transaction, receipt_png()),
        lambda: authority_store.delete_receipt("personal", transaction),
        lambda: authority_store.restore_budget("personal", portable),
        lambda: authority_store.restore_budget_files("personal", files),
        lambda: authority_store.import_workspace("personal", "fictional", "1" * 64, WorkspaceImport((), (), (), (), "2026-10")),
    ):
        with pytest.raises(StoreError, match="fenced"):
            mutate()
        assert authority_store.commit_evidence() == before


def test_receipt_expiry_refuses_unknown_stale_commands(authority_store, monkeypatch):
    monkeypatch.setattr(authority, "MAX_OPERATION_RECEIPTS", 2)
    requests = []
    for index in range(3):
        request = operation(authority_store, "add_category", {"name": f"Fictional {index}", "group": "Fictional", "target": 0}, identifier=f"operation-{index}")
        requests.append(request)
        authority_store.execute_operation(request)
    assert authority_store.operation_receipt(requests[0].operation_id) is None
    before = authority_store.commit_evidence()
    with pytest.raises(StoreError, match="unknown"):
        authority_store.execute_operation(requests[0])
    assert authority_store.commit_evidence() == before


def test_complete_local_transfer_retains_protected_state_receipts_retry_identities_and_both_workspaces(
    authority_store, tmp_path, imported_account, imported_transaction, sync_changes,
):
    authority_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    authority_store.apply_sync("item", sync_changes(added=[imported_transaction()]), "cursor", [imported_account()])
    request = operation(authority_store, "save_receipt", {"transactionId": "tx", "image": base64.b64encode(receipt_png()).decode()})
    result = authority_store.execute_operation(request)
    snapshot = authority_store.export_local()
    assert "fictional-ciphertext" not in repr(snapshot)
    destination = Store(tmp_path / "staged.sqlite", month="2026-10", initial_role="staged")
    try:
        assert destination.preview_local(snapshot).supported
        imported = destination.import_local("transfer", snapshot)
        assert imported.role == "staged" and imported.instance_id != snapshot.source.instance_id
        assert imported.generation == snapshot.source.generation and imported.state_digest == snapshot.source.state_digest
        assert destination.import_local("transfer", snapshot) == imported
        assert destination.operation_receipt(request.operation_id) == result.receipt
        assert destination.get_item("item") == authority_store.get_item("item")
        assert destination.accounts("personal") == authority_store.accounts("personal")
        assert destination.state("demo") == authority_store.state("demo")
        assert destination.get_receipt("personal", "tx") == receipt_png()
        assert decode_local_state(snapshot.financial, snapshot.protected)["personal"]["items"]["item"]["cursor"] == "cursor"
        with pytest.raises(StoreError):
            destination.import_local("different", snapshot)
        with pytest.raises(StoreError):
            destination.set_role("different", imported, "primary", authority_epoch=str(uuid4()))
        primary = destination.set_role("transfer", imported, "primary", authority_epoch=str(uuid4()))
        assert primary.authority_epoch != snapshot.source.authority_epoch
        assert destination.execute_operation(request).receipt == result.receipt
        with pytest.raises(StoreError, match="unknown"):
            destination.execute_operation(replace(request, operation_id="new-old-epoch"))
        assert not destination.preview_local(snapshot).empty
        destination.close()
        destination = Store(tmp_path / "staged.sqlite")
        assert destination.operation_receipt(request.operation_id) == result.receipt
        assert destination.get_item("item")["cursor"] == "cursor"
        assert destination.get_receipt("personal", "tx") == receipt_png()
    finally:
        destination.close()


def test_transfer_preserves_workspace_import_identity_and_excludes_native_control_tables_from_portable_image(authority_store, tmp_path):
    imported = WorkspaceImport((), (), (), (), "2026-10")
    request = operation(authority_store, "import_workspace", {"pluginId": "fictional", "fingerprint": "0" * 64, "data": {
        "accounts": [], "categories": [], "transactions": [], "assignments": [], "budget_start_month": "2026-10", "currency": "USD",
    }})
    result = authority_store.execute_operation(request)
    destination = Store(tmp_path / "imported.sqlite", month="2026-10", initial_role="staged")
    try:
        destination.import_local("transfer", authority_store.export_local())
        evidence = destination.commit_evidence()
        destination.set_role("transfer", evidence, "primary", authority_epoch=str(uuid4()))
        assert destination.execute_operation(request).receipt == result.receipt
        assert destination.import_workspace("personal", "fictional", "0" * 64, imported)["alreadyImported"]
        portable = destination.export_budget_files()["budget.sqlite"]
        clean = sqlite3.connect(":memory:")
        try:
            clean.deserialize(portable)
            tables = {row[0] for row in clean.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            assert "storage_authority" not in tables and "storage_operations" not in tables
            assert not clean.execute("SELECT * FROM workspace_imports").fetchall()
        finally:
            clean.close()
    finally:
        destination.close()


@pytest.mark.parametrize("role", ["staged", "copy"])
def test_new_roles_stay_persistent_and_legacy_database_adopts_primary_only_once(tmp_path, role):
    path = tmp_path / "fictional.sqlite"
    initial = Store(path, month="2026-10", initial_role=role)
    evidence = initial.commit_evidence()
    initial.close()
    reopened = Store(path, initial_role="primary")
    try:
        assert reopened.commit_evidence() == evidence
        with pytest.raises(StoreError, match="fenced"):
            reopened.execute_operation(add_account(reopened))
    finally:
        reopened.close()
    old_path = tmp_path / "old.sqlite"
    old = Store(old_path, month="2026-10", _authority=False)
    old.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 1})
    before = old.state("personal")
    old.close()
    adopted = Store(old_path, initial_role=role)
    try:
        assert adopted.commit_evidence().role == "primary"
        assert adopted.commit_evidence().generation == 0
        assert adopted.state("personal") == before
    finally:
        adopted.close()


@pytest.mark.parametrize("field", ["budget", "environment"])
def test_existing_storage_rejects_wrong_budget_or_environment_without_changing_native_file(authority_store, field):
    path = authority_store.database_path
    authority_store.close()
    before = Path(path).read_bytes()
    with pytest.raises(StoreError, match="another budget or environment"):
        Store(path, **({"budget_id": "other-budget"} if field == "budget" else {"environment": "production"}))
    assert Path(path).read_bytes() == before


@pytest.mark.parametrize("role", [None, {}, "retired", "fenced"])
def test_invalid_initial_role_cannot_create_a_database(tmp_path, role):
    path = tmp_path / "fictional.sqlite"
    with pytest.raises(StoreError, match="New storage"):
        Store(path, initial_role=role)
    assert not path.exists()


def test_corrupt_or_changed_demo_is_not_a_pristine_transfer_destination(authority_store, tmp_path):
    snapshot = authority_store.export_local()
    destination = Store(tmp_path / "changed.sqlite", month="2026-10", initial_role="staged")
    try:
        destination.db.execute("DELETE FROM transactions WHERE workspace='demo' AND id='pending-demo'")
        assert destination.preview_local(snapshot).reason_code == "destination_not_empty"
        with pytest.raises(StoreError, match="empty staged"):
            destination.import_local("transfer", snapshot)
    finally:
        destination.close()


@pytest.mark.parametrize("kind", ["active", "copy", "wrong-budget", "wrong-environment", "same-instance", "invalid-snapshot"])
def test_transfer_target_policy_refuses_unsupported_or_non_staged_storage(authority_store, tmp_path, kind):
    snapshot = authority_store.export_local()
    if kind == "same-instance":
        destination = authority_store
    else:
        destination = Store(tmp_path / "target.sqlite", month="2026-10",
                            initial_role="primary" if kind == "active" else "copy" if kind == "copy" else "staged",
                            budget_id="other" if kind == "wrong-budget" else "default",
                            environment="production" if kind == "wrong-environment" else "sandbox")
    if kind == "invalid-snapshot":
        snapshot = None
    try:
        before = destination.commit_evidence()
        assert destination.preview_local(snapshot).supported is (kind == "copy")
        with pytest.raises(StoreError):
            destination.import_local("transfer", snapshot)
        assert destination.commit_evidence() == before
    finally:
        if destination is not authority_store:
            destination.close()


@pytest.mark.parametrize("corruption", ["unknown-field", "boolean-version", "empty-not-bool", "bad-instance", "bad-transition", "transfer-shape", "transfer-digest", "transfer-budget", "transfer-environment", "transfer-same-instance"])
def test_authority_metadata_corruption_is_rejected_without_rewriting_raw_database(authority_store, corruption):
    value = read_storage_json(bytes(authority_store.db.execute("SELECT record FROM storage_authority").fetchone()[0]))
    if corruption.startswith("transfer-"):
        source = authority_store.commit_evidence()
        value["transfer"] = {"id": "transfer", "digest": "0" * 64, "source": {
            name: getattr(replace(source, instance_id=str(uuid4())), name) for name in CommitEvidence.__slots__}}
        if corruption == "transfer-shape":
            value["transfer"].pop("digest")
        elif corruption == "transfer-digest":
            value["transfer"]["digest"] = "invalid"
        elif corruption == "transfer-budget":
            value["transfer"]["source"]["budget_id"] = "other"
        elif corruption == "transfer-environment":
            value["transfer"]["source"]["environment"] = "production"
        else:
            value["transfer"]["source"]["instance_id"] = value["instanceId"]
    elif corruption == "unknown-field":
        value["unknown"] = True
    elif corruption == "boolean-version":
        value["formatVersion"] = True
    elif corruption == "empty-not-bool":
        value["empty"] = 1
    elif corruption == "bad-instance":
        value["instanceId"] = "invalid"
    else:
        value["transitionId"] = ""
    authority_store.db.execute("UPDATE storage_authority SET record=?", (json.dumps(value).encode(),))
    path = authority_store.database_path
    authority_store.close()
    before = Path(path).read_bytes()
    with pytest.raises(StoreError):
        Store(path)
    assert Path(path).read_bytes() == before


def test_missing_authority_record_cannot_silently_adopt_fenced_storage_as_primary(authority_store):
    authority_store.set_role("transfer", authority_store.commit_evidence(), "fenced")
    authority_store.db.execute("DELETE FROM storage_authority")
    path = authority_store.database_path
    authority_store.close()
    before = Path(path).read_bytes()
    with pytest.raises(StoreError, match="authority"):
        Store(path)
    assert Path(path).read_bytes() == before


def test_missing_authority_during_live_commit_fails_without_committing_money(authority_store):
    before = authority_store.commit_evidence()
    authority_store.db.execute(
        "CREATE TRIGGER lose_authority BEFORE UPDATE ON storage_authority "
        "BEGIN DELETE FROM storage_authority WHERE id=OLD.id; SELECT RAISE(IGNORE); END"
    )
    with pytest.raises(StoreError, match="missing"):
        authority_store.execute_operation(add_account(authority_store))
    assert authority_store.commit_evidence() == before
    assert authority_store.accounts("personal") == []
    authority_store.db.execute("DELETE FROM storage_authority")
    with pytest.raises(StoreError, match="missing"):
        authority_store.commit_evidence()


@pytest.mark.parametrize("corruption", ["column-id", "column-generation", "future-generation", "equal-generation-digest", "foreign-budget", "foreign-environment", "record-text", "record-json"])
def test_corrupt_operation_receipts_fail_lookup_export_and_reopen_without_rewriting(authority_store, corruption):
    request = add_account(authority_store)
    authority_store.execute_operation(request)
    row = authority_store.db.execute("SELECT operation_id,generation,record FROM storage_operations").fetchone()
    identifier, generation, value = row[0], row[1], read_storage_json(row[2])
    if corruption == "column-id":
        identifier = "different-sql-identity"
    elif corruption == "column-generation":
        generation = "not-an-integer"
    elif corruption == "future-generation":
        generation += 1
        value["evidence"]["generation"] = generation
    elif corruption == "equal-generation-digest":
        value["evidence"]["state_digest"] = "1" * 64
    elif corruption == "foreign-budget":
        value["evidence"]["budget_id"] = "other-budget"
    elif corruption == "foreign-environment":
        value["evidence"]["environment"] = "production"
    raw = json.dumps(value).encode()
    if corruption == "record-text":
        raw = raw.decode()
    if corruption == "record-json":
        raw = b"not-json"
    authority_store.db.execute("UPDATE storage_operations SET operation_id=?,generation=?,record=?", (identifier, generation, raw))
    for inspect in (lambda: authority_store.operation_receipt(identifier), authority_store.export_local):
        with pytest.raises(StoreError):
            inspect()
    path = authority_store.database_path
    authority_store.close()
    before = Path(path).read_bytes()
    with pytest.raises(StoreError):
        Store(path)
    assert Path(path).read_bytes() == before


def test_native_receipt_limit_rejects_4097_records_without_rewriting(authority_store):
    request = add_account(authority_store)
    authority_store.execute_operation(request)
    value = read_storage_json(authority_store.db.execute("SELECT record FROM storage_operations").fetchone()[0])
    records = []
    for index in range(1, 4097):
        identifier = f"fictional-receipt-{index}"
        copied = {**value, "operation_id": identifier, "evidence": {**value["evidence"], "operation_id": identifier}}
        records.append((identifier, copied["evidence"]["generation"], canonical_storage_json(copied)))
    authority_store.db.executemany("INSERT INTO storage_operations VALUES(?,?,?)", records)
    path = authority_store.database_path
    authority_store.close()
    before = Path(path).read_bytes()
    with pytest.raises(StoreError, match="bound"):
        Store(path)
    assert Path(path).read_bytes() == before


@pytest.mark.parametrize("raw", ["text-in-blob-column", 0])
def test_authority_record_sql_type_is_validated_before_decoding(authority_store, raw):
    authority_store.db.execute("UPDATE storage_authority SET record=?", (raw,))
    path = authority_store.database_path
    authority_store.close()
    before = Path(path).read_bytes()
    with pytest.raises(StoreError):
        Store(path)
    assert Path(path).read_bytes() == before


@pytest.mark.parametrize("outer_transaction", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_data_only_portable_builder_preserves_transaction_ownership_and_rolls_back_errors(tmp_path, outer_transaction, invalid):
    backend = Store(tmp_path / "data-only.sqlite", month="2026-10", _authority=False)
    try:
        if outer_transaction:
            backend.db.execute("BEGIN IMMEDIATE")
        payload = {"name": "Fictional", "type": "depository", "balance": True if invalid else 100}
        if invalid:
            with pytest.raises(StoreError):
                backend.add_account("personal", payload)
            assert backend.accounts("personal") == []
        else:
            backend.add_account("personal", payload)
            assert len(backend.accounts("personal")) == 1
        assert backend.db.in_transaction is outer_transaction
        if outer_transaction:
            backend.db.execute("ROLLBACK")
            assert backend.accounts("personal") == []
        assert not backend.db.execute("SELECT name FROM sqlite_schema WHERE name='storage_authority'").fetchall()
    finally:
        backend.close()


@pytest.mark.parametrize("committed", [False, True])
def test_constructor_authority_commit_failure_closes_handle_and_reopen_recovers(tmp_path, monkeypatch, committed):
    path = tmp_path / "startup.sqlite"
    original_connect = sqlite3.connect
    closed = []

    class FailedStartup(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            if statement == "COMMIT" and super().execute("SELECT name FROM sqlite_schema WHERE name='storage_authority'").fetchone():
                if committed:
                    super().execute(statement, parameters)
                raise OSError("fictional startup commit response loss")
            return super().execute(statement, parameters)

        def close(self):
            closed.append(self)
            return super().close()

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *args, **kwargs: original_connect(*args, **kwargs, factory=FailedStartup))
        with pytest.raises(OSError):
            Store(path, month="2026-10")
    assert len(closed) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        closed[0].execute("SELECT 1")
    # Transactional DDL rolls an uncommitted adoption back to a usable legacy
    # database. A committed adoption retains its valid identity after reply loss.
    recovered = Store(path)
    try:
        assert recovered.commit_evidence().role == "primary"
        assert recovered.commit_evidence().generation == 0
    finally:
        recovered.close()


@pytest.mark.parametrize("table", ["storage_authority", "storage_operations"])
def test_incomplete_authority_schema_is_rejected_without_recreating_control_tables(authority_store, table):
    authority_store.db.execute(f"DROP TABLE {table}")
    path = authority_store.database_path
    authority_store.close()
    before = Path(path).read_bytes()
    with pytest.raises(StoreError, match="incomplete"):
        Store(path)
    assert Path(path).read_bytes() == before


def test_provider_status_disconnect_and_missing_removals_advance_evidence_without_affecting_unrelated_account(
    authority_store, imported_account, sync_changes,
):
    manual = authority_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100})
    authority_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    authority_store.apply_sync("item", sync_changes(), "first", [imported_account()])
    before = authority_store.commit_evidence()
    authority_store.set_item_status("item", "error", "Fictional transient error")
    assert authority_store.get_item("item")["status"] == "error"
    assert authority_store.commit_evidence().generation == before.generation + 1
    removed = authority_store.apply_sync("item", sync_changes(removed=["already-absent"]), "after-absent")
    assert removed == {"added": 0, "modified": 0, "removed": 1}
    before = authority_store.commit_evidence()
    authority_store.disconnect_item("item")
    assert authority_store.commit_evidence().generation == before.generation + 1
    assert authority_store.get_item("item")["token"] is None
    assert authority_store.get_item("item")["cursor"] is None
    accounts = {row["id"]: row for row in authority_store.accounts("personal")}
    assert accounts["bank"]["stale"] and not accounts[manual]["stale"]


@pytest.mark.parametrize("mismatch", ["account-item", "account-provider", "existing-account", "transaction-account", "transaction-provider", "removed-transaction"])
def test_foreign_provider_records_reject_atomically_with_no_authority_advance(
    authority_store, imported_account, imported_transaction, sync_changes, mismatch,
):
    manual = authority_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100})
    authority_store.add_transaction("personal", {"id": "foreign", "accountId": manual, "date": "2026-10-01", "merchant": "Fictional", "amount": 1})
    authority_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    authority_store.apply_sync("item", sync_changes(), "before", [imported_account()])
    if mismatch == "account-item":
        changes, accounts = sync_changes(), [imported_account(itemId="other")]
    elif mismatch == "account-provider":
        changes, accounts = sync_changes(), [imported_account(source="other")]
    elif mismatch == "existing-account":
        changes, accounts = sync_changes(), [imported_account(id=manual)]
    elif mismatch == "transaction-account":
        changes, accounts = sync_changes(added=[imported_transaction(accountId=manual)]), []
    elif mismatch == "transaction-provider":
        changes, accounts = sync_changes(added=[imported_transaction(source="other")]), []
    else:
        changes, accounts = sync_changes(removed=["foreign"]), []
    before = authority_store.commit_evidence()
    original = authority_store.state("personal")
    with pytest.raises(StoreError, match="another connection|this connection"):
        authority_store.apply_sync("item", changes, "never-commit", accounts)
    assert authority_store.state("personal") == original
    assert authority_store.commit_evidence() == before
