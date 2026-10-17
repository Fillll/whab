"""Signed synthetic notifications validate authentication and safe state changes."""

import asyncio
import base64
import hashlib
import json

import httpx
import pytest
import whab_plugin_plaid.service as provider
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from whab_plugin_plaid.service import PlaidService


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


@pytest.mark.parametrize("signed", [None, 123, "x" * 8193, "%.invalid.encoding"])
def test_webhook_verification_rejects_missing_oversized_and_invalid_encoding(
    store, plaid_env, plaid_client, signed
):
    async def scenario():
        async with plaid_client(
            lambda _: pytest.fail("Invalid encoding must not fetch a key")
        ) as client:
            assert (
                await PlaidService(store, plaid_env, client).verify_webhook(b"{}", signed) is False
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "header",
    [
        [],
        {"kid": 123, "alg": "ES256"},
        {"kid": "", "alg": "ES256"},
        {"kid": "k" * 201, "alg": "ES256"},
    ],
)
def test_webhook_verification_rejects_invalid_headers_before_fetching_a_key(
    store, plaid_env, plaid_client, webhook_signature, header
):
    _key, sign = webhook_signature

    async def scenario():
        async with plaid_client(
            lambda _: pytest.fail("Invalid header must not fetch a key")
        ) as client:
            assert (
                await PlaidService(store, plaid_env, client).verify_webhook(
                    b"{}", sign(b"{}", header=header)
                )
                is False
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [("kid", "other-key"), ("alg", "RS256"), ("kty", "RSA"), ("crv", "P-384"), ("x", 123)],
)
def test_webhook_key_metadata_and_curve_coordinates_are_validated(
    store, plaid_env, plaid_client, webhook_signature, field, value
):
    key, sign = webhook_signature

    async def scenario():
        async with plaid_client(
            lambda _: httpx.Response(200, json={"key": {**key, field: value}})
        ) as client:
            assert (
                await PlaidService(store, plaid_env, client).verify_webhook(b"{}", sign(b"{}"))
                is False
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("signature", [b"short", b"x" * 64])
def test_webhook_requires_a_well_formed_authentic_signature(
    store, plaid_env, plaid_client, webhook_signature, signature
):
    key, sign = webhook_signature

    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={"key": key})) as client:
            assert (
                await PlaidService(store, plaid_env, client).verify_webhook(
                    b"{}", sign(b"{}", signature=signature)
                )
                is False
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "claims",
    [
        {"iat": True},
        {"iat": "now"},
        {"iat": None},
        {"iat": float("nan")},
        {"request_body_sha256": 123},
        {"request_body_sha256": "invalid-digest"},
    ],
)
def test_webhook_rejects_invalid_issued_times_and_body_digests(
    store, plaid_env, plaid_client, webhook_signature, claims
):
    key, sign = webhook_signature

    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={"key": key})) as client:
            assert (
                await PlaidService(store, plaid_env, client).verify_webhook(
                    b"{}", sign(b"{}", claims=claims)
                )
                is False
            )

    asyncio.run(scenario())


def test_webhook_rejects_invalid_key_expiry(store, plaid_env, plaid_client, webhook_signature):
    key, sign = webhook_signature

    async def scenario():
        async with plaid_client(
            lambda _: httpx.Response(200, json={"key": {**key, "expired_at": "invalid"}})
        ) as client:
            assert (
                await PlaidService(store, plaid_env, client).verify_webhook(b"{}", sign(b"{}"))
                is False
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"[]", b'{"item_id":123}'])
def test_authenticated_malformed_events_return_safe_bad_request(
    store, plaid_env, plaid_client, webhook_signature, body
):
    key, sign = webhook_signature

    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={"key": key})) as client:
            result = await PlaidService(store, plaid_env, client).webhook(
                body, {"Plaid-Verification": sign(body)}
            )
        assert result.ok is False
        assert result.status_code == 400
        assert result.error == "Invalid webhook"

    asyncio.run(scenario())


def test_unknown_items_are_acknowledged_without_creating_connections(
    store, plaid_env, plaid_client, webhook_signature
):
    key, sign = webhook_signature
    body = b'{"item_id":"unknown","webhook_type":"TRANSACTIONS","webhook_code":"SYNC_UPDATES_AVAILABLE"}'

    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={"key": key})) as client:
            service = PlaidService(store, plaid_env, client)
            result = await service.webhook(body, {"Plaid-Verification": sign(body)})
            assert result.ok is True
            assert not service._queued
        assert store.items() == []

    asyncio.run(scenario())


def test_verified_transaction_update_queues_sync_and_matches_header_case_insensitively(
    store, plaid_env, plaid_client, save_plaid_item, webhook_signature
):
    key, sign = webhook_signature
    body = (
        b'{"item_id":"item","webhook_type":"TRANSACTIONS","webhook_code":"SYNC_UPDATES_AVAILABLE"}'
    )

    async def scenario():
        save_plaid_item(store)
        async with plaid_client(lambda _: httpx.Response(200, json={"key": key})) as client:
            service = PlaidService(store, plaid_env, client)
            calls = []

            async def sync(item_id):
                calls.append(item_id)

            service.sync = sync
            result = await service.webhook(
                body, {"Other-Header": "ignored", "pLaId-VeRiFiCaTiOn": sign(body)}
            )
            assert result.ok is True
            await service._queued["item"]["task"]
            assert calls == ["item"]
            assert not service._queued

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("event", "reconnect"),
    [
        ({"webhook_type": "ITEM", "webhook_code": "PENDING_DISCONNECT"}, True),
        ({"webhook_type": "ITEM", "webhook_code": "PENDING_EXPIRATION"}, True),
        (
            {
                "webhook_type": "ITEM",
                "webhook_code": "ERROR",
                "error": {"error_code": "ITEM_LOGIN_REQUIRED"},
            },
            True,
        ),
        (
            {
                "webhook_type": "ITEM",
                "webhook_code": "ERROR",
                "error": {"error_code": "OTHER_ERROR"},
            },
            False,
        ),
        (
            {"webhook_type": "ITEM", "webhook_code": "ERROR", "error": "untrusted remote detail"},
            False,
        ),
        ({"webhook_type": "ITEM", "webhook_code": "UNKNOWN"}, False),
        ({"webhook_type": "TRANSACTIONS", "webhook_code": "UNKNOWN"}, False),
        ({"webhook_type": "UNKNOWN"}, False),
    ],
)
def test_item_notifications_only_mark_reconnect_for_documented_events(
    store, plaid_env, plaid_client, save_plaid_item, webhook_signature, event, reconnect
):
    key, sign = webhook_signature
    body = json.dumps({"item_id": "item", **event}).encode()

    async def scenario():
        save_plaid_item(store)
        async with plaid_client(lambda _: httpx.Response(200, json={"key": key})) as client:
            service = PlaidService(store, plaid_env, client)
            result = await service.webhook(body, {"Plaid-Verification": sign(body)})
            assert result.ok is True
            assert not service._queued
        item = store.items()[0]
        assert item["status"] == ("reconnect" if reconnect else "connected")
        assert item["error"] == (
            "Reconnect this institution through Plaid Link." if reconnect else None
        )

    asyncio.run(scenario())
