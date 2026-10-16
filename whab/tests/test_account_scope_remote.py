"""Account classification survives the real client/server command boundary."""

from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from whab_client.plugin import RemoteStore
from whab_server.plugin import create_interface

from whab.services.ledger import LedgerService


def test_remote_scope_changes_preserve_budget_and_portable_semantics(store):
    token = "synthetic-account-scope-token-" + "a" * 32
    app = FastAPI()
    app.include_router(create_interface(SimpleNamespace(
        service=LedgerService(store, budget_id="scope"),
        config={"SERVER_TOKEN": token}, budget_id="scope",
    )))
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
