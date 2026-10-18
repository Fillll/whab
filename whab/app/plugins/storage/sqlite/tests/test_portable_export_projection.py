"""Fresh portable exports reuse validated snapshots without bypassing validation."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store, portable
from whab_plugin_sqlite import store as store_module

from tests.receipt_images import png
from whab.domain.portable import (
    ACCOUNT_FIELDS,
    CATEGORY_FIELDS,
    TRANSACTION_FIELDS,
    validate_portable_budget,
)
from whab.plugins import StoreError


def previous_snapshot(store, workspace):
    """Reference the original full-domain-state public snapshot contract."""
    state = store.state(workspace)
    return validate_portable_budget({
        "formatVersion": 1, "budgetStartMonth": state["budgetStartMonth"],
        "accounts": [
            {**{key: row[key] for key in ACCOUNT_FIELDS},
             **{key: row[key] for key in ("bankWebsite", "nameOverridden") if key in row}}
            for row in state["accounts"].values()
        ],
        "categories": [{key: row[key] for key in CATEGORY_FIELDS} for row in state["categories"].values()],
        "transactions": [{key: row[key] for key in TRANSACTION_FIELDS} for row in state["transactions"].values()],
        "assignments": [{"month": month, "categoryId": key, "assigned": amount}
                        for month, rows in state["budget"].items() for key, amount in rows.items()],
        "futureFunding": [{"month": month, "currency": currency, "assigned": amount}
                          for month, rows in sorted(state["futureFunding"].items())
                          for currency, amount in sorted(rows.items())],
        "rolloverModes": [{"month": month, "currency": currency, "mode": mode}
                          for month, rows in sorted(state["rolloverModes"].items())
                          for currency, mode in sorted(rows.items())],
    })


def previous_export(store, workspace):
    """Reference the original public restore route and exact database encoding."""
    snapshot = previous_snapshot(store, workspace)
    images = [(key, store.get_receipt(workspace, key)) for key in store.receipt_ids(workspace)]
    clean = Store(":memory:", month=snapshot["budgetStartMonth"])
    try:
        clean.restore_budget("personal", snapshot)
        for key, image in images:
            clean.db.execute("INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES('personal',?,?)",
                             (key, image))
        for table in ("meta", *portable._ROW_LIMITS):
            clean.db.execute(f"DELETE FROM {table} WHERE workspace <> 'personal'")
        clean.db.execute("VACUUM")
        clean.db.execute("PRAGMA user_version=1")
        return {"budget.sqlite": clean.db.serialize()}
    finally:
        clean.close()


@pytest.fixture
def populated_export(store, imported_account, imported_transaction, sync_changes):
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 100000})
    key = store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Synthetic purchase",
        "amount": 100, "categoryId": "groceries", "note": "Saved annotation",
    })
    store.update_account("personal", account, {"name": "Renamed cash", "bankWebsite": "https://bank.example.org"})
    store.add_account("personal", {"name": "Yen", "type": "depository", "balance": 1000, "currency": "JPY"})
    store.add_category("personal", {"name": "Yen food", "group": "Travel", "target": 100, "currency": "JPY"})
    store.assign("personal", "groceries", "2026-10", 1000)
    store.assign_next_month("personal", "2026-10", "USD", 2000)
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    store.save_item("private-storage-item", "private-storage-token", "Synthetic Bank")
    store.apply_sync("private-storage-item", sync_changes(added=[
        imported_transaction("active"), imported_transaction("removed"),
    ]), "private-storage-cursor", [imported_account(itemId="private-storage-item", mask="9876")])
    store.apply_sync("private-storage-item", sync_changes(removed=["removed"]), "private-final-cursor")
    store.save_receipt("personal", key, png())
    return store


@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_export_matches_public_restore_data_with_one_full_validation(populated_export, monkeypatch, workspace):
    store = populated_export
    expected = previous_export(store, workspace)
    before = store.state("personal")
    validations = []
    validate = store_module.validate_portable_budget

    def counted_validation(snapshot):
        validations.append(snapshot)
        return validate(snapshot)

    monkeypatch.setattr(store_module, "validate_portable_budget", counted_validation)
    files = store.export_budget_files(workspace)
    assert len(validations) == 1
    assert len(files["budget.sqlite"]) < len(expected["budget.sqlite"])
    assert portable.read_files(files, with_receipts=True) == portable.read_files(expected, with_receipts=True)
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(files["budget.sqlite"])
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='index'")}
        assert not indexes.intersection(portable._ADDITIVE_INDEXES)
        assert "tx_month" in indexes
    finally:
        connection.close()
    assert store.state("personal") == before
    for private in (b"private-storage-item", b"private-storage-token", b"private-storage-cursor", b"private-final-cursor"):
        assert private not in files["budget.sqlite"]
    snapshot, images = portable.read_files(files, with_receipts=True)
    assert snapshot["transactions"]
    if workspace == "personal":
        assert images and images[0][1] == png()
        assert next(row for row in snapshot["accounts"] if row["id"] == "bank")["stale"] is True
        assert next(row for row in snapshot["transactions"] if row["id"] == "removed")["deleted"] is True
    else:
        assert images == []


@pytest.mark.parametrize("sql,parameters", [
    ("UPDATE transactions SET amount=? WHERE workspace='personal'", (1.5,)),
    ("UPDATE transactions SET category_id=? WHERE workspace='personal'", ("missing",)),
    ("UPDATE transactions SET date=? WHERE workspace='personal'", ("2026-02-30",)),
    ("UPDATE transactions SET note=? WHERE workspace='personal'", ("x" * 1001,)),
])
def test_export_still_validates_untrusted_runtime_records_before_populating_clean_database(
    populated_export, monkeypatch, sql, parameters,
):
    store = populated_export
    store.db.execute(sql, parameters)
    before = store.state("personal")
    monkeypatch.setattr(Store, "_restore_validated_snapshot",
                        lambda *_: pytest.fail("Invalid runtime data must never populate an export"))
    with pytest.raises(StoreError):
        store.export_budget_files()
    assert store.state("personal") == before


def test_public_restore_keeps_full_validation_and_pristine_workspace_guards(populated_export):
    snapshot = populated_export.export_budget()
    target = Store(":memory:", month="2026-10")
    try:
        before = target.state("personal")
        snapshot["transactions"][0]["amount"] = 1.5
        with pytest.raises(StoreError, match="integer minor units"):
            target.restore_budget("personal", snapshot)
        assert target.state("personal") == before
        snapshot = populated_export.export_budget()
        target.restore_budget("personal", snapshot)
        saved = target.state("personal")
        with pytest.raises(StoreError, match="empty personal workspace"):
            target.restore_budget("personal", snapshot)
        assert target.state("personal") == saved
    finally:
        target.close()


@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_direct_export_matches_original_snapshot_without_loading_history_twice(populated_export, monkeypatch, workspace):
    store = populated_export
    expected = previous_snapshot(store, workspace)
    queries = []
    store.db.set_trace_callback(queries.append)
    monkeypatch.setattr(store, "state", lambda *_: pytest.fail("Portable projection must not load full domain state"))
    monkeypatch.setattr(store, "_read_state", lambda *_: pytest.fail("Portable projection must not read bank state"))
    try:
        actual = store.export_budget(workspace)
    finally:
        store.db.set_trace_callback(None)
    assert actual == expected
    assert not any("FROM items" in query for query in queries)
    assert sum("FROM transactions" in query for query in queries) == 1
    assert queries[0] == "BEGIN" and queries[-1] == "COMMIT"
    actual["transactions"][0]["note"] = "Changed outside storage"
    monkeypatch.undo()
    assert store.export_budget(workspace) == expected


@pytest.mark.parametrize("workspace", [None, [], "invalid"])
def test_direct_export_preserves_workspace_errors(store, workspace):
    with pytest.raises(StoreError, match="Invalid workspace"):
        store.export_budget(workspace)
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="Workspace not found"):
        store.export_budget("personal")


def test_export_metadata_transactions_and_receipts_share_one_snapshot(tmp_path, monkeypatch):
    store = Store(tmp_path / "coherent-export.sqlite", month="2026-10")
    writer = sqlite3.connect(store.database_path, isolation_level=None)
    try:
        account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
        key = store.add_transaction("personal", {
            "accountId": account, "date": "2026-10-01", "merchant": "Synthetic purchase",
            "amount": 100, "categoryId": None, "note": "Before",
        })
        store.save_receipt("personal", key, png())
        original = store._read_accounts
        changed = False

        def accounts(workspace):
            nonlocal changed
            result = original(workspace)
            if not changed:
                changed = True
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE accounts SET balance=20000 WHERE workspace=? AND id=?", (workspace, account))
                writer.execute("UPDATE transactions SET note='After' WHERE workspace=? AND id=?", (workspace, key))
                writer.execute("UPDATE transaction_receipts SET image=? WHERE workspace=? AND transaction_id=?",
                               (png(2, 1), workspace, key))
                writer.execute("COMMIT")
            return result

        monkeypatch.setattr(store, "_read_accounts", accounts)
        first, images = portable.read_files(store.export_budget_files(), with_receipts=True)
        assert first["accounts"][0]["balance"] == 9900
        assert first["transactions"][0]["note"] == "Before" and images == [(key, png())]
        second, images = portable.read_files(store.export_budget_files(), with_receipts=True)
        assert second["accounts"][0]["balance"] == 20000
        assert second["transactions"][0]["note"] == "After" and images == [(key, png(2, 1))]
    finally:
        writer.close()
        store.close()
