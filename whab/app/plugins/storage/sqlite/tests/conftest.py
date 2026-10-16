"""Normalized provider records exercise storage without importing a bank plugin."""

import pytest


@pytest.fixture
def imported_account():
    def record(balance=10000, **extra):
        return {
            "id": "bank",
            "name": "Checking",
            "institution": "Fixture Bank",
            "type": "depository",
            "balance": balance,
            "currency": "USD",
            "source": "plaid",
            "itemId": "item",
            "mask": "1234",
            **extra,
        }

    return record


@pytest.fixture
def imported_transaction():
    def record(transaction_id="tx", amount=1000, **extra):
        return {
            "id": transaction_id,
            "accountId": "bank",
            "date": "2026-10-01",
            "merchant": "Market",
            "amount": amount,
            "pending": False,
            "suggestedCategoryId": "groceries",
            "source": "plaid",
            "inferredTransfer": False,
            "pendingTransactionId": None,
            **extra,
        }

    return record


@pytest.fixture
def sync_changes():
    def record(added=None, modified=None, removed=None):
        return {"added": added or [], "modified": modified or [], "removed": removed or []}

    return record


@pytest.fixture
def save_item():
    def save(ledger):
        ledger.save_item("item", "synthetic-ciphertext", "Fixture Bank")

    return save


@pytest.fixture
def apply_import(store):
    return store.apply_sync
