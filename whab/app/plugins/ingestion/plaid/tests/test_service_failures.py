"""Failure paths keep saved connections, cursors and public errors safe."""

import asyncio
import base64
import json

import httpx
import pytest
import whab_plaid.service as provider
from whab_plaid.service import (
    PlaidError,
    PlaidRemoteError,
    PlaidService,
    decrypt_token,
    encrypt_token,
    plaid_configuration,
)


def test_invalid_environment_is_missing_and_process_defaults_are_supported(
    monkeypatch, store, plaid_env
):
    monkeypatch.setattr(provider.os, "environ", {**plaid_env, "PLAID_ENV": "unsupported"})
    config = plaid_configuration()
    assert config["missingConfig"] == ["PLAID_ENV"]
    assert config["environment"] == "sandbox"
    assert config["plaidConfigured"] is False

    async def scenario():
        service = PlaidService(store)
        assert service.env["PLAID_ENV"] == "unsupported"
        assert service.client.base_url == "https://sandbox.plaid.com"
        assert service.client.follow_redirects is False
        await service.close()
        assert service.client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("key", [None, "invalid", "aa" * 31])
def test_token_encryption_rejects_invalid_key_material(key):
    with pytest.raises(PlaidError, match="Plaid encryption key missing"):
        encrypt_token("fixture-token", key, "item")


@pytest.mark.parametrize(("nonce", "tag"), [(b"short", b"t" * 16), (b"n" * 12, b"short")])
def test_stored_tokens_with_invalid_envelope_lengths_fail_safely(nonce, tag, plaid_env):
    encoded = ".".join(base64.b64encode(part).decode() for part in (nonce, tag, b"fixture"))
    with pytest.raises(PlaidError, match="Stored bank connection could not be unlocked"):
        decrypt_token(encoded, plaid_env["TOKEN_ENCRYPTION_KEY"], "item")


@pytest.mark.parametrize(
    ("status", "data", "expected_code"),
    [
        (200, [], ""),
        (400, [], ""),
        (400, {"error_code": 123}, ""),
        (400, {"error_code": "private detail"}, ""),
        (400, {"error_code": "ITEM_LOGIN_REQUIRED"}, "ITEM_LOGIN_REQUIRED"),
    ],
)
def test_remote_responses_allow_only_bounded_authored_error_codes(
    store, plaid_env, plaid_client, status, data, expected_code
):
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(status, json=data)) as client:
            service = PlaidService(store, plaid_env, client)
            with pytest.raises(PlaidRemoteError) as error:
                await service._call("/fixture", {})
            assert error.value.code == expected_code
            assert str(error.value) == "Plaid request could not complete."

    asyncio.run(scenario())


@pytest.mark.parametrize("network_failure", [False, True])
def test_malformed_json_and_transport_failures_are_sanitized(
    store, plaid_env, plaid_client, network_failure
):
    def handler(request):
        if network_failure:
            raise httpx.ConnectError("fixture-secret private remote detail", request=request)
        return httpx.Response(200, text="fixture-secret is not JSON")

    async def scenario():
        async with plaid_client(handler) as client:
            with pytest.raises(PlaidRemoteError) as error:
                await PlaidService(store, plaid_env, client)._call("/fixture", {})
            assert error.value.code == ""
            assert "fixture-secret" not in str(error.value)

    asyncio.run(scenario())


def test_missing_and_disconnected_connections_cannot_supply_a_token(store, plaid_env, plaid_client):
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={})) as client:
            service = PlaidService(store, plaid_env, client)
            with pytest.raises(PlaidError, match="Connection not found"):
                service.token("missing")
            store.save_item("item", "synthetic-ciphertext", "Fixture Bank")
            store.disconnect_item("item")
            with pytest.raises(PlaidError, match="Connection not found"):
                service.token("item")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("environment", "redirect"),
    [
        ("sandbox", "https://localhost/callback"),
        ("sandbox", "https://127.0.0.1/callback"),
        ("production", "https://app.example/callback"),
    ],
)
def test_new_link_includes_valid_urls_without_reusing_an_access_token(
    store, plaid_env, plaid_client, environment, redirect
):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"link_token": "fixture-link"})

    async def scenario():
        env = {
            **plaid_env,
            "PLAID_ENV": environment,
            "ALLOW_LIVE_PLAID": "true",
            "PLAID_WEBHOOK_URL": "https://hooks.example/plaid",
            "PLAID_REDIRECT_URI": redirect,
        }
        async with plaid_client(handler) as client:
            assert await PlaidService(store, env, client).link_token() == "fixture-link"
        assert requests == [
            {
                "user": {"client_user_id": "whab-local-household"},
                "client_name": "WHAB",
                "country_codes": ["US"],
                "language": "en",
                "products": ["transactions"],
                "transactions": {"days_requested": 730},
                "webhook": "https://hooks.example/plaid",
                "redirect_uri": redirect,
            }
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("PLAID_WEBHOOK_URL", "http://hooks.example/", "Webhook URL must use HTTPS"),
        ("PLAID_WEBHOOK_URL", "https://user:pass@hooks.example/", "Webhook URL must use HTTPS"),
        ("PLAID_WEBHOOK_URL", "https:///missing-host", "Webhook URL must use HTTPS"),
        ("PLAID_REDIRECT_URI", "http://app.example/callback", "Invalid redirect URI"),
        ("PLAID_REDIRECT_URI", "http://localhost/callback", "Invalid redirect URI"),
        ("PLAID_REDIRECT_URI", "http://127.0.0.1/callback", "Invalid redirect URI"),
        ("PLAID_REDIRECT_URI", "https://user:pass@app.example/callback", "Invalid redirect URI"),
        ("PLAID_REDIRECT_URI", "https:///missing-host", "Invalid redirect URI"),
    ],
)
def test_invalid_link_urls_are_rejected_before_any_remote_request(
    store, plaid_env, plaid_client, setting, value, message
):
    async def scenario():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={})

        async with plaid_client(handler) as client:
            with pytest.raises(PlaidError, match=message):
                await PlaidService(store, {**plaid_env, setting: value}, client).link_token()
        assert not requests

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [200, 500])
def test_link_failures_and_missing_link_tokens_return_safe_setup_error(
    store, plaid_env, plaid_client, status
):
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(status, json={})) as client:
            with pytest.raises(PlaidError, match="Bank connection setup could not complete"):
                await PlaidService(store, plaid_env, client).link_token()

    asyncio.run(scenario())


@pytest.mark.parametrize("sync_fails", [False, True])
def test_exchange_saves_encrypted_connection_even_if_initial_sync_needs_retry(
    store, plaid_env, plaid_client, sync_fails, sync_changes
):
    def handler(request):
        if request.url.path == "/item/public_token/exchange":
            assert json.loads(request.content) == {"public_token": "fixture-public"}
            return httpx.Response(200, json={"item_id": "item", "access_token": "fixture-access"})
        if sync_fails:
            return httpx.Response(500, json={"error_message": "fixture-secret"})
        if request.url.path == "/accounts/get":
            return httpx.Response(200, json={"accounts": []})
        return httpx.Response(
            200, json={**sync_changes(), "next_cursor": "saved", "has_more": False}
        )

    async def scenario():
        async with plaid_client(handler) as client:
            await PlaidService(store, plaid_env, client).exchange("fixture-public")
        item = store.get_item("item")
        assert item["institutionName"] == "Connected institution"
        assert (
            decrypt_token(item["token"], plaid_env["TOKEN_ENCRYPTION_KEY"], "item")
            == "fixture-access"
        )
        assert item["status"] == ("error" if sync_fails else "connected")
        assert item["cursor"] == (None if sync_fails else "saved")
        assert "fixture-secret" not in str(store.items())

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [200, 500])
def test_failed_exchange_does_not_save_partial_connection(store, plaid_env, plaid_client, status):
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(status, json={})) as client:
            with pytest.raises(PlaidError, match="Bank connection could not complete"):
                await PlaidService(store, plaid_env, client).exchange(
                    "fixture-public", "Fixture Bank"
                )
        assert store.items() == []

    asyncio.run(scenario())


def test_sync_without_item_sums_each_connected_institution(
    store, plaid_env, plaid_client, sync_changes, bank_account, bank_transaction
):
    def handler(request):
        item_id = json.loads(request.content)["access_token"].removesuffix("-access")
        if request.url.path == "/accounts/get":
            return httpx.Response(200, json={"accounts": [bank_account(account_id=item_id)]})
        return httpx.Response(
            200,
            json={
                **sync_changes([bank_transaction(f"{item_id}-transaction", account_id=item_id)]),
                "next_cursor": "saved",
                "has_more": False,
            },
        )

    async def scenario():
        async with plaid_client(handler) as client:
            service = PlaidService(store, plaid_env, client)
            assert await service.sync() == {"added": 0, "modified": 0, "removed": 0}
            for item_id in ("first", "second"):
                store.save_item(
                    item_id,
                    encrypt_token(f"{item_id}-access", plaid_env["TOKEN_ENCRYPTION_KEY"], item_id),
                    "Fixture Bank",
                )
            assert await service.sync() == {"added": 2, "modified": 0, "removed": 0}
        assert [store.get_item(item_id)["cursor"] for item_id in ("first", "second")] == [
            "saved",
            "saved",
        ]
        assert len(store.transactions("personal")) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error_code", ["ITEM_LOGIN_REQUIRED", "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"]
)
def test_sync_reconnects_or_stops_after_bounded_pagination_retries(
    store, plaid_env, plaid_client, save_plaid_item, error_code
):
    calls = []

    def handler(request):
        calls.append((request.url.path, json.loads(request.content).get("cursor")))
        return httpx.Response(
            400, json={"error_code": error_code, "error_message": "fixture-secret"}
        )

    async def scenario():
        save_plaid_item(store)
        store.apply_sync("item", {"added": [], "modified": [], "removed": []}, "original")
        async with plaid_client(handler) as client:
            service = PlaidService(store, plaid_env, client)
            with pytest.raises(PlaidError):
                await service.sync("item")
        item = store.items()[0]
        assert item["status"] == ("reconnect" if error_code == "ITEM_LOGIN_REQUIRED" else "error")
        assert store.get_item("item")["cursor"] == "original"
        assert "fixture-secret" not in str(item)
        assert len(calls) == (1 if error_code == "ITEM_LOGIN_REQUIRED" else 3)
        assert all(call == ("/transactions/sync", "original") for call in calls)
        assert store.transactions("personal") == []

    asyncio.run(scenario())


def test_sync_bounds_page_count_without_committing_partial_results(
    store, plaid_env, plaid_client, save_plaid_item, monkeypatch, sync_changes
):
    async def scenario():
        save_plaid_item(store)
        async with plaid_client(lambda _: httpx.Response(200, json={})) as client:
            service = PlaidService(store, plaid_env, client)
            calls = []

            async def endless_page(endpoint, payload):
                calls.append(endpoint)
                return {**sync_changes(), "has_more": True, "next_cursor": "another-page"}

            monkeypatch.setattr(service, "_call", endless_page)
            with pytest.raises(PlaidError, match="Bank sync could not complete"):
                await service.sync("item")
        assert calls == ["/transactions/sync"] * 2000
        assert store.get_item("item")["cursor"] is None
        assert store.transactions("personal") == []

    asyncio.run(scenario())


def test_remote_disconnect_failure_preserves_connection(
    store, plaid_env, plaid_client, save_plaid_item
):
    async def scenario():
        save_plaid_item(store)
        original = store.get_item("item")
        async with plaid_client(lambda _: httpx.Response(500, json={})) as client:
            with pytest.raises(PlaidError, match="Bank disconnection could not complete"):
                await PlaidService(store, plaid_env, client).disconnect("item")
        assert store.get_item("item") == original

    asyncio.run(scenario())


def test_close_cancels_inflight_sync_without_closing_injected_transport(
    store, plaid_env, plaid_client
):
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={})) as client:
            service = PlaidService(store, plaid_env, client)
            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def sync(_item_id):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            service.sync = sync
            service.queue_sync("item")
            await started.wait()
            task = service._queued["item"]["task"]
            await service.close()
            assert cancelled.is_set()
            assert task.cancelled()
            assert not service._queued
            assert not client.is_closed

    asyncio.run(scenario())


def test_queued_sync_failure_is_contained_and_worker_is_removed(store, plaid_env, plaid_client):
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={})) as client:
            service = PlaidService(store, plaid_env, client)

            async def sync(_item_id):
                raise PlaidError("Safe retry required")

            service.sync = sync
            service.queue_sync("item")
            await service._queued["item"]["task"]
            assert not service._queued
            await service.close()

    asyncio.run(scenario())
