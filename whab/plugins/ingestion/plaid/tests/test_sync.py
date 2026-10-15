"""Provider pagination, reconnects and notifications use mocked HTTP only."""

import asyncio
import json

import httpx
import pytest
from whab_plaid.service import PlaidError, PlaidService


def test_pagination_restarts_original_cursor_and_saves_all_pages(
    store, bank_account, bank_transaction, plaid_client, plaid_env, save_plaid_item, sync_changes
):
    async def scenario():
        save_plaid_item(store)
        cursors = []

        def handler(request):
            if request.url.path == "/accounts/get":
                return httpx.Response(200, json={"accounts": [bank_account()]})
            payload = json.loads(request.content)
            cursors.append(payload.get("cursor"))
            number = len(cursors)
            if number == 2:
                return httpx.Response(
                    400,
                    json={
                        "error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
                        "error_message": "private bank detail never exposed",
                    },
                )
            return httpx.Response(
                200,
                json={
                    **sync_changes(
                        [bank_transaction("discarded" if number == 1 else f"kept-{number}")]
                    ),
                    "next_cursor": f"cursor-{number}",
                    "has_more": number in {1, 3},
                    "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
                },
            )

        async with plaid_client(handler) as client:
            service = PlaidService(store, plaid_env, client)
            await service.sync("item")
        assert cursors == [None, "cursor-1", None, "cursor-3"]
        assert {t["id"] for t in store.transactions("personal")} == {"kept-3", "kept-4"}
        assert store.get_item("item")["cursor"] == "cursor-4"

    asyncio.run(scenario())


def test_failed_sync_does_not_advance_and_does_not_leak_remote_error(
    store, bank_transaction, plaid_client, plaid_env, save_plaid_item, sync_changes
):
    async def scenario():
        save_plaid_item(store)

        def handler(request):
            if request.url.path == "/accounts/get":
                return httpx.Response(500, json={"error_message": "fixture-secret private-token"})
            return httpx.Response(
                200,
                json={
                    **sync_changes([bank_transaction("new")]),
                    "has_more": False,
                    "next_cursor": "advance",
                },
            )

        async with plaid_client(handler) as client:
            service = PlaidService(store, plaid_env, client)
            with pytest.raises(PlaidError, match="Bank sync could not complete") as error:
                await service.sync("item")
            assert "fixture-secret" not in str(error.value)
        assert store.transactions("personal") == []
        assert store.get_item("item")["cursor"] is None
        assert store.items()[0]["error"] == "Sync could not complete. Try again."

    asyncio.run(scenario())


def test_supplied_merchant_artwork_survives_sync_without_image_requests(
    store, bank_account, bank_transaction, plaid_client, plaid_env, save_plaid_item, sync_changes
):
    async def scenario():
        save_plaid_item(store)
        requests = []
        artwork = "https://plaid-merchant-logos.plaid.com/fixture.png"

        def handler(request):
            requests.append(request.url.path)
            if request.url.path == "/accounts/get":
                return httpx.Response(200, json={"accounts": [bank_account()]})
            assert request.url.path == "/transactions/sync"
            return httpx.Response(
                200,
                json={
                    **sync_changes([
                        bank_transaction("artwork", logo_url=artwork),
                        bank_transaction("fallback", logo_url="https://example.com/private.png"),
                    ]),
                    "has_more": False,
                    "next_cursor": "artwork-cursor",
                },
            )

        async with plaid_client(handler) as client:
            await PlaidService(store, plaid_env, client).sync("item")
        records = {row["id"]: row for row in store.transactions("personal")}
        assert records["artwork"]["merchantLogo"] == "merchant-fixture"
        assert records["fallback"].get("merchantLogo") is None
        assert store.get_item("item")["cursor"] == "artwork-cursor"
        assert requests == ["/transactions/sync", "/accounts/get"]

    asyncio.run(scenario())


def test_reconnect_update_mode_and_disconnect_preserves_ledger(
    store,
    apply_plaid_sync,
    bank_account,
    bank_transaction,
    plaid_client,
    plaid_env,
    save_plaid_item,
    sync_changes,
):
    async def scenario():
        save_plaid_item(store)
        apply_plaid_sync("item", sync_changes([bank_transaction()]), "one", [bank_account()])
        payloads = []

        def handler(request):
            payloads.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"link_token": "fixture-link"})

        async with plaid_client(handler) as client:
            service = PlaidService(store, plaid_env, client)
            assert await service.link_token("item") == "fixture-link"
            assert "products" not in payloads[0][1]
            assert "access_token" in payloads[0][1]
            await service.disconnect("item")
        assert store.get_item("item")["token"] is None
        assert store.accounts("personal")[0]["stale"] is True
        assert len(store.transactions("personal")) == 1

    asyncio.run(scenario())


def test_mid_sync_webhook_is_not_lost(store, plaid_client, plaid_env):
    async def scenario():
        service = PlaidService(
            store, plaid_env, plaid_client(lambda _: httpx.Response(200, json={}))
        )
        calls = 0
        first = asyncio.Event()

        async def sync(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                await first.wait()

        service.sync = sync
        service.queue_sync("item")
        await asyncio.sleep(0)
        service.queue_sync("item")
        service.queue_sync("item")
        first.set()
        await asyncio.sleep(0)
        assert calls == 2
        assert not service._queued
        await service.client.aclose()

    asyncio.run(scenario())
