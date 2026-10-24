"""Real provider continuations prevent native authority changes before fencing."""

import asyncio

import pytest

from tests.test_bank_runtime import Provider
from tests.web.test_live_plugins import running
from tests.web.test_storage_migration import migrate
from tests.web.test_storage_replicas import attach, promoted
from whab.app import create_app
from whab.config import Settings
from whab.plugins import (
    BankAdapter,
    Capability,
    Plugin,
    PluginContract,
    PluginRegistry,
    load_plugins,
)
from whab.services.bank_runtime import BankCoordinator
from whab.storage.bank import BankStore


@pytest.mark.parametrize("primary,destination", [("sqlite", "json"), ("json", "sqlite")])
@pytest.mark.parametrize("stage", ["ready", "exchange_uncertain", "apply_uncertain"])
@pytest.mark.parametrize("control", ["migration", "promotion"])
def test_idle_real_bank_flow_blocks_authority_and_terminal_cancellation_releases_it(
        tmp_path, monkeypatch, primary, destination, stage, control):
    async def scenario():
        descriptor = Plugin("fictional-bank", "Fictional bank", bank=BankAdapter(lambda _: pytest.fail("Use reviewed injected provider")),
            contract=PluginContract(lifecycle="live", provides=(Capability("lifecycle.background"),)))
        native = load_plugins((primary,)).storage_plugin(primary)
        settings = Settings(tmp_path, 5173, "sandbox", "", "", "11" * 32, False, "", "", (descriptor.id,), ui="", storage=primary)
        injected = {}
        app = create_app(settings=settings, plugins=PluginRegistry((native, descriptor)), bank_services=injected)
        tracked = app.state.tracked_store
        provider = Provider()
        bank = BankCoordinator(provider, tracked, BankStore(tracked, descriptor.id), budget_id="default",
            environment="sandbox", provider_id=descriptor.id, generation=1, home=tmp_path / "fictional-flows",
            encryption_key="11" * 32, code_identity="fictional-code-v1")
        injected[descriptor.id] = bank
        async with running(app) as client:
            copy_id = await attach(client, app, destination) if control == "promotion" else None
            prefix = "/api/banks/" + descriptor.id
            ready = await client.post(prefix + "/link-token", json={})
            assert ready.status_code == 200
            flow_id = ready.json()["flow_id"]
            if stage == "exchange_uncertain":
                provider.failure = ("exchange_token", OSError("fictional private response loss"))
            elif stage == "apply_uncertain":
                def unknown(_operation):
                    raise OSError("fictional native dispatch outcome")
                monkeypatch.setattr(bank.broker, "execute", unknown)
            if stage != "ready":
                response = await client.post(prefix + "/exchange", json={"flowId": flow_id, "publicToken": "fictional-public-token"})
                assert response.status_code == 409 and response.json()["code"] == stage
            status_path = prefix + "/continuations/" + flow_id
            original_flow = (await client.get(status_path)).json()
            assert original_flow["stage"] == stage
            original_evidence = tracked.commit_evidence()
            journal = app.state.storage_runtime.coordinator.journal
            original_decision = journal.read()
            if control == "migration":
                result = await migrate(client, app, destination)
            else:
                response = await client.post(f"/api/storage/copies/{copy_id}/promote", json={})
                assert response.status_code == 202
                await promoted(app)
                result = (await client.get("/api/storage/copies")).json()
            operation = result["operations"][-1]
            assert operation["state"] == "failed" and operation["error"]["code"] == "continuation_busy"
            assert operation["error"]["plugin_id"] == descriptor.id
            assert tracked.commit_evidence() == original_evidence
            assert journal.read() == original_decision
            assert (await client.get(status_path)).json() == original_flow
            assert app.state.storage_plugin == primary
            assert app.state.budget_runtime.status()["admission"] == "open"
            assert app.state.budget_runtime._active.services["banks"][descriptor.id] is bank
            assert [command for command, _ in provider.calls].count("exchange_token") == (0 if stage == "ready" else 1)
            if stage == "ready":
                cancelled = await client.delete(status_path)
                assert cancelled.status_code == 200 and cancelled.json()["stage"] == "cancelled"
                if control == "migration":
                    result = await migrate(client, app, destination)
                else:
                    response = await client.post(f"/api/storage/copies/{copy_id}/promote", json={})
                    assert response.status_code == 202
                    await promoted(app)
                    result = (await client.get("/api/storage/copies")).json()
                assert result["operations"][-1]["state"] == "complete"
                assert app.state.storage_plugin == destination
                assert app.state.budget_runtime._active.services["banks"][descriptor.id] is bank
    asyncio.run(scenario())
