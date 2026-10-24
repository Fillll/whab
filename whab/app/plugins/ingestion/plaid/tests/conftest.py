"""Synthetic Plaid payloads and mocked transports belong to the Plaid plugin."""

import base64
import hashlib
import json

import httpx
import pytest
import whab_plugin_plaid.service as provider
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
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


def url64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.fixture
def webhook_signature(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(provider.time, "time", lambda: now)
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_numbers()
    key = {
        "kid": "fixture-key",
        "alg": "ES256",
        "kty": "EC",
        "crv": "P-256",
        "x": url64(public.x.to_bytes(32)),
        "y": url64(public.y.to_bytes(32)),
    }

    def sign(body, *, claims=None, header=None, signature=None):
        header64 = url64(
            json.dumps(
                {"kid": "fixture-key", "alg": "ES256"} if header is None else header
            ).encode()
        )
        payload64 = url64(
            json.dumps(
                {
                    "iat": now,
                    "request_body_sha256": hashlib.sha256(body).hexdigest(),
                    **(claims or {}),
                }
            ).encode()
        )
        message = f"{header64}.{payload64}".encode()
        r, s = decode_dss_signature(private.sign(message, ec.ECDSA(hashes.SHA256())))
        raw_signature = r.to_bytes(32) + s.to_bytes(32) if signature is None else signature
        return message.decode() + "." + url64(raw_signature)

    return key, sign
