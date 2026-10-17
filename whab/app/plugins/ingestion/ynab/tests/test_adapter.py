"""The adapter preserves exported annotations within the public import contract."""

import pytest
from test_parser import archive, budget, transaction
from whab_plugin_ynab.plugin import plugin, workspace_import

from whab.plugins import StoreError


def test_adapter_normalizes_records_and_keeps_memo_flags_and_reconciliation():
    imported = workspace_import(archive(transactions=[
        transaction(),
        transaction(Account="Savings", Flag="", Cleared="Uncleared", Memo="", Payee="",
                    Outflow="0", Inflow="2.50"),
        transaction(Cleared="Reconciled", Flag="Red", Memo="Reconciled memo"),
    ])).validated_copy()
    assert plugin.id == "ynab"
    assert plugin.file_import.parse is workspace_import
    assert plugin.file_import.templates.is_dir()
    assert plugin.file_import.static.is_dir()
    accounts = {row["name"]: row for row in imported.accounts}
    assert accounts["Checking"]["balance"] == -2468
    assert accounts["Savings"]["balance"] == 250
    for account in imported.accounts:
        assert account["institution"] == "YNAB export"
        assert account["type"] == "other"
        assert account["onBudget"] is False
        assert account["subtype"] is None
        assert account["stale"] is account["balanceUnknown"] is True
        assert account["source"] == "ynab"
    category, = imported.categories
    assert {key: category[key] for key in ("name", "group", "target")} == {
        "name": "Food", "group": "Living", "target": 0,
    }
    assert imported.assignments == ({
        "month": "2026-10", "categoryId": category["id"], "assigned": 10000,
    },)
    first, uncleared, reconciled = imported.transactions
    assert first["note"] == "Synthetic memo\n[YNAB: cleared; flag Blue]"
    assert uncleared["note"] == "[YNAB: uncleared]"
    assert uncleared["merchant"] == "(No payee)"
    assert reconciled["note"] == "Reconciled memo\n[YNAB: reconciled; flag Red]"
    assert (first["reviewed"], uncleared["reviewed"], reconciled["reviewed"]) == (True, False, True)
    for row in imported.transactions:
        assert row["pending"] is False
        assert row["categoryEdited"] is True
        assert row["deleted"] is False
        assert row["source"] == "ynab"
    assert imported.budget_start_month == "2026-10"
    assert any("USD" in warning for warning in imported.warnings)
    assert any("rollover" in warning for warning in imported.warnings)


def test_income_transfers_and_split_metadata_survive_without_collapsing_rows():
    no_category = {"Category Group/Category": "", "Category Group": "", "Category": ""}
    rows = [
        transaction(**no_category, Payee="Transfer : Savings"),
        transaction(Account="Savings", **no_category, Payee="Transfer : Checking",
                    Outflow="0", Inflow="12.34"),
        transaction(**{"Category Group/Category": "Inflow: Ready to Assign",
                       "Category Group": "Inflow", "Category": "Ready to Assign",
                       "Outflow": "0", "Inflow": "100.00"}),
        transaction(**{"Category Group/Category": "-- Split --", "Category Group": "",
                       "Category": "", "Flag": "", "Memo": ""}),
        transaction(Payee="Transfer : Savings"),
        transaction(),
        transaction(),
    ]
    imported = workspace_import(archive(transactions=rows)).validated_copy()
    assert len(imported.transactions) == 7
    assert len({row["id"] for row in imported.transactions}) == 7
    assert [row["isTransfer"] for row in imported.transactions] == [
        True, True, False, False, False, False, False,
    ]
    assert all(row["isTransfer"] == row["inferredTransfer"] for row in imported.transactions)
    assert imported.transactions[2]["categoryId"] is None
    assert imported.transactions[2]["amount"] == -10000
    assert imported.transactions[3]["note"] == "[YNAB: cleared; split row]"


def test_budget_start_month_uses_oldest_budget_or_transaction_month():
    budget_first = workspace_import(archive(budgets=[budget(Month="Jan 2024")]))
    assert budget_first.budget_start_month == "2024-01"
    transaction_first = workspace_import(archive(transactions=[transaction(Date="2023-12-31")]))
    assert transaction_first.budget_start_month == "2023-12"
    budget_only = workspace_import(archive(transactions=[])).validated_copy()
    assert budget_only.budget_start_month == "2026-10"
    assert budget_only.transactions == budget_only.accounts == ()
    register_only = workspace_import(archive(budgets=[])).validated_copy()
    assert register_only.budget_start_month == "2026-10"
    assert register_only.assignments == ()


def test_note_limit_accounts_for_added_status_metadata_and_preserves_boundary():
    suffix = "\n[YNAB: cleared; flag Blue]"
    fitting = workspace_import(archive(transactions=[transaction(Memo="m" * (1000 - len(suffix)))]))
    assert len(fitting.transactions[0]["note"]) == 1000
    fitting.validated_copy()
    with pytest.raises(StoreError, match="1,000 character limit"):
        workspace_import(archive(transactions=[transaction(Memo="m" * (1001 - len(suffix)))]))


def test_long_payees_are_preserved_in_notes_when_display_labels_are_shortened():
    payee = "Original fixture payee " * 16
    imported = workspace_import(archive(transactions=[transaction(Payee=payee)])).validated_copy()
    row, = imported.transactions
    assert row["merchant"] == payee[:200]
    assert row["note"] == f"Synthetic memo\n[YNAB: cleared; flag Blue]\nOriginal YNAB payee: {payee}"
    assert any("Payee labels" in warning for warning in imported.warnings)
    with pytest.raises(StoreError, match="1,000 character limit"):
        workspace_import(archive(transactions=[transaction(Payee="p" * 1000)]))


def test_empty_export_fails_with_authored_error_instead_of_minimum_exception():
    with pytest.raises(StoreError, match="no dated budget assignments or transactions"):
        workspace_import(archive(budgets=[], transactions=[]))
