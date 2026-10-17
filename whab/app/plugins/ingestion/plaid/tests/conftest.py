"""Synthetic Plaid payloads and mocked transports belong to the Plaid plugin."""

import httpx
import pytest
from whab_plugin_plaid.service import encrypt_token


@pytest.fixture
def plaid_env():
    return {
        "PLAID_CLIENT_ID": "fixture-client",
        "PLAID_SECRET": "fixture-secret",
        "TOKEN_ENCRYPTION_KEY": "aa" * 32,
        "PLAID_ENV": "sandbox",
    }


@pytest.fixture
def bank_account():
    def record(balance=100, **extra):
        return {
            "account_id": "bank",
            "name": "Checking",
            "type": "depository",
            "balances": {"current": balance, "iso_currency_code": "USD"},
            "mask": "1234",
            **extra,
        }

    return record


@pytest.fixture
def bank_transaction():
    def record(transaction_id="tx", amount=10, **extra):
        return {
            "transaction_id": transaction_id,
            "account_id": "bank",
            "date": "2026-10-01",
            "name": "Market",
            "amount": amount,
            "pending": False,
            "personal_finance_category": {
                "primary": "FOOD_AND_DRINK",
                "detailed": "FOOD_AND_DRINK_GROCERIES",
            },
            **extra,
        }

    return record


@pytest.fixture
def sync_changes():
    def record(added=None, modified=None, removed=None):
        return {"added": added or [], "modified": modified or [], "removed": removed or []}

    return record


@pytest.fixture
def save_plaid_item(plaid_env):
    def save(ledger):
        ledger.save_item(
            "item",
            encrypt_token("fixture-access-token", plaid_env["TOKEN_ENCRYPTION_KEY"], "item"),
            "Fixture Bank",
        )

    return save


@pytest.fixture
def plaid_client():
    def client(handler):
        return httpx.AsyncClient(
            base_url="https://sandbox.plaid.com", transport=httpx.MockTransport(handler)
        )

    return client


@pytest.fixture
def apply_plaid_sync(store):
    """Translate synthetic provider payloads into the generic ledger import contract."""
    from whab_plugin_plaid.mapping import normalize_account, normalize_changes

    def apply(item_id, changes, cursor, accounts, **options):
        return store.apply_sync(
            item_id,
            normalize_changes(changes),
            cursor,
            [normalize_account(account, item_id, "Fixture Bank") for account in accounts],
            **options,
        )

    return apply
