"""Synchronous bank storage must leave the network loop responsive and finish writes."""

import asyncio
from contextvars import ContextVar
from threading import Event, get_ident

import httpx
import pytest
from whab_plugin_plaid.async_work import run_blocking
from whab_plugin_plaid.service import PlaidService


def test_blocking_work_preserves_request_context_and_propagates_results_and_errors():
    identity = ContextVar("fixture_identity", default=None)

    async def scenario():
        identity.set("fixture request")
        loop_thread = get_ident()

        def work(value, *, increment):
            assert get_ident() != loop_thread
            assert identity.get() == "fixture request"
            return value + increment

        assert await run_blocking(work, 3, increment=4) == 7

        def fail():
            raise ValueError("fixture failure")

        with pytest.raises(ValueError, match="fixture failure"):
            await run_blocking(fail)

    asyncio.run(scenario())


@pytest.mark.parametrize("fail", [False, True])
def test_repeated_cancellation_waits_for_worker_completion(fail):
    started, release, finished = Event(), Event(), Event()

    def work():
        started.set()
        try:
            assert release.wait(5), "Test did not release its worker"
            if fail:
                raise ValueError("fixture failure after cancellation")
            return "committed"
        finally:
            finished.set()

    async def scenario():
        task = asyncio.create_task(run_blocking(work))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 5), 5)
            for _ in range(2):
                task.cancel("fixture cancellation")
                await asyncio.sleep(0)
                assert not task.done()
            assert not finished.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError, match="fixture cancellation"):
            await task
        assert finished.is_set()

    asyncio.run(scenario())


def test_sync_persistence_leaves_loop_responsive_and_retains_item_lock_until_commit(
    store, plaid_env, plaid_client, save_plaid_item, sync_changes, monkeypatch,
):
    save_plaid_item(store)
    started, release = Event(), Event()
    apply = store.apply_sync

    def persist(*args, **kwargs):
        started.set()
        assert release.wait(5), "Test did not release persistence"
        return apply(*args, **kwargs)

    monkeypatch.setattr(store, "apply_sync", persist)

    def response(request):
        return httpx.Response(200, json={"accounts": []} if request.url.path == "/accounts/get" else {
            **sync_changes(), "has_more": False, "next_cursor": "committed-fixture-cursor",
        })

    async def scenario():
        async with plaid_client(response) as client:
            service = PlaidService(store, plaid_env, client)
            task = asyncio.create_task(service.sync("item"))
            try:
                # This can run only if storage yielded the event loop. The
                # storage worker cannot finish until this coroutine releases it.
                assert await asyncio.wait_for(asyncio.to_thread(started.wait, 5), 5)
                assert service._locks["item"].locked()
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert service._locks["item"].locked()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not service._locks["item"].locked()
        assert store.get_item("item")["cursor"] == "committed-fixture-cursor"

    asyncio.run(scenario())
