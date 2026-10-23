"""Unsaved browser edits never become forced reloads during a UI handover."""

import asyncio

import pytest

from whab.plugins import PluginError
from whab.plugins.ui_views import BrowserViews

OLD, NEW = "a" * 64, "b" * 64


def test_ui_handover_waits_for_saved_and_frozen_views_and_keeps_other_budgets_independent():
    async def scenario():
        views, other = BrowserViews(OLD), BrowserViews(OLD)
        first = views.register(OLD, dirty=True)["view"]
        second = views.register(OLD)["view"]
        task = asyncio.create_task(views.prepare(NEW))
        await asyncio.sleep(0)
        ticket = views.status(first)["pending"]["id"]
        assert other.register(OLD)["generation"] == OLD
        assert not task.done() and views.generation == OLD
        with pytest.raises(PluginError):
            views.update(first, OLD, dirty=True, prepared=ticket)
        views.update(first, OLD, dirty=False, prepared=ticket)
        assert not task.done()
        views.update(second, OLD, dirty=False, prepared=ticket)
        assert await task == ticket
        views.commit(ticket)
        assert views.status(first)["reload"] and views.generation == NEW
        # A previously offline view retains unsaved edits and its original assets.
        offline = views.register(OLD, dirty=True)["view"]
        assert not views.status(offline)["reload"]
        assert views.update(offline, OLD, dirty=False)["reload"]
        assert other.generation == OLD
    asyncio.run(scenario())


def test_new_view_after_readiness_defers_commit_and_abort_resumes_old_views():
    async def scenario():
        views = BrowserViews(OLD)
        ticket = await views.prepare(NEW)
        late = views.register(OLD, dirty=True)["view"]
        with pytest.raises(PluginError, match="browser view changed"):
            views.commit(ticket)
        assert views.generation == OLD
        views.abort(ticket)
        assert views.status(late)["pending"] is None and not views.status(late)["reload"]
    asyncio.run(scenario())


def test_durable_publication_preserves_a_late_dirty_view_without_changing_its_document():
    async def scenario():
        views = BrowserViews(OLD)
        ticket = await views.prepare(NEW)
        views.validate(ticket)
        # The durable filesystem publication can yield to an offline browser.
        late = views.register(OLD, dirty=True)["view"]
        views.publish(ticket)
        assert views.generation == NEW and not views.status(late)["reload"]
        assert views.update(late, OLD, dirty=False)["reload"]
    asyncio.run(scenario())


def test_timeout_and_cancellation_clear_preparation_without_losing_view_edits():
    async def scenario():
        views = BrowserViews(OLD)
        view = views.register(OLD, dirty=True)["view"]
        with pytest.raises(PluginError) as failed:
            await views.prepare(NEW, timeout=0)
        assert failed.value.code == "ui_views_busy"
        assert views.status(view)["dirty"] and views.pending is None
        task = asyncio.create_task(views.prepare(NEW))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert views.pending is None and views.generation == OLD
        views.unregister(view)
        assert await views.prepare(NEW)
    asyncio.run(scenario())


def test_expired_view_cannot_ack_another_view_or_force_a_dirty_reload():
    now = [0]
    views = BrowserViews(OLD, clock=lambda: now[0], lifetime=60)
    view = views.register(OLD, dirty=True)["view"]
    now[0] = 61
    fresh = views.register(OLD)["view"]
    assert view not in views.views and fresh in views.views
    with pytest.raises(PluginError):
        views.update(view, OLD, dirty=False)
    with pytest.raises(PluginError):
        views.update(fresh, NEW, dirty=False)
    with pytest.raises(PluginError):
        views.update(fresh, OLD, dirty=False, prepared="wrong")
    for invalid in (None, "a" * 63, "x" * 64, True):
        with pytest.raises(PluginError):
            views.register(invalid)
