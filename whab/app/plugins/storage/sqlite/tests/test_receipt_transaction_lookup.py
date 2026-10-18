"""Receipt identity checks stay bounded, read-only and scoped to active history."""

import sqlite3
from contextlib import closing

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.portable import read_files

from tests.receipt_images import png
from whab.plugins import StoreError


def populate(store):
    account = store.add_account("personal", {
        "name": "Synthetic receipt cash", "type": "depository", "balance": 10000,
    })
    for key in ("posted", "pending", "deleted", "historical", "transfer", "1", "café"):
        store.add_transaction("personal", {
            "id": key, "accountId": account,
            "date": "2020-01-01" if key == "historical" else "2026-10-01",
            "merchant": "Synthetic shop", "amount": 50, "categoryId": "groceries",
        })
    store.db.execute("UPDATE transactions SET pending=1 WHERE workspace='personal' AND id='pending'")
    store.db.execute("UPDATE transactions SET deleted=1 WHERE workspace='personal' AND id='deleted'")
    store.db.execute("UPDATE transactions SET is_transfer=1 WHERE workspace='personal' AND id='transfer'")
    # Inactive bank history remains eligible after a connection is removed.
    store.db.execute("UPDATE accounts SET active=0,source='plaid' WHERE workspace='personal' AND id=?", (account,))


@pytest.fixture
def receipt_ledger(store):
    populate(store)
    return store


@pytest.mark.parametrize("workspace,key", [
    ("personal", "posted"), ("personal", "pending"), ("personal", "historical"),
    ("personal", "transfer"), ("personal", "1"), ("personal", "café"),
    ("personal", "deleted"), ("personal", "missing"), ("demo", "posted"),
])
def test_lookup_matches_legacy_active_history_membership(receipt_ledger, workspace, key):
    expected = any(row["id"] == key for row in receipt_ledger.transactions(workspace))
    actual = receipt_ledger.receipt_transaction_exists(workspace, key)
    assert type(actual) is bool
    assert actual is expected


def test_string_subclasses_keep_their_existing_identity_semantics(receipt_ledger):
    class TransactionId(str):
        pass

    assert receipt_ledger.receipt_transaction_exists("personal", TransactionId("posted")) is True


@pytest.mark.parametrize("key", [None, True, False, 1, 1.0, [], {}, b"posted", bytearray(b"posted"), memoryview(b"posted")])
def test_malformed_ids_return_false_without_sql_affinity_or_binding_errors(receipt_ledger, key):
    assert receipt_ledger.receipt_transaction_exists("personal", key) is False


@pytest.mark.parametrize("workspace", ["invalid", "", None, [], True])
def test_workspace_validation_precedes_malformed_identity(store, workspace):
    with pytest.raises(StoreError, match="^Invalid workspace\\.$"):
        store.receipt_transaction_exists(workspace, [])
    assert not store.db.in_transaction


@pytest.mark.parametrize("key", ["posted", None, [], 1])
def test_missing_workspace_metadata_precedes_identity_result(receipt_ledger, key):
    receipt_ledger.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="^Workspace not found\\.$"):
        receipt_ledger.receipt_transaction_exists("personal", key)
    assert not receipt_ledger.db.in_transaction


def test_lookup_reads_only_indexed_identity_and_workspace_metadata(receipt_ledger, monkeypatch):
    store = receipt_ledger
    for method in ("state", "transactions", "accounts", "categories", "get_receipt"):
        monkeypatch.setattr(store, method, lambda *_: pytest.fail("Lookup must not read register or image data"))
    reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            reads.append((table, column))
            return sqlite3.SQLITE_OK if (table, column) in {
                ("meta", "workspace"), ("transactions", "workspace"),
                ("transactions", "id"), ("transactions", "deleted"),
            } else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in {
            sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION,
        } else sqlite3.SQLITE_DENY

    before, changes = store.db.serialize(), store.db.total_changes
    store.db.set_authorizer(authorize)
    try:
        assert store.receipt_transaction_exists("personal", "pending") is True
        assert store.receipt_transaction_exists("personal", "missing") is False
    finally:
        store.db.set_authorizer(None)
    assert {table for table, _ in reads} == {"meta", "transactions"}
    assert store.db.serialize() == before and store.db.total_changes == changes
    plan = [row["detail"] for row in store.db.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM transactions WHERE workspace=? AND id=? AND deleted=0",
        ("personal", "pending"),
    )]
    assert any("SEARCH transactions" in detail and "workspace=? AND id=?" in detail for detail in plan)


def test_lookup_preserves_outer_snapshot_and_legacy_optional_table_omissions(receipt_ledger):
    for table in ("future_funding", "rollover_modes", "transaction_receipts"):
        receipt_ledger.db.execute(f"DROP TABLE {table}")
    with receipt_ledger._snapshot():
        assert receipt_ledger.receipt_transaction_exists("personal", "historical") is True
        assert receipt_ledger.db.in_transaction
    assert not receipt_ledger.db.in_transaction
    for table in ("future_funding", "rollover_modes", "transaction_receipts"):
        assert receipt_ledger.db.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (table,)).fetchone() is None


def test_lookup_keeps_receipt_bytes_and_portable_image_unchanged(receipt_ledger):
    image = png()
    receipt_ledger.save_receipt("personal", "pending", image)
    files = receipt_ledger.export_budget_files()
    assert receipt_ledger.receipt_transaction_exists("personal", "pending") is True
    assert receipt_ledger.receipt_transaction_exists("personal", "deleted") is False
    assert receipt_ledger.get_receipt("personal", "pending") == image
    assert receipt_ledger.export_budget_files() == files
    assert read_files(files, with_receipts=True)[1] == [("pending", image)]


@pytest.mark.parametrize("operation", ["get", "save", "delete"])
def test_receipt_operations_recheck_identity_after_another_process_removes_it(tmp_path, operation):
    store = Store(tmp_path / "receipt-race.sqlite", month="2026-10")
    try:
        populate(store)
        assert store.receipt_transaction_exists("personal", "posted") is True
        with closing(sqlite3.connect(store.database_path)) as writer, writer:
            writer.execute("UPDATE transactions SET deleted=1 WHERE workspace='personal' AND id='posted'")
        with pytest.raises(StoreError, match="^Transaction not found\\.$"):
            if operation == "save":
                store.save_receipt("personal", "posted", png())
            else:
                getattr(store, operation + "_receipt")("personal", "posted")
    finally:
        store.close()
