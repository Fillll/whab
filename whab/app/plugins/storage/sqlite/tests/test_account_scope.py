"""SQLite preserves core account classifications through every ledger boundary."""

import sqlite3
from copy import deepcopy

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.schema import initialize_schema

from whab.domain.errors import StoreError


def test_account_classification_survives_reopen_and_both_portable_formats(tmp_path):
    path = tmp_path / "accounts.sqlite"
    original = Store(path, month="2026-10")
    try:
        checking = original.add_account("personal", {
            "name": "Checking", "type": "depository", "balance": 10000,
        })
        subtypes = {
            "investment": "brokerage", "retirement": "401k", "property": "home",
            "loan": "mortgage", "deposit": "cd", "other_asset": None,
            "other_liability": None, "other": None,
        }
        for kind, subtype in subtypes.items():
            original.add_account("personal", {
                "name": kind, "type": kind, "subtype": subtype, "balance": 20000,
            })
        original.update_account("personal", checking, {"onBudget": False})
        snapshot = original.export_budget()
        images = original.export_budget_files()
        assert all(account["onBudget"] is False for account in snapshot["accounts"])
        assert {account["type"]: account["subtype"] for account in snapshot["accounts"]} == {
            "depository": None, **subtypes,
        }
        assert original.db.execute(
            "SELECT on_budget,subtype FROM accounts WHERE workspace='personal' AND id=?", (checking,),
        ).fetchone()[:] == (0, None)
    finally:
        original.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.export_budget() == snapshot
        assert all(account["onBudget"] is True for account in reopened.accounts("demo"))
    finally:
        reopened.close()
    for binary in (False, True):
        target = Store(":memory:", month="2026-11")
        try:
            if binary:
                target.restore_budget_files("personal", images)
            else:
                target.restore_budget("personal", snapshot)
            assert target.export_budget() == snapshot
        finally:
            target.close()


def test_legacy_account_metadata_migration_preserves_money_history_and_scope_defaults(tmp_path):
    path = tmp_path / "legacy.sqlite"
    original = Store(path, month="2026-10")
    try:
        ids = {
            kind: original.add_account("personal", {"name": kind, "type": kind, "balance": 10000})
            for kind in ("depository", "credit", "investment", "other")
        }
        transaction_id = original.add_transaction("personal", {
            "accountId": ids["depository"], "date": "2026-10-01", "merchant": "Fixture",
            "amount": 123, "categoryId": "groceries", "note": "Keep annotation",
        })
        before = deepcopy(original.state("personal"))
    finally:
        original.close()
    with sqlite3.connect(path) as legacy:
        legacy.execute("ALTER TABLE accounts DROP COLUMN on_budget")
        legacy.execute("ALTER TABLE accounts DROP COLUMN subtype")
    for _ in range(2):
        reopened = Store(path, month="2026-11")
        try:
            assert reopened.state("personal") == before
            assert reopened.state("personal")["transactions"][transaction_id]["note"] == "Keep annotation"
            assert {row["type"]: row["onBudget"] for row in reopened.accounts("personal")} == {
                "depository": True, "credit": True, "investment": False, "other": False,
            }
        finally:
            reopened.close()


def test_legacy_portable_sqlite_image_is_read_as_data_with_inferred_scope(store):
    store.add_account("personal", {"name": "Investments", "type": "investment", "balance": 10000})
    store.add_account("personal", {"name": "Checking", "type": "depository", "balance": 5000})
    expected = store.export_budget()
    raw = store.export_budget_files()["budget.sqlite"]
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute("ALTER TABLE accounts DROP COLUMN on_budget")
        connection.execute("ALTER TABLE accounts DROP COLUMN subtype")
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    target = Store(":memory:", month="2026-11")
    try:
        target.restore_budget_files("personal", files)
        assert target.export_budget() == expected
        assert "on_budget" in {
            row["name"] for row in target.db.execute("PRAGMA table_info(accounts)")
        }
    finally:
        target.close()


def test_unknown_legacy_provider_type_opens_as_off_budget_other_without_losing_history(tmp_path):
    path = tmp_path / "provider.sqlite"
    original = Store(path, month="2026-10")
    try:
        account_id = original.add_account("personal", {
            "name": "Provider account", "type": "other", "balance": 12345,
        })
    finally:
        original.close()
    with sqlite3.connect(path) as legacy:
        legacy.execute("UPDATE accounts SET type='future-provider-type' WHERE id=?", (account_id,))
        legacy.execute("ALTER TABLE accounts DROP COLUMN on_budget")
        legacy.execute("ALTER TABLE accounts DROP COLUMN subtype")
    reopened = Store(path, month="2026-11")
    try:
        account = reopened.accounts("personal")[0]
        assert (account["id"], account["type"], account["onBudget"], account["balance"]) == (
            account_id, "other", False, 12345,
        )
        assert reopened.db.execute(
            "SELECT type FROM accounts WHERE id=?", (account_id,)
        ).fetchone()[0] == "future-provider-type"
        assert reopened.export_budget()["accounts"][0] == account
    finally:
        reopened.close()


def test_failed_scope_backfill_rolls_back_column_so_retry_preserves_legacy_defaults(tmp_path):
    path = tmp_path / "interrupted.sqlite"
    original = Store(path, month="2026-10")
    try:
        account_id = original.add_account("personal", {
            "name": "Investments", "type": "investment", "balance": 20000,
        })
    finally:
        original.close()
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("ALTER TABLE accounts DROP COLUMN on_budget")
        connection.execute("ALTER TABLE accounts DROP COLUMN subtype")

        def reject_backfill(action, table, column, database, trigger):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and table == "accounts"
                else sqlite3.SQLITE_OK
            )

        connection.set_authorizer(reject_backfill)
        with pytest.raises(sqlite3.DatabaseError):
            initialize_schema(connection, "2026-11")
        connection.set_authorizer(None)
        assert "on_budget" not in {
            row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
        }
        assert connection.execute("SELECT balance FROM accounts WHERE id=?", (account_id,)).fetchone()[0] == 20000
    finally:
        connection.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.accounts("personal")[0]["onBudget"] is False
        assert reopened.accounts("personal")[0]["balance"] == 20000
    finally:
        reopened.close()


def test_provider_refresh_preserves_edited_scope_and_classification(
    store, imported_account, save_item, sync_changes,
):
    save_item(store)
    store.apply_sync("item", sync_changes(), "first", [imported_account()])
    store.update_account("personal", "bank", {
        "type": "investment", "onBudget": False, "subtype": "brokerage",
    })
    store.apply_sync("item", sync_changes(), "second", [imported_account(balance=15000)])
    account = store.accounts("personal")[0]
    assert (account["type"], account["onBudget"], account["subtype"], account["balance"]) == (
        "investment", False, "brokerage", 15000,
    )
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.update_account("personal", "bank", {"onBudget": "false"})
    assert store.state("personal") == before


def test_unrecognized_partial_metadata_schema_is_rejected(store):
    files = store.export_budget_files()
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(files["budget.sqlite"])
        connection.execute("ALTER TABLE accounts DROP COLUMN subtype")
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    target = Store(":memory:", month="2026-10")
    try:
        with pytest.raises(StoreError, match="unsupported database schema"):
            target.restore_budget_files("personal", files)
    finally:
        target.close()
