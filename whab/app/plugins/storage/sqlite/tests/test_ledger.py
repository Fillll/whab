"""Workspace and envelope behavior through isolated SQLite ledgers."""

import pytest
from whab_plugin_sqlite import Store

from whab.domain.errors import StoreError


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


def test_manual_transactions_have_no_provider_artwork(store):
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    transaction = store.add_transaction(
        "personal",
        {
            "accountId": account, "date": "2026-10-01", "merchant": "Market", "amount": 100,
            "categoryId": None, "merchantLogo": "untrusted-input",
        },
    )
    assert store.state("personal")["transactions"][transaction]["merchantLogo"] is None
    assert all(row["merchantLogo"] is None for row in store.state("demo")["transactions"].values())


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
    store, apply_import, imported_account, imported_transaction, save_item, sync_changes
):
    save_item(store)
    apply_import(
        "item",
        sync_changes([imported_transaction("old", amount=800000, date="2024-11-01")]),
        "one",
        [imported_account(100000)],
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
    store, apply_import, imported_account, imported_transaction, save_item, sync_changes
):
    save_item(store)
    apply_import(
        "item",
        sync_changes(
            [
                imported_transaction("old", amount=5000),
                imported_transaction(
                    "transfer", amount=5000, inferredTransfer=True, suggestedCategoryId=None
                ),
            ]
        ),
        "one",
        [imported_account(10000)],
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
    store, apply_import, imported_account, imported_transaction, save_item, sync_changes
):
    save_item(store)
    purchase = imported_transaction(
        "payment", amount=2000, inferredTransfer=True, suggestedCategoryId=None
    )
    apply_import("item", sync_changes([purchase]), "one", [imported_account()])
    store.edit_transaction("personal", "payment", reviewed=True)
    assert store.state("personal")["transactions"]["payment"]["categoryEdited"] is False
    store.edit_transaction("personal", "payment", "dining", True)
    apply_import("item", sync_changes(modified=[purchase]), "two", [imported_account()])
    assert store.transactions("personal")[0]["isTransfer"] is False
    assert store.workspace("personal", "2026-10")["summary"]["spent"] == 2000
    store.edit_transaction("personal", "payment", None)
    assert store.transactions("personal")[0]["isTransfer"] is True


def test_removed_and_unknown_balances_are_conservative(
    store, apply_import, imported_account, imported_transaction, save_item, sync_changes
):
    save_item(store)
    apply_import("item", sync_changes([imported_transaction()]), "one", [imported_account()])
    apply_import("item", sync_changes(), "two", [])
    assert store.accounts("personal")[0]["stale"] is True
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 0
    assert len(store.transactions("personal")) == 1
    apply_import(
        "item",
        sync_changes(),
        "three",
        [
            imported_account(),
            {
                "id": "card",
                "name": "Card",
                "institution": "Fixture Bank",
                "type": "credit",
                "balance": 0,
                "balanceUnknown": True,
                "stale": True,
                "currency": "USD",
                "source": "plaid",
                "itemId": "item",
            },
        ],
    )
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] <= 0


def test_manual_money_and_import_currency_validation(
    store, apply_import, imported_account, save_item, sync_changes
):
    with pytest.raises(StoreError):
        store.add_account("personal", {"name": "Cash", "type": "depository", "balance": True})
    save_item(store)
    with pytest.raises(StoreError, match="[Cc]urrency"):
        apply_import(
            "item",
            sync_changes(),
            "one",
            [imported_account(balance=10000, currency="XXX")],
        )
