"""Exercise the installed plugins together without opening a network listener."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from whab_client.plugin import RemoteStore
from whab_server.plugin import create_interface

from whab.plugins import LedgerService, StoreError


def test_client_server_commands_export_and_concurrent_conflict(store):
    token = "synthetic-protocol-token-" + "x" * 32
    service = LedgerService(store, budget_id="household")
    app = FastAPI()
    app.include_router(create_interface(SimpleNamespace(
        config={"SERVER_TOKEN": token}, service=service, budget_id="household",
    )))
    server = TestClient(app, base_url="https://budget.example")

    def forward(request):
        return server.request(
            request.method, str(request.url), headers=request.headers,
            content=request.content,
        )

    client = RemoteStore(
        "https://budget.example", token, "household", transport=httpx.MockTransport(forward),
    )
    account = client.add_account("personal", {
        "name": "Cash", "type": "depository", "balance": 10000,
    })
    category = client.add_category("personal", {"name": "Food", "group": "Living", "target": 0})
    transaction = client.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Shop", "amount": 100,
        "categoryId": category,
    })
    client.edit_transaction("personal", transaction, reviewed=True)
    client.assign("personal", category, "2026-10", 500)
    assert client.transactions("personal", "2026-10")[0]["reviewed"] is True
    assert client.export_budget() == store.export_budget()
    assert "token" not in str(client.workspace("personal", "2026-10"))

    # Another interface changed this budget after the client's last read.
    service.add_account("personal", {"name": "Other", "type": "depository", "balance": 0})
    with pytest.raises(StoreError, match="Remote budget changed"):
        client.add_account("personal", {"name": "Unsaved", "type": "depository", "balance": 0})
    assert {row["name"] for row in client.accounts("personal")} == {"Cash", "Other"}
    client.close()
    server.close()
