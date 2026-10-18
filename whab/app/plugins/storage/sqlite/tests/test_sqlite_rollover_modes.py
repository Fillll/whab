"""Rollover choices remain currency scoped, atomic and portable."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store

from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport


def test_rollover_choices_survive_reopen_and_both_portable_formats(tmp_path):
    path = tmp_path / "rollover.sqlite"
    original = Store(path, month="2026-10")
    try:
        original.set_rollover_mode("personal", "2026-10", "USD", "balance")
        original.set_rollover_mode("personal", "2026-10", "CAD", "category")
        original.set_rollover_mode("personal", "2026-11", "CAD", "balance")
        expected = {
            "2026-10": {"USD": "balance", "CAD": "category"},
            "2026-11": {"CAD": "balance"},
        }
        snapshot, files = original.export_budget(), original.export_budget_files()
        assert original.state("personal")["rolloverModes"] == expected
        assert original.state("demo")["rolloverModes"] == {}
        assert snapshot["rolloverModes"] == [
            {"month": "2026-10", "currency": "CAD", "mode": "category"},
            {"month": "2026-10", "currency": "USD", "mode": "balance"},
            {"month": "2026-11", "currency": "CAD", "mode": "balance"},
        ]
    finally:
        original.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.export_budget() == snapshot
        assert reopened.state("personal")["rolloverModes"] == expected
    finally:
        reopened.close()
    for binary in (False, True):
        target = Store(":memory:", month="2026-11")
        try:
            if binary:
                target.restore_budget_files("personal", files)
            else:
                target.restore_budget("personal", snapshot)
            assert target.export_budget() == snapshot
            assert target.state("personal")["rolloverModes"] == expected
            changes = target.db.total_changes
            target.prepare_budget_files("personal", files)
            assert target.db.total_changes == changes
        finally:
            target.close()


def test_monthly_display_is_independent_of_current_date_without_weakening_later_guards(store, monkeypatch):
    import whab_plugin_sqlite.store as module

    monkeypatch.setattr(module, "current_month", lambda: "2026-11")
    store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.assign("personal", "rent", "2026-11", 8000)
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 10000
    assert store.workspace("personal")["month"] == "2026-11"
    before = store.state("personal")
    with pytest.raises(StoreError, match="available money"):
        store.assign("personal", "groceries", "2026-10", 3000)
    assert store.state("personal") == before
    monkeypatch.setattr(module, "current_month", lambda: "2026-10")
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 10000
    assert store.workspace("personal", "2026-11")["summary"]["fundingCapacity"] == 2000


def test_saved_rollover_mode_changes_only_its_currency_without_adding_cash(store):
    cash = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.add_account("personal", {
        "name": "Canadian cash", "type": "depository", "balance": 10000, "currency": "CAD",
    })
    canadian = store.add_category("personal", {"name": "Food", "group": "Travel", "currency": "CAD"})
    store.assign("personal", "groceries", "2026-10", 3000)
    store.assign("personal", canadian, "2026-10", 4000)
    store.add_transaction("personal", {
        "accountId": cash, "date": "2026-10-01", "merchant": "Shop", "amount": 500,
        "categoryId": "groceries",
    })
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    november = store.workspace("personal", "2026-11")
    rows = {row["categoryId"]: row for row in november["budget"]}
    assert rows["groceries"]["carryover"] == 0
    assert rows[canadian]["carryover"] == 4000
    assert november["summariesByCurrency"]["USD"]["balanceCarryover"] == 2500
    assert november["summariesByCurrency"]["USD"]["readyToAssign"] == 9500
    assert november["summariesByCurrency"]["CAD"]["balanceCarryover"] == 0
    assert november["summariesByCurrency"]["CAD"]["readyToAssign"] == 6000
    assert {account["currency"]: account["balance"] for account in store.accounts("personal")} == {
        "USD": 9500, "CAD": 10000,
    }


@pytest.mark.parametrize("choice,pool,category_carry", [
    ("category", 0, 2500), ("balance", 2500, 0),
])
@pytest.mark.parametrize("binary", [False, True])
def test_portable_roundtrip_preserves_unassigned_set_aside_and_optional_pooled_carry(
    store, choice, pool, category_carry, binary,
):
    cash = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.assign("personal", "groceries", "2026-10", 3000)
    store.add_transaction("personal", {
        "accountId": cash, "date": "2026-10-01", "merchant": "Shop", "amount": 500,
        "categoryId": "groceries",
    })
    store.assign_next_month("personal", "2026-10", "USD", 2000)
    store.set_rollover_mode("personal", "2026-10", "USD", choice)
    expected = store.workspace("personal", "2026-11")
    summary = expected["summary"]
    assert summary["carriedInUnassigned"] == 5000
    assert summary["carriedInSetAside"] == 2000
    assert summary["balanceCarryover"] == pool
    assert summary["carriedIn"] == summary["readyToAssign"] == 7000 + pool
    assert next(row for row in expected["budget"] if row["categoryId"] == "groceries")[
        "carryover"
    ] == category_carry
    target = Store(":memory:", month="2026-11")
    try:
        if binary:
            target.restore_budget_files("personal", store.export_budget_files())
        else:
            target.restore_budget("personal", store.export_budget())
        assert target.workspace("personal", "2026-11") == expected
        assert target.accounts("personal")[0]["balance"] == 9500
    finally:
        target.close()


def test_restoring_reallocated_category_carryover_cannot_underfund_today(store, monkeypatch):
    import whab_plugin_sqlite.store as module

    monkeypatch.setattr(module, "current_month", lambda: "2026-11")
    cash = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.assign("personal", "rent", "2026-10", 10000)
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    store.assign("personal", "groceries", "2026-11", 10000)
    before = store.state("personal")
    with pytest.raises(StoreError, match="available money"):
        store.set_rollover_mode("personal", "2026-10", "USD", "category")
    assert store.state("personal") == before
    assert store.workspace("personal", "2026-11")["summary"]["readyToAssign"] == 0
    # An existing external deficit does not prohibit a non-worsening setting.
    store.db.execute("UPDATE accounts SET balance=9000 WHERE id=?", (cash,))
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    assert store.workspace("personal", "2026-11")["summary"]["readyToAssign"] == -1000


@pytest.mark.parametrize("change", ["assignment", "rollover"])
def test_later_balance_reset_cannot_hide_an_intermediate_historical_deficit(store, monkeypatch, change):
    import whab_plugin_sqlite.store as module

    monkeypatch.setattr(module, "current_month", lambda: "2026-12")
    store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.set_rollover_mode("personal", "2026-11", "USD", "balance")
    if change == "rollover":
        store.assign("personal", "rent", "2026-10", 10000)
        store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    store.assign("personal", "groceries", "2026-11", 10000)
    assert store.workspace("personal", "2026-11")["summary"]["readyToAssign"] == 0
    assert store.workspace("personal", "2026-12")["summary"]["readyToAssign"] == 10000
    before = store.state("personal")
    with pytest.raises(StoreError, match="available money"):
        if change == "assignment":
            store.assign("personal", "rent", "2026-10", 10000)
        else:
            store.set_rollover_mode("personal", "2026-10", "USD", "category")
    assert store.state("personal") == before


@pytest.mark.parametrize("month,currency,mode", [
    ("2026-09", "USD", "balance"), ("invalid", "USD", "category"),
    ("2026-10", "usd", "balance"), ("2026-10", "USD", "unknown"),
    ("2026-10", "USD", None), ("2026-10", "USD", {}),
])
def test_invalid_rollover_choices_cannot_mutate_saved_state(store, month, currency, mode):
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.set_rollover_mode("personal", month, currency, mode)
    assert store.state("personal") == before
    assert not store.db.in_transaction


def test_database_failure_rolls_back_rollover_replacement(store):
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    before = store.state("personal")
    store.db.execute("CREATE TRIGGER reject_rollover BEFORE INSERT ON rollover_modes BEGIN SELECT RAISE(ABORT,'fixture rollover failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture rollover failure"):
        store.set_rollover_mode("personal", "2026-10", "USD", "category")
    assert store.state("personal") == before
    assert not store.db.in_transaction


def test_rollover_setting_alone_prevents_empty_workspace_import_and_restore(store):
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    before = store.state("personal")
    assert store.preview_workspace_import("personal", "fixture", "digest")["canImport"] is False
    with pytest.raises(StoreError, match="empty personal workspace"):
        store.import_workspace("personal", "fixture", "digest", WorkspaceImport((), (), (), (), "2026-10"))
    with pytest.raises(StoreError, match="empty personal workspace"):
        store.restore_budget("personal", store.export_budget("demo"))
    assert store.state("personal") == before


@pytest.mark.parametrize("legacy_accounts", [False, True])
@pytest.mark.parametrize("omit_funding", [False, True])
def test_every_previous_portable_schema_remains_supported(store, legacy_accounts, omit_funding):
    snapshot = store.export_budget()
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(store.export_budget_files()["budget.sqlite"])
        connection.execute("DROP TABLE rollover_modes")
        if omit_funding:
            connection.execute("DROP TABLE future_funding")
        if legacy_accounts:
            connection.execute("ALTER TABLE accounts DROP COLUMN on_budget")
            connection.execute("ALTER TABLE accounts DROP COLUMN subtype")
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    target = Store(":memory:", month="2026-11")
    try:
        target.restore_budget_files("personal", files)
        assert target.export_budget() == snapshot
        assert target.state("personal")["rolloverModes"] == {}
    finally:
        target.close()


def test_neutral_snapshot_without_rollover_settings_restores_canonical_defaults(store):
    snapshot = store.export_budget()
    snapshot.pop("rolloverModes")
    store.restore_budget("personal", snapshot)
    assert store.export_budget() == {**snapshot, "rolloverModes": []}


@pytest.mark.parametrize("statement", [
    "INSERT INTO rollover_modes VALUES('personal','invalid','USD','balance')",
    "INSERT INTO rollover_modes VALUES('personal','2026-09','USD','balance')",
    "INSERT INTO rollover_modes VALUES('personal','2026-10','usd','balance')",
    "INSERT INTO rollover_modes VALUES('personal','2026-10','USD','unknown')",
    "INSERT INTO rollover_modes VALUES('demo','2026-10','USD','category')",
])
def test_portable_rollover_rows_are_revalidated_as_untrusted_data(store, statement):
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(store.export_budget_files()["budget.sqlite"])
        connection.execute(statement)
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.restore_budget_files("personal", files)
    assert store.state("personal") == before


def test_portable_rollover_record_count_is_bounded_before_projection(store, monkeypatch):
    from whab_plugin_sqlite import portable

    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(store.export_budget_files()["budget.sqlite"])
        connection.execute("INSERT INTO rollover_modes VALUES('personal','2026-10','USD','balance')")
        connection.execute("INSERT INTO rollover_modes VALUES('personal','2026-11','USD','category')")
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    monkeypatch.setitem(portable._ROW_LIMITS, "rollover_modes", 1)
    with pytest.raises(StoreError, match="supported bounds"):
        store.restore_budget_files("personal", files)
