"""Workspace and envelope behavior through isolated SQLite ledgers."""

import pytest

from whab.domain.errors import StoreError
from whab.storage.sqlite import Store


def test_workspaces_isolated_and_invalid_foreign_references_rejected(store):
    assert store.accounts("personal") == []
    with pytest.raises(StoreError, match="Account not found"):
        store.add_transaction(
            "personal",
            {
                "accountId": "checking",
                "date": "2026-10-01",
                "merchant": "Other workspace",
                "amount": 100,
                "categoryId": None,
            },
        )
    demo_id = store.transactions("demo")[0]["id"]
    with pytest.raises(StoreError, match="Transaction not found"):
        store.edit_transaction("personal", demo_id, "fun", True)
    with pytest.raises(StoreError, match="Invalid workspace"):
        store.workspace({"$ne": "demo"})
    assert store.transactions("personal") == []


def test_monthly_rollover_cents_refunds_and_credit_balances():
    store = Store(":memory:", month="2026-08")
    try:
        cash = store.add_account(
            "personal", {"name": "Cash", "type": "depository", "balance": 100000}
        )
        store.assign("personal", "groceries", "2026-08", 20000)
        store.add_transaction(
            "personal",
            {
                "accountId": cash,
                "date": "2026-08-10",
                "merchant": "Market",
                "amount": 7000,
                "categoryId": "groceries",
            },
        )
        store.add_transaction(
            "personal",
            {
                "accountId": cash,
                "date": "2026-09-10",
                "merchant": "Refund",
                "amount": -500,
                "categoryId": "groceries",
            },
        )
        store.assign("personal", "groceries", "2026-09", 10000)
        snapshot = store.workspace("personal", "2026-09")
        row = next(row for row in snapshot["budget"] if row["categoryId"] == "groceries")
        assert (row["carryover"], row["assigned"], row["spent"], row["available"]) == (
            13000,
            10000,
            -500,
            23500,
        )
        assert snapshot["summary"]["readyToAssign"] == 70000
        card = store.add_account("personal", {"name": "Card", "type": "credit", "balance": 2000})
        store.add_transaction(
            "personal",
            {
                "accountId": card,
                "date": "2026-09-10",
                "merchant": "Café",
                "amount": 300,
                "categoryId": "dining",
            },
        )
        assert next(a for a in store.accounts("personal") if a["id"] == card)["balance"] == 2300
    finally:
        store.close()


def test_initial_history_does_not_consume_new_budget(
    store, apply_plaid_sync, bank_account, bank_transaction, save_plaid_item, sync_changes
):
    save_plaid_item(store)
    apply_plaid_sync(
        "item",
        sync_changes([bank_transaction("old", amount=8000, date="2024-11-01")]),
        "one",
        [bank_account(1000)],
    )
    store.assign("personal", "groceries", "2026-10", 20000)
    snapshot = store.workspace("personal", "2026-10")
    row = next(row for row in snapshot["budget"] if row["categoryId"] == "groceries")
    assert snapshot["budgetStartMonth"] == "2026-10"
    assert (row["carryover"], row["available"], snapshot["summary"]["readyToAssign"]) == (
        0,
        20000,
        80000,
    )
    assert store.workspace("personal", "2024-11")["summary"]["spent"] == 800000
    with pytest.raises(StoreError, match="before the budget start"):
        store.assign("personal", "groceries", "2024-11", 100)


def test_transfers_history_and_card_debt_never_invent_cash(
    store, apply_plaid_sync, bank_account, bank_transaction, save_plaid_item, sync_changes
):
    save_plaid_item(store)
    apply_plaid_sync(
        "item",
        sync_changes(
            [
                bank_transaction("old", amount=50),
                bank_transaction(
                    "transfer", amount=50, personal_finance_category={"primary": "TRANSFER_OUT"}
                ),
            ]
        ),
        "one",
        [bank_account(100)],
    )
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 10000
    store.add_account("personal", {"name": "Card", "type": "credit", "balance": 2000})
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 8000
    with pytest.raises(StoreError, match="manual account"):
        store.add_transaction(
            "personal",
            {
                "accountId": "bank",
                "date": "2026-10-01",
                "merchant": "Duplicate",
                "amount": 100,
                "categoryId": None,
            },
        )


def test_category_reduction_can_repair_deficit(store):
    cash = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 20000})
    store.assign("personal", "fun", "2026-10", 5000)
    store.db.execute("UPDATE accounts SET balance=0 WHERE workspace='personal' AND id=?", (cash,))
    store.assign("personal", "fun", "2026-10", 0)
    assert (
        next(
            row
            for row in store.workspace("personal", "2026-10")["budget"]
            if row["categoryId"] == "fun"
        )["assigned"]
        == 0
    )


def test_review_only_does_not_override_category_and_transfer_can_restore(
    store, apply_plaid_sync, bank_account, bank_transaction, save_plaid_item, sync_changes
):
    save_plaid_item(store)
    purchase = bank_transaction(
        "payment", amount=20, personal_finance_category={"primary": "TRANSFER_OUT"}
    )
    apply_plaid_sync("item", sync_changes([purchase]), "one", [bank_account()])
    store.edit_transaction("personal", "payment", reviewed=True)
    assert store.state("personal")["transactions"]["payment"]["categoryEdited"] is False
    store.edit_transaction("personal", "payment", "dining", True)
    apply_plaid_sync("item", sync_changes(modified=[purchase]), "two", [bank_account()])
    assert store.transactions("personal")[0]["isTransfer"] is False
    assert store.workspace("personal", "2026-10")["summary"]["spent"] == 2000
    store.edit_transaction("personal", "payment", None)
    assert store.transactions("personal")[0]["isTransfer"] is True


def test_removed_and_unknown_balances_are_conservative(
    store, apply_plaid_sync, bank_account, bank_transaction, save_plaid_item, sync_changes
):
    save_plaid_item(store)
    apply_plaid_sync("item", sync_changes([bank_transaction()]), "one", [bank_account()])
    apply_plaid_sync("item", sync_changes(), "two", [])
    assert store.accounts("personal")[0]["stale"] is True
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 0
    assert len(store.transactions("personal")) == 1
    apply_plaid_sync(
        "item",
        sync_changes(),
        "three",
        [
            bank_account(),
            {
                "account_id": "card",
                "name": "Card",
                "type": "credit",
                "balances": {"current": None, "available": 10000},
            },
        ],
    )
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] <= 0


def test_manual_money_and_import_currency_validation(
    store, apply_plaid_sync, bank_account, save_plaid_item, sync_changes
):
    with pytest.raises(StoreError):
        store.add_account("personal", {"name": "Cash", "type": "depository", "balance": True})
    save_plaid_item(store)
    with pytest.raises(StoreError, match="USD"):
        apply_plaid_sync(
            "item",
            sync_changes(),
            "one",
            [bank_account(balances={"current": 100, "iso_currency_code": "EUR"})],
        )
