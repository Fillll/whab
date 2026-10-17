"""Synthetic token compatibility and signed webhook verification."""

import asyncio
import base64
import hashlib
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from whab_plugin_plaid.service import (
    PlaidError,
    PlaidService,
    decrypt_token,
    encrypt_token,
    plaid_configuration,
)


def test_encryption_compatible_with_node_and_authenticates_item(legacy_token, plaid_env):
    assert (
        decrypt_token(legacy_token, plaid_env["TOKEN_ENCRYPTION_KEY"], "fixture-item")
        == "fixture-access-token"
    )
    encrypted = encrypt_token("fixture-access-token", plaid_env["TOKEN_ENCRYPTION_KEY"], "item")
    assert (
        decrypt_token(encrypted, plaid_env["TOKEN_ENCRYPTION_KEY"], "item")
        == "fixture-access-token"
    )
    with pytest.raises(PlaidError):
        decrypt_token(encrypted, plaid_env["TOKEN_ENCRYPTION_KEY"], "wrong-item")
    assert "fixture-access-token" not in encrypted


def test_production_guard_and_missing_key(plaid_env):
    assert plaid_configuration({**plaid_env, "PLAID_ENV": "production"})["plaidConfigured"] is False
    assert (
        plaid_configuration({**plaid_env, "PLAID_ENV": "production", "ALLOW_LIVE_PLAID": "true"})[
            "plaidConfigured"
        ]
        is True
    )
    assert plaid_configuration({})["missingConfig"] == [
        "PLAID_CLIENT_ID",
        "PLAID_SECRET",
        "TOKEN_ENCRYPTION_KEY",
    ]


def _url64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def test_verified_webhook_requires_signature_exact_body_and_freshness(
    store, plaid_client, plaid_env
):
    async def scenario():
        private = ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_numbers()
        key = {
            "kid": "key-id",
            "alg": "ES256",
            "kty": "EC",
            "crv": "P-256",
            "x": _url64(public.x.to_bytes(32)),
            "y": _url64(public.y.to_bytes(32)),
        }
        body = b'{"item_id":"item"}'

        def jwt(age=0, alg="ES256"):
            header = _url64(json.dumps({"alg": alg, "kid": "key-id"}).encode())
            payload = _url64(
                json.dumps(
                    {
                        "iat": time.time() - age,
                        "request_body_sha256": hashlib.sha256(body).hexdigest(),
                    }
                ).encode()
            )
            message = f"{header}.{payload}".encode()
            r, s = decode_dss_signature(private.sign(message, ec.ECDSA(hashes.SHA256())))
            return message.decode() + "." + _url64(r.to_bytes(32) + s.to_bytes(32))

        async with plaid_client(lambda _: httpx.Response(200, json={"key": key})) as client:
            service = PlaidService(store, plaid_env, client)
            assert await service.verify_webhook(body, jwt()) is True
            assert await service.verify_webhook(b"{}", jwt()) is False
            assert await service.verify_webhook(body, jwt(301)) is False
            assert await service.verify_webhook(body, jwt(-60)) is False
            assert await service.verify_webhook(body, jwt(alg="HS256")) is False
            assert await service.verify_webhook(body, "invalid") is False
            key["expired_at"] = time.time() - 10
            service._keys.clear()
            assert await service.verify_webhook(body, jwt()) is False
            assert await service.verify_webhook(body, jwt(20)) is True

    asyncio.run(scenario())
