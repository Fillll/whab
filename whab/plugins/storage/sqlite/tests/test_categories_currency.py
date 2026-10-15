"""Two-level leaves persist safely while currency envelopes remain separate."""

import sqlite3
from dataclasses import replace

import pytest
from whab_sqlite import Store

from whab.domain.categories import category_group_id
from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport


def account(ledger, currency="USD", balance=100000):
    return ledger.add_account(
        "personal", {
            "name": f"Fixture {currency}", "type": "depository", "balance": balance,
            "currency": currency,
        }
    )


def transaction(account_id, category_id=None, **extra):
    return {
        "accountId": account_id, "date": "2026-10-01", "merchant": "Fixture market",
        "amount": 1000, "categoryId": category_id, **extra,
    }


def test_currency_envelopes_and_hierarchy_survive_reopen_without_conversion(tmp_path):
    path = tmp_path / "currencies.sqlite"
    ledger = Store(path, month="2026-10")
    try:
        usd = account(ledger)
        eur = account(ledger, "EUR", 50000)
        category = ledger.add_category("personal", {
            "name": "European groceries", "group": "Everyday", "target": 30000,
            "currency": "EUR",
        })
        ledger.assign("personal", "groceries", "2026-10", 10000)
        ledger.assign("personal", category, "2026-10", 20000)
        ledger.add_transaction("personal", transaction(usd, "groceries", amount=2500))
        ledger.add_transaction("personal", transaction(eur, category))
        before = ledger.workspace("personal", "2026-10")
        assert before["summariesByCurrency"] == {
            "USD": {"income": 0, "spent": 2500, "assigned": 10000,
                    "available": 7500, "readyToAssign": 90000},
            "EUR": {"income": 0, "spent": 1000, "assigned": 20000,
                    "available": 19000, "readyToAssign": 30000},
        }
        assert before["summary"] == before["summariesByCurrency"]["USD"]
        child = next(row for row in before["categories"] if row["id"] == category)
        assert child["currency"] == "EUR"
        assert child["parentId"] == category_group_id("Everyday")
        with pytest.raises(StoreError, match="available money"):
            ledger.assign("personal", category, "2026-10", 50001)
        with pytest.raises(StoreError, match="available money"):
            ledger.assign("personal", "groceries", "2026-10", 100001)
        assert ledger.workspace("personal", "2026-10") == before
    finally:
        ledger.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.workspace("personal", "2026-10") == before
        assert {row["currency"] for row in reopened.accounts("personal")} == {"USD", "EUR"}
    finally:
        reopened.close()


def test_existing_parent_can_create_child_but_child_cannot_become_parent(store):
    parent_id = category_group_id("Family")
    first = store.add_category("personal", {"name": "School", "group": "Family", "target": 100})
    second = store.add_category("personal", {"name": "Child care", "parentId": parent_id})
    children = {row["id"]: row for row in store.categories("personal")}
    assert children[first]["parentId"] == children[second]["parentId"] == parent_id
    assert children[second]["group"] == "Family"
    before = store.state("personal")
    for invalid in ({"parentId": first}, {"parentId": "missing"},
                    {"parentId": parent_id, "group": "Different"}, {"children": []}):
        with pytest.raises(StoreError):
            store.add_category("personal", {"name": "Invalid", **invalid})
        assert store.state("personal") == before


def test_parent_ids_cannot_receive_transactions_reviews_or_envelope_assignments(store):
    cash = account(store)
    child = store.categories("personal")[0]
    parent = child["parentId"]
    transaction_id = store.add_transaction("personal", transaction(cash, child["id"]))
    before = store.state("personal")
    for action in (
        lambda: store.add_transaction("personal", transaction(cash, parent)),
        lambda: store.edit_transaction("personal", transaction_id, parent, True),
        lambda: store.assign("personal", parent, "2026-10", 100),
    ):
        with pytest.raises(StoreError, match="Category not found"):
            action()
        assert store.state("personal") == before


def test_cross_currency_transaction_and_review_fail_atomically(store):
    euro = account(store, "EUR")
    euro_category = store.add_category("personal", {
        "name": "Euro food", "group": "Everyday", "currency": "EUR",
    })
    transaction_id = store.add_transaction("personal", transaction(euro, euro_category))
    before = store.state("personal")
    with pytest.raises(StoreError, match="currency must match"):
        store.add_transaction("personal", transaction(euro, "groceries"))
    assert store.state("personal") == before
    with pytest.raises(StoreError, match="currency must match"):
        store.edit_transaction("personal", transaction_id, "groceries", True)
    assert store.state("personal") == before
    store.edit_transaction("personal", transaction_id, reviewed=True)
    assert store.transactions("personal")[0]["reviewed"] is True
    store.edit_transaction("personal", transaction_id, None)
    assert store.transactions("personal")[0]["categoryId"] is None


@pytest.mark.parametrize("suggestion", ["groceries", category_group_id("Everyday"), None])
def test_bank_suggestions_cannot_assign_another_currency_or_a_parent(
    store, imported_account, imported_transaction, sync_changes, save_item, suggestion
):
    save_item(store)
    store.apply_sync("item", sync_changes([
        imported_transaction(suggestedCategoryId=suggestion)
    ]), "first", [imported_account(currency="EUR")])
    assert store.transactions("personal")[0]["categoryId"] is None
    assert store.accounts("personal")[0]["currency"] == "EUR"


def test_saved_currency_annotations_fail_safely_when_category_currency_is_corrupt(
    store, imported_account, imported_transaction, sync_changes, save_item
):
    save_item(store)
    euro_category = store.add_category("personal", {
        "name": "Euro food", "group": "Everyday", "currency": "EUR",
    })
    imported = imported_transaction(suggestedCategoryId=euro_category)
    store.apply_sync("item", sync_changes([imported]), "first", [imported_account(currency="EUR")])
    store.edit_transaction("personal", "tx", euro_category, True)
    store.db.execute("UPDATE categories SET currency='USD' WHERE workspace='personal' AND id=?",
                     (euro_category,))
    before = store.state("personal")
    with pytest.raises(StoreError, match="currency must match"):
        store.apply_sync("item", sync_changes(modified=[imported]), "second",
                         [imported_account(currency="EUR", balance=99900)])
    assert store.state("personal") == before


def test_bank_account_currency_is_immutable_and_cursor_stays_atomic(
    store, imported_account, sync_changes, save_item
):
    save_item(store)
    store.apply_sync("item", sync_changes(), "first", [imported_account(currency="EUR")])
    before = store.state("personal")
    with pytest.raises(StoreError, match="currency cannot change"):
        store.apply_sync("item", sync_changes(), "second", [imported_account(currency="USD")])
    assert store.state("personal") == before


def test_additive_currency_migration_preserves_ids_money_and_parent_identity(tmp_path):
    path = tmp_path / "legacy.sqlite"
    original = Store(path, month="2026-10")
    try:
        cash = account(original)
        category = original.add_category("personal", {
            "name": "Legacy child", "group": "Legacy parent", "target": 2000,
        })
        original.assign("personal", category, "2026-10", 2000)
        transaction_id = original.add_transaction("personal", transaction(cash, category))
        before = original.workspace("personal", "2026-10")
    finally:
        original.close()
    with sqlite3.connect(path) as legacy:
        legacy.execute("ALTER TABLE accounts DROP COLUMN currency")
        legacy.execute("ALTER TABLE categories DROP COLUMN currency")
    reopened = Store(path, month="2026-11")
    try:
        after = reopened.workspace("personal", "2026-10")
        assert after == before
        assert after["accounts"][0]["id"] == cash
        assert after["transactions"][0]["id"] == transaction_id
        assert after["transactions"][0]["categoryId"] == category
        assert {row["currency"] for row in after["accounts"] + after["categories"]} == {"USD"}
    finally:
        reopened.close()


def test_file_import_currency_is_inherited_without_changing_legacy_record_shape(store):
    payload = WorkspaceImport(
        accounts=({"id": "cash", "name": "Euro wallet", "institution": "", "type": "depository",
                   "balance": 50000, "source": "fixture", "stale": False,
                   "balanceUnknown": False},),
        categories=({"id": "food", "name": "Food", "group": "Everyday", "target": 10000},),
        transactions=(), assignments=(), budget_start_month="2026-10", currency="EUR",
    )
    store.import_workspace("personal", "fixture", "eur", payload)
    snapshot = store.workspace("personal", "2026-10")
    assert snapshot["summariesByCurrency"]["EUR"]["readyToAssign"] == 50000
    assert snapshot["accounts"][0]["currency"] == snapshot["categories"][0]["currency"] == "EUR"
    assert payload.accounts[0].keys() == {
        "id", "name", "institution", "type", "balance", "source", "stale", "balanceUnknown"
    }
    with pytest.raises(StoreError, match="[Cc]urrency"):
        replace(payload, currency="XXX").validated_copy()
