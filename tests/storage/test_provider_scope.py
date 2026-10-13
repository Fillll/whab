"""Normalized providers cannot collide across providers or connection identities."""

import pytest

from whab.domain.errors import StoreError
from whab.storage.bank import BankStore


def account():
    return {
        "id": "bank",
        "name": "Checking",
        "institution": "Fixture Bank",
        "type": "depository",
        "balance": 10000,
        "currency": "USD",
        "source": "fixture",
        "itemId": "item",
    }


def transaction(transaction_id="tx", *, pending=False, pending_id=None):
    return {
        "id": transaction_id,
        "accountId": "bank",
        "date": "2026-10-01",
        "merchant": "Fixture Market",
        "amount": 1000,
        "suggestedCategoryId": "groceries",
        "pending": pending,
        "source": "fixture",
        "inferredTransfer": False,
        "pendingTransactionId": pending_id,
    }


def changes(*added, removed=()):
    return {"added": list(added), "modified": [], "removed": list(removed)}


def test_same_provider_ids_are_scoped_and_legacy_plaid_ids_are_preserved(store):
    providers = {name: BankStore(store, name) for name in ("plaid", "alpha", "beta")}
    for name, bank in providers.items():
        bank.save_item("item", f"{name}-synthetic-encrypted-token", "Fixture Bank")
        bank.apply_sync("item", changes(transaction()), "one", [account()])
        assert [item["id"] for item in bank.items()] == ["item"]
        assert bank.get_item("item")["token"] == f"{name}-synthetic-encrypted-token"
    accounts = store.accounts("personal")
    transactions = store.transactions("personal")
    assert len({record["id"] for record in accounts}) == 3
    assert len({record["id"] for record in transactions}) == 3
    assert next(record for record in accounts if record["source"] == "plaid")["id"] == "bank"
    assert next(record for record in transactions if record["source"] == "plaid")["id"] == "tx"
    assert {record["accountId"] for record in transactions} == {record["id"] for record in accounts}

    # Reusing account/transaction IDs in a second connection also stays isolated.
    providers["alpha"].save_item("other-item", "other-synthetic-token", "Other Fixture Bank")
    providers["alpha"].apply_sync("other-item", changes(transaction()), "one", [account()])
    assert len(store.accounts("personal")) == 4
    assert len(store.transactions("personal")) == 4


def test_provider_cannot_access_status_or_disconnect_another_provider_item(store):
    plaid = BankStore(store, "plaid")
    alpha = BankStore(store, "alpha")
    alpha.save_item("item", "synthetic-encrypted-token", "Fixture Bank")
    foreign_id = alpha.item_identity("item")
    assert plaid.get_item(foreign_id) is None
    assert plaid.items() == []
    plaid.set_item_status(foreign_id, "error", "Synthetic error")
    assert alpha.get_item("item")["status"] == "connected"
    with pytest.raises(StoreError, match="Connection not found"):
        plaid.disconnect_item(foreign_id)
    assert alpha.get_item("item")["token"] == "synthetic-encrypted-token"


def test_namespaced_pending_replacement_preserves_latest_annotations(store):
    alpha = BankStore(store, "alpha")
    alpha.save_item("item", "synthetic-encrypted-token", "Fixture Bank")
    alpha.apply_sync("item", changes(transaction("pending", pending=True)), "one", [account()])
    pending_id = store.transactions("personal")[0]["id"]
    store.edit_transaction("personal", pending_id, "fun", True)
    store.db.execute(
        "UPDATE transactions SET note=? WHERE workspace='personal' AND id=?",
        ("Fixture annotation", pending_id),
    )
    batch = changes(transaction("posted", pending_id="pending"), removed=("pending",))
    alpha.apply_sync("item", batch, "two", [account()], expected_cursor="one")
    alpha.apply_sync("item", batch, "two", [account()], expected_cursor="two")
    rows = store.transactions("personal")
    assert len(rows) == 1
    assert rows[0]["id"] != pending_id
    assert (rows[0]["categoryId"], rows[0]["reviewed"], rows[0]["note"]) == (
        "fun",
        True,
        "Fixture annotation",
    )
    assert alpha.get_item("item")["cursor"] == "two"
