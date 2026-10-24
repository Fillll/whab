"""Account classification survives the real client/server command boundary."""

from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from whab_plugin_client.plugin import RemoteStore
from whab_plugin_server.plugin import plugin as server_plugin

from whab.services.ledger import LedgerService
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router


def test_remote_scope_changes_preserve_budget_and_portable_semantics(store):
    token = "synthetic-account-scope-token-" + "a" * 32
    app = FastAPI()
    binding = BoundRemoteInterface(server_plugin.interface.adapter, SimpleNamespace(
        service=LedgerService(store, budget_id="scope"),
        config={"SERVER_TOKEN": token}, budget_id="scope",
    ))
    app.include_router(create_remote_router(lambda: binding))
    with TestClient(app, base_url="https://budget.example") as server:
        def dispatch(request):
            response = server.request(
                request.method, str(request.url), headers=dict(request.headers), content=request.content,
            )
            return httpx.Response(response.status_code, json=response.json())

        with_remote = RemoteStore(
            "https://budget.example", token, "scope", transport=httpx.MockTransport(dispatch),
        )
        try:
            account = with_remote.add_account("personal", {
                "name": "CD", "type": "deposit", "subtype": "cd", "balance": 70000,
            })
            view = with_remote.workspace("personal", "2026-10")
            assert view["summary"]["readyToAssign"] == 0
            assert view["accountSummary"]["netWorth"] == 70000
            with_remote.update_account("personal", account, {
                "type": "depository", "subtype": "savings", "onBudget": True,
            })
            assert with_remote.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 70000
            portable = with_remote.export_budget()
            assert portable["accounts"][0]["onBudget"] is True
            assert portable["accounts"][0]["subtype"] == "savings"
        finally:
            with_remote.close()
