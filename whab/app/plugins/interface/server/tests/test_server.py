"""The remote interface only exposes authenticated, scoped public ledger operations."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from whab_plugin_server.plugin import plugin

from whab.domain.errors import StoreError
from whab.services.ledger import LedgerService
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router

TOKEN = "synthetic-server-token-" + "a" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}
BASE = "/remote/v1/budgets/budget-1"


def context(service, token=TOKEN):
    return SimpleNamespace(service=service, config={"SERVER_TOKEN": token}, budget_id="budget-1")


def client_for(service, **kwargs):
    app = FastAPI()
    binding = BoundRemoteInterface(plugin.interface.adapter, context(service))
    app.include_router(create_remote_router(lambda: binding))
    return TestClient(app, base_url="https://testserver", **kwargs)


def test_descriptor_configuration_and_auth(store):
    assert plugin.id == "server" and plugin.interface.factory is None
    assert plugin.contract.lifecycle == "live"
    with pytest.raises(StoreError, match="host bearer token"):
        BoundRemoteInterface(plugin.interface.adapter, context(LedgerService(store), "short"))
    client = client_for(LedgerService(store))
    path = BASE + "/queries/accounts?workspace=demo"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("http://testserver" + path, headers=HEADERS).status_code == 400
    assert client.get(path, headers=HEADERS).status_code == 200
    assert client.get(path.replace("budget-1", "other"), headers=HEADERS).status_code == 404
    assert client.get(path.replace("accounts", "get_item"), headers=HEADERS).status_code == 400
    assert client.get(BASE + "/queries/accounts?workspace=unknown", headers=HEADERS).status_code == 422


def test_command_success_replay_conflict_and_validation(store):
    service = LedgerService(store)
    client = client_for(service)
    revision = client.get(BASE + "/queries/workspace?workspace=personal", headers=HEADERS).json()["revision"]
    body = {
        "operation": "add_account", "workspace": "personal",
        "payload": {"name": "Cash", "type": "depository", "balance": 100},
        "request_id": "request-1", "expected_revision": revision,
    }
    response = client.post(BASE + "/commands", json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["budgetId"] == "budget-1"
    assert client.post(BASE + "/commands", json=body, headers=HEADERS).json() == response.json()
    changed = {**body, "payload": {**body["payload"], "name": "Other"}}
    assert client.post(BASE + "/commands", json=changed, headers=HEADERS).status_code == 409
    assert client.post(BASE + "/commands", json={**body, "request_id": "request-2"}, headers=HEADERS).status_code == 409
    invalid = {**body, "operation": "save_item"}
    assert client.post(BASE + "/commands", json=invalid, headers=HEADERS).status_code == 400
    assert client.post(BASE + "/commands", json={**body, "extra": "no"}, headers=HEADERS).status_code == 422
    assert client.post(BASE.replace("budget-1", "other") + "/commands", json=body, headers=HEADERS).status_code == 404


def test_storage_failures_are_sanitized():
    class Failed:
        def query(self, *args):
            raise RuntimeError("private-server-details")

        def command(self, *args, **kwargs):
            raise RuntimeError("private-server-details")

    client = client_for(Failed())
    response = client.get(BASE + "/queries/workspace?workspace=personal", headers=HEADERS)
    assert response.status_code == 503 and "private" not in response.text
    response = client.post(BASE + "/commands", headers=HEADERS, json={
        "operation": "assign", "workspace": "personal", "payload": {},
        "request_id": "one", "expected_revision": "revision",
    })
    assert response.status_code == 503 and "private" not in response.text
