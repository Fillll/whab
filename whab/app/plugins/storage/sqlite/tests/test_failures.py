"""Storage failures preserve committed records and transaction ownership."""

import sqlite3

import pytest
import whab_sqlite
from whab_sqlite import Store
from whab_sqlite.schema import initialize_schema

from whab.domain.categories import category_group_id
from whab.domain.errors import StoreError


def manual_account(store):
    return store.add_account(
        "personal", {"name": "Fictional wallet", "type": "depository", "balance": 10000}
    )


def manual_transaction(account_id, **extra):
    return {
        "id": "manual-fixture",
        "accountId": account_id,
        "date": "2026-10-01",
        "merchant": "Fixture market",
        "amount": 1000,
        "categoryId": "groceries",
        **extra,
    }


def test_unknown_lazy_export_raises_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute 'MissingBackend'"):
        getattr(whab_sqlite, "MissingBackend")


def test_failed_household_seeding_rolls_back_then_can_be_retried(tmp_path, monkeypatch):
    path = tmp_path / "seed.sqlite"
    prepared = sqlite3.connect(path, isolation_level=None)
    prepared.row_factory = sqlite3.Row
    initialize_schema(prepared, "2026-10")
    prepared.execute(
        "CREATE TRIGGER reject_household BEFORE INSERT ON categories "
        "WHEN NEW.workspace='personal' BEGIN SELECT RAISE(ABORT,'fixture seed failure'); END"
    )
    prepared.close()
    opened = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="fixture seed failure"):
            Store(path, month="2026-10")
        failed = opened[0]
        assert failed.in_transaction is False
        assert failed.execute("SELECT 1 FROM meta WHERE workspace='demo'").fetchone()
        assert not failed.execute("SELECT 1 FROM meta WHERE workspace='personal'").fetchone()
        assert not failed.execute("SELECT 1 FROM categories WHERE workspace='personal'").fetchone()
        failed.execute("DROP TRIGGER reject_household")
    finally:
        for connection in opened:
            connection.close()
    recovered = Store(path, month="2026-10")
    try:
        assert len(recovered.categories("personal")) == 9
        assert len(recovered.transactions("demo")) == 55
    finally:
        recovered.close()


@pytest.mark.parametrize("outer_transaction", [False, True])
def test_failed_snapshot_preserves_outer_transaction_ownership(store, outer_transaction):
    if outer_transaction:
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute("UPDATE categories SET name='Uncommitted fixture' WHERE id='fun'")

    def deny_accounts(action, table, column, database, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "accounts" else sqlite3.SQLITE_OK

    store.db.set_authorizer(deny_accounts)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            store.state("personal")
        assert store.db.in_transaction is outer_transaction
    finally:
        store.db.set_authorizer(None)
    if outer_transaction:
        name = store.db.execute(
            "SELECT name FROM categories WHERE workspace='personal' AND id='fun'"
        ).fetchone()[0]
        assert name == "Uncommitted fixture"
        store.db.execute("ROLLBACK")
    assert store.state("personal")["categories"]["fun"]["name"] == "Fun"


def test_missing_workspace_metadata_fails_without_leaving_a_read_transaction(store):
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="Workspace not found"):
        store.state("personal")
    assert store.db.in_transaction is False
    assert store.state("demo")["_id"] == "demo"


def test_invalid_account_type_does_not_create_an_account(store):
    with pytest.raises(StoreError, match="Invalid account type"):
        store.add_account("personal", {"name": "Fixture", "type": "unsupported", "balance": 0})
    assert store.accounts("personal") == []


def test_custom_category_is_persisted_and_negative_target_is_rejected(store):
    category_id = store.add_category(
        "personal", {"name": "  Child care  ", "group": "  Family  ", "target": 12345}
    )
    category = next(record for record in store.categories("personal") if record["id"] == category_id)
    assert category == {
        "id": category_id, "name": "Child care", "group": "Family", "target": 12345,
        "parentId": category_group_id("Family"), "currency": "USD",
    }
    before = store.categories("personal")
    with pytest.raises(StoreError, match="Target cannot be negative"):
        store.add_category("personal", {"name": "Invalid", "group": "Family", "target": -1})
    assert store.categories("personal") == before
    assert len(store.categories("demo")) == 9


@pytest.mark.parametrize("note", [None, "x" * 1001])
def test_invalid_notes_do_not_change_balance_or_ledger(store, note):
    account_id = manual_account(store)
    before = store.state("personal")
    with pytest.raises(StoreError, match="Invalid note"):
        store.add_transaction("personal", manual_transaction(account_id, note=note))
    assert store.state("personal") == before


def test_unknown_category_and_duplicate_transaction_roll_back_money_changes(store):
    account_id = manual_account(store)
    before = store.state("personal")
    with pytest.raises(StoreError, match="Category not found"):
        store.add_transaction("personal", manual_transaction(account_id, categoryId="absent"))
    assert store.state("personal") == before
    store.add_transaction("personal", manual_transaction(account_id))
    committed = store.state("personal")
    with pytest.raises(StoreError, match="Transaction already exists"):
        store.add_transaction("personal", manual_transaction(account_id, amount=2000))
    assert store.state("personal") == committed


def test_invalid_edits_and_negative_assignment_preserve_saved_records(store):
    account_id = manual_account(store)
    transaction_id = store.add_transaction("personal", manual_transaction(account_id))
    before = store.state("personal")
    with pytest.raises(StoreError, match="Category not found"):
        store.edit_transaction("personal", transaction_id, "absent")
    with pytest.raises(StoreError, match="Invalid review status"):
        store.edit_transaction("personal", transaction_id, reviewed="true")
    with pytest.raises(StoreError, match="Assignment cannot be negative"):
        store.assign("personal", "groceries", "2026-10", -1)
    assert store.state("personal") == before


def test_connection_cannot_be_reassigned_to_a_different_provider(store):
    store.save_item("item", "fixture-ciphertext", "Fixture Bank", provider_id="alpha")
    before = store.get_item("item")
    with pytest.raises(StoreError, match="another bank provider"):
        store.save_item("item", "replacement-ciphertext", "Other Bank", provider_id="beta")
    assert store.get_item("item") == before


@pytest.mark.parametrize("disconnected", [False, True])
def test_absent_and_disconnected_connections_cannot_be_changed_or_synced(store, disconnected):
    if disconnected:
        store.save_item("item", "fixture-ciphertext", "Fixture Bank")
        store.disconnect_item("item")
    before = store.state("personal")
    store.set_item_status("item", "error", "Fixture error")
    assert store.state("personal") == before
    with pytest.raises(StoreError, match="Connection not found"):
        store.disconnect_item("item")
    with pytest.raises(StoreError, match="Connection not found"):
        store.apply_sync("item", {"added": [], "modified": [], "removed": []}, "advance")
    assert store.state("personal") == before


@pytest.mark.parametrize("collision", ["existing", "pending"])
def test_import_rejects_foreign_transaction_annotations(
    store, save_item, imported_account, imported_transaction, sync_changes, collision
):
    save_item(store)
    account_id = manual_account(store)
    store.add_transaction("personal", manual_transaction(account_id, id="foreign"))
    store.apply_sync("item", sync_changes(), "one", [imported_account()])
    before = store.state("personal")
    transaction = imported_transaction(
        "foreign" if collision == "existing" else "posted",
        pendingTransactionId="foreign" if collision == "pending" else None,
    )
    with pytest.raises(StoreError, match="another connection"):
        store.apply_sync("item", sync_changes([transaction]), "advance", [imported_account(99900)])
    assert store.state("personal") == before


def test_import_rejects_annotations_pointing_at_a_missing_saved_category(
    store, save_item, imported_account, imported_transaction, sync_changes
):
    save_item(store)
    store.apply_sync("item", sync_changes([imported_transaction()]), "one", [imported_account()])
    store.edit_transaction("personal", "tx", "fun", True)
    # A legacy database with a removed category must fail safely when merging user edits.
    store.db.execute("DELETE FROM categories WHERE workspace='personal' AND id='fun'")
    before = store.state("personal")
    with pytest.raises(StoreError, match="Category not found"):
        store.apply_sync(
            "item", sync_changes(modified=[imported_transaction(amount=2000)]), "advance",
            [imported_account(99900)],
        )
    assert store.state("personal") == before


def test_transaction_without_its_account_rolls_back_new_accounts_and_cursor(
    store, save_item, imported_account, imported_transaction, sync_changes
):
    save_item(store)
    before = store.state("personal")
    with pytest.raises(StoreError, match="Bank account data missing"):
        store.apply_sync(
            "item", sync_changes([imported_transaction()]), "advance",
            [imported_account(id="unrelated")],
        )
    assert store.state("personal") == before
