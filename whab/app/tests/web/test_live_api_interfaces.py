"""API selection owns its authorization and dispatch in one leased generation."""

import asyncio
from dataclasses import dataclass
from threading import Event

import httpx
import pytest
from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import (
    Interface,
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    RemoteLedgerInterface,
    load_plugins,
)
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router

TOKEN = "fictional-api-private-token-" * 3


@dataclass
class Entry:
    name: str
    descriptor: Plugin
    group: str = "whab.plugins"

    def load(self):
        return self.descriptor


def api(identifier="first", **changes):
    return Plugin(identifier, identifier.title(), interface=Interface(adapter=RemoteLedgerInterface(
        token_setting=identifier.upper() + "_TOKEN", **changes)), contract=PluginContract(lifecycle="live"))


def setup(monkeypatch, home, store, *available, selected=(), child=False):
    entries = tuple(Entry(plugin.id, plugin) for plugin in (sqlite_plugin, *available))
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **_: entries)
    for plugin in available:
        monkeypatch.setenv(plugin.id.upper() + "_TOKEN", TOKEN)
    configuration = Settings(home, 5173, "sandbox", "", "", "", False, "", "", tuple(selected), ui="")
    return create_app(store=store, settings=configuration,
        plugins=PluginRegistry((sqlite_plugin, *(row for row in available if row.id in selected))), _child=child)


def url(app):
    return f"/remote/v1/budgets/{app.state.ledger_service.budget_id}/queries/accounts?workspace=personal"


def test_selection_replacement_disable_and_failed_configuration_preserve_ledger_retry(tmp_path, store, monkeypatch):
    async def run():
        first, second, broken = api(), api("second", commands=False), api("broken")
        app = setup(monkeypatch, tmp_path, store, first, second, broken)
        monkeypatch.setenv("BROKEN_TOKEN", "short")
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:5173") as client:
            path = url(app)
            headers = {"Authorization": "Bearer " + TOKEN}
            assert (await client.get(path)).status_code == 404
            assert (await client.get(path, headers={"Host": "fictional.example"})).status_code == 403
            await app.state.live_plugins.activate(("first",))
            previous = app.state.budget_runtime._active.services["interfaces"].binding
            assert (await client.get(path, headers={"Host": "fictional.example"})).status_code == 401
            assert (await client.get(path, headers={**headers, "Host": "fictional.example"})).status_code == 200
            assert (await client.get("/api/workspace?workspace=personal", headers={"Host": "fictional.example"})).status_code == 403
            assert (await client.get("/api/workspace?workspace=personal", headers={"Origin": "https://fictional.example"})).status_code == 403
            revision = app.state.ledger_service.revision
            command = path.split("/queries/")[0] + "/commands"
            body = {"operation": "add_account", "workspace": "personal", "payload": {"name": "Fictional cash", "type": "depository", "balance": 125}, "request_id": "one", "expected_revision": revision}
            response = await client.post(command, json=body, headers=headers)
            assert response.status_code == 200
            await app.state.live_plugins.activate(("second",))
            assert previous._closed
            assert (await client.post(command, json=body, headers=headers)).status_code == 405
            current = app.state.budget_runtime._active.services["interfaces"]
            with pytest.raises(PluginError):
                await app.state.live_plugins.activate(("broken",))
            assert app.state.budget_runtime._active.services["interfaces"] is current
            assert (await client.get(path, headers=headers)).status_code == 200
            assert len(store.accounts("personal")) == 1
            await app.state.live_plugins.activate(("first",))
            assert (await client.post(command, json=body, headers=headers)).json() == response.json()
            await app.state.live_plugins.activate(())
            assert (await client.get(path, headers=headers)).status_code == 404
            assert (await client.get(path, headers={**headers, "Origin": "https://fictional.example"})).status_code == 403
            assert (await client.get(path, headers={**headers, "Host": "fictional.example"})).status_code == 403
    asyncio.run(run())


@pytest.mark.parametrize("child", [False, True])
def test_active_request_pins_guard_and_dispatch_drain_and_cleartext_stays_rejected(tmp_path, store, monkeypatch, child):
    async def run():
        app = setup(monkeypatch, tmp_path, store, api(), selected=("first",), child=child)
        entered, finish = Event(), Event()
        old = app.state.budget_runtime._active.services["interfaces"].binding
        original = old.query

        def held(*args):
            entered.set()
            assert finish.wait(5)
            return original(*args)
        old.query = held
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:5173") as client:
            pending = asyncio.create_task(client.get(url(app), headers={"Authorization": "Bearer " + TOKEN, "Host": "fictional.example"}))
            assert await asyncio.to_thread(entered.wait, 5)
            change = asyncio.create_task(app.state.live_plugins.activate(()))
            await app.state.budget_runtime.wait_for_phase("draining")
            response = await asyncio.wait_for(client.get("http://fictional.example" + url(app)), 1)
            assert response.status_code == 400
            assert (await asyncio.wait_for(client.get("/api/plugins/runtime", headers={"Host": "fictional.example"}), 1)).status_code == 403
            assert (await asyncio.wait_for(client.get("/api/plugins/runtime", headers={"Origin": "https://fictional.example"}), 1)).status_code == 403
            assert not change.done() and not old._closed
            finish.set()
            assert (await pending).status_code == 200
            assert await change == 2
            assert old._closed
            with pytest.raises(Exception) as refused:
                old.query("default", "accounts", "personal", None)
            assert refused.value.status_code == 503
            assert (await client.get(url(app), headers={"Host": "fictional.example"})).status_code == 403
            assert (await client.get(url(app))).status_code == 404
    asyncio.run(run())


def test_duplicate_route_owners_reject_before_configuration_or_binding(tmp_path, store, monkeypatch):
    first, second = api(), api("second")
    app = setup(monkeypatch, tmp_path, store, first, second, selected=("first",))
    monkeypatch.setattr("whab.app.plugin_config", lambda *_: pytest.fail("Ambiguous owners must fail before configuration"))
    with pytest.raises(PluginError) as rejected:
        app.state.live_plugins.preflight(("first", "second"))
    assert rejected.value.code == "interface_conflict" and rejected.value.plugin_id == "first"
    with pytest.raises(PluginError, match="exactly one"):
        create_app(store=store, settings=Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui=""), plugins=PluginRegistry((first, second)))


def test_legacy_route_alias_is_authenticated_and_legacy_selection_requires_restart(tmp_path, store, monkeypatch):
    def legacy_factory(context):
        binding = BoundRemoteInterface(RemoteLedgerInterface(token_setting="LEGACY_TOKEN"), context)
        return create_remote_router(lambda: binding)
    legacy = Plugin("legacy", "Legacy", interface=Interface(legacy_factory))
    app = setup(monkeypatch, tmp_path, store, legacy, api(), selected=("legacy",))
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert client.get(url(app)).status_code == 401
        assert client.get(url(app), headers={"Authorization": "Bearer " + TOKEN}).status_code == 200
        with pytest.raises(PluginError) as rejected:
            app.state.live_plugins.preflight(())
        assert rejected.value.code == "restart_required"
        with pytest.raises(PluginError):
            app.state.live_plugins.preflight(("legacy", "first"))


def test_portable_settings_cannot_supply_a_machine_bearer_credential(tmp_path, monkeypatch):
    from whab.budgets.session import BudgetSession

    descriptor = Plugin("first", "Fictional API", interface=Interface(adapter=RemoteLedgerInterface(token_setting="FIRST_CREDENTIAL")), contract=PluginContract(lifecycle="live"))
    entries = tuple(Entry(row.id, row) for row in (sqlite_plugin, descriptor))
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **_: entries)
    session = BudgetSession.create(tmp_path, "sandbox", "Fictional portable", ("first",))
    session.save_plugin_data("first", settings={"FIRST_CREDENTIAL": TOKEN})
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    with pytest.raises(PluginError) as rejected:
        create_app(settings=settings, budget_session=session)
    assert rejected.value.code == "interface_startup_failed" and rejected.value.plugin_id == "first"
    assert session.plugin_record("first")["settings"] == {"FIRST_CREDENTIAL": TOKEN}


def test_api_handover_preserves_categorization_and_failed_startup_closes_its_resources(tmp_path, store, monkeypatch):
    from whab.services.categorization import CategorizationService

    category = load_plugins(("random-forest-categorization",)).descriptors()[0]
    closed = []
    original_close = CategorizationService.close

    def close(service):
        closed.append(service)
        original_close(service)

    monkeypatch.setattr(CategorizationService, "close", close)

    async def run():
        app = setup(monkeypatch, tmp_path / "active", store, api(), category, selected=(category.id,))
        runtime = app.state.budget_runtime
        service = runtime._active.services["categorization"]
        async with app.router.lifespan_context(app):
            await app.state.live_plugins.activate((category.id, "first"))
            assert runtime._active.services["categorization"] is service
            await app.state.live_plugins.activate((category.id,))
            assert runtime._active.services["categorization"] is service
            assert service not in closed
        assert service in closed

    asyncio.run(run())
    previous = len(closed)
    # An unconfigured credential fails after categorization was prepared.
    descriptor = api("broken")
    settings = Settings(tmp_path / "failed", 5173, "sandbox", "", "", "", False, "", "", (category.id, descriptor.id), ui="")
    with pytest.raises(PluginError) as rejected:
        create_app(store=store, settings=settings, plugins=PluginRegistry((sqlite_plugin, category, descriptor)))
    assert rejected.value.code == "interface_startup_failed"
    assert len(closed) == previous + 1
