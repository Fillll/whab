"""Queued fictional sync holds host admission independently of its HTTP request."""

import asyncio
from contextlib import asynccontextmanager
from threading import Event

import httpx
import pytest
from whab_plugin_plaid.plugin import create_bank, plugin
from whab_plugin_plaid.service import PlaidError, PlaidService

from whab.budgets.runtime import BudgetRuntime, RuntimeCandidate
from whab.plugins import BankAdapter, BankContext, Capability


def test_factory_retains_host_background_scope_and_truthful_descriptor_contract(store, plaid_env):
    @asynccontextmanager
    async def scope():
        yield

    context = BankContext(store, plaid_env, "sandbox", plaid_env["TOKEN_ENCRYPTION_KEY"], background_scope=scope)
    service = create_bank(context)
    assert service._background_scope is scope
    assert Capability("lifecycle.background") in plugin.contract.provides
    assert type(plugin.bank) is BankAdapter
    assert plugin.contract.lifecycle == "live" and plugin.contract.authority == "local"
    asyncio.run(service.close())


def test_queued_worker_holds_independent_lease_after_scheduling_request_and_blocks_handover(store, plaid_env, plaid_client):
    async def scenario():
        runtime = BudgetRuntime("fictional", {"bank": object()})
        started, release, prepared = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async with plaid_client(lambda _: pytest.fail("Fictional sync is injected")) as client:
            service = PlaidService(store, plaid_env, client, background_scope=runtime.background)
            calls = []

            async def sync(identifier):
                calls.append(identifier)
                started.set()
                await release.wait()

            service.sync = sync
            async with runtime.request():
                service.queue_sync("item")
                await asyncio.wait_for(started.wait(), 5)
                assert runtime.status()["active_leases"] == 2
                # Repeated notifications share one worker/lease until the
                # complete loop drains, without losing the follow-up sync.
                service.queue_sync("item")
                service.queue_sync("item")
                worker = service._queued["item"]["task"]
            assert runtime.status()["active_leases"] == 1

            async def prepare(_):
                prepared.set()
                return RuntimeCandidate({"bank": object()})

            transition = asyncio.create_task(runtime.transition(prepare))
            await asyncio.wait_for(prepared.wait(), 5)
            assert runtime.status()["admission"] == "gated"
            assert not transition.done()
            release.set()
            await asyncio.wait_for(worker, 5)
            assert await asyncio.wait_for(transition, 5) == 2
            assert calls == ["item", "item"]
            assert runtime.status()["active_leases"] == 0
            assert not service._queued
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["safe-sync", "unexpected-sync", "admission"])
def test_worker_failures_release_scope_remove_queue_and_do_not_log_private_payloads(store, plaid_env, plaid_client, failure, capsys):
    async def scenario():
        held = []

        @asynccontextmanager
        async def scope():
            if failure == "admission":
                raise RuntimeError("synthetic-private-payload")
            held.append(True)
            try:
                yield
            finally:
                held.pop()

        async with plaid_client(lambda _: pytest.fail("Fictional sync is injected")) as client:
            service = PlaidService(store, plaid_env, client, background_scope=scope)
            calls = []

            async def sync(identifier):
                assert held == [True]
                calls.append(identifier)
                raise (PlaidError if failure == "safe-sync" else RuntimeError)("synthetic-private-payload")

            service.sync = sync
            service.queue_sync("item")
            worker = service._queued["item"]["task"]
            if failure == "safe-sync":
                await worker
            else:
                with pytest.raises(RuntimeError):
                    await worker
            await asyncio.sleep(0)
            assert not held and not service._queued
            assert calls == ([] if failure == "admission" else ["item"])
            await service.close()

    asyncio.run(scenario())
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err


def test_close_cancellation_keeps_background_lease_until_native_commit_finishes(
    store, plaid_env, plaid_client, save_plaid_item, sync_changes, monkeypatch,
):
    save_plaid_item(store)
    started, release = Event(), Event()
    original = store.apply_sync

    def persist(*args, **kwargs):
        started.set()
        assert release.wait(5), "Fictional persistence was not released"
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "apply_sync", persist)

    def response(request):
        return httpx.Response(200, json={"accounts": []} if request.url.path == "/accounts/get" else {
            **sync_changes(), "has_more": False, "next_cursor": "fictional-committed-cursor",
        })

    async def scenario():
        runtime = BudgetRuntime("fictional", {"bank": object()})
        async with plaid_client(response) as client:
            service = PlaidService(store, plaid_env, client, background_scope=runtime.background)
            service.queue_sync("item")
            closing = None
            try:
                assert await asyncio.wait_for(asyncio.to_thread(started.wait, 5), 5)
                assert runtime.status()["active_leases"] == 1
                closing = asyncio.create_task(service.close())
                await asyncio.sleep(0)
                assert not closing.done()
                assert runtime.status()["active_leases"] == 1
            finally:
                release.set()
                await asyncio.wait_for(closing if closing is not None else service.close(), 5)
            assert runtime.status()["active_leases"] == 0
            assert not service._queued and not client.is_closed
            assert store.get_item("item")["cursor"] == "fictional-committed-cursor"

    asyncio.run(scenario())


def test_legacy_service_without_configuration_rejects_link_and_unsigned_webhook(store, plaid_client):
    async def scenario():
        async with plaid_client(lambda _: pytest.fail('Unconfigured legacy service cannot send HTTP')) as client:
            service = PlaidService(store, {}, client)
            with pytest.raises(PlaidError, match='not configured'):
                await service.link_token()
            assert await service.verify_webhook(b'{}', None) is False
            result = await service.webhook(b'{}', {})
            assert result.ok is False and result.status_code == 401
            await service.close()
    asyncio.run(scenario())
