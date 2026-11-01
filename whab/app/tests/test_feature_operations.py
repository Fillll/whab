"""Generic plugin dispatch shares the ledger's scope, revisions and retry rules."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from whab.plugins import FeatureOperations, RemoteLedgerInterface, StoreError
from whab.plugins.remote_transport import RemoteStore
from whab.services.ledger import LedgerService, RequestConflict, RevisionConflict
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router


def register(service):
    values = []
    def query(workspace, action, params):
        return {"workspace": workspace, "values": values.copy(), "params": params, "token": "private"}
    def command(workspace, action, params):
        values.append(params["value"])
        return {"count": len(values)}
    service.register_feature("fictional", FeatureOperations(("read",), ("append",), query, command, adapter=values))
    return values


def test_registration_dispatch_replay_and_scope(store):
    service = LedgerService(store)
    values = register(service)
    assert service.operation_lock is service.operation_lock
    assert service.feature_adapter("fictional") is values
    with pytest.raises(ValueError):
        register(service)
    with pytest.raises(ValueError):
        service.register_feature("bad id", object())
    with pytest.raises(StoreError):
        service.feature_adapter("absent")
    query = {"pluginId": "fictional", "action": "read", "parameters": {}}
    result = service.query("feature", "demo", parameters=query)
    assert result["data"]["workspace"] == "demo" and "token" not in result["data"]
    payload = {"pluginId": "fictional", "action": "append", "parameters": {"value": 1}}
    changed = service.command("feature", "demo", payload, request_id="one", expected_revision=result["revision"])
    assert service.command("feature", "demo", payload, request_id="one", expected_revision=result["revision"]) == changed
    assert values == [1]
    with pytest.raises(RequestConflict):
        service.command("feature", "demo", {**payload, "parameters": {"value": 2}}, request_id="one", expected_revision=result["revision"])
    with pytest.raises(RevisionConflict):
        service.command("feature", "demo", payload, request_id="two", expected_revision=result["revision"])
    for bad in (None, {}, {**query, "parameters": []}, {**query, "pluginId": 1}, {**query, "parameters": {"x": float("nan")}}, {**query, "parameters": {"x": "x" * 1024 * 1024}}, {**query, "action": "absent"}, {**query, "pluginId": "missing"}):
        with pytest.raises(StoreError):
            service.query("feature", "personal", parameters=bad)
    with pytest.raises(StoreError):
        service.query("accounts", "personal", parameters={})


@pytest.mark.parametrize("args", [dict(), dict(queries=[]), dict(queries=("bad name",), query=lambda: None), dict(queries=("read", "read"), query=lambda: None), dict(queries=("read",)), dict(query=lambda: None)])
def test_feature_records_reject_invalid_contracts(args):
    with pytest.raises(ValueError):
        FeatureOperations(**args)


def test_authenticated_parameter_queries_and_client_forwarding(store, monkeypatch):
    service = LedgerService(store, budget_id="fictional-budget")
    values = register(service)
    token = "fictional-access-" + "x" * 32
    bound = BoundRemoteInterface(RemoteLedgerInterface(), SimpleNamespace(service=service, config={"SERVER_TOKEN": token}, budget_id="fictional-budget"))
    app = FastAPI()
    app.include_router(create_remote_router(lambda: bound))
    with TestClient(app, base_url="https://localhost") as connection:
        body = {"operation": "feature", "workspace": "personal", "parameters": {"pluginId": "fictional", "action": "read", "parameters": {}}}
        path = "/remote/v1/budgets/fictional-budget/queries"
        assert connection.post(path, json=body).status_code == 401
        headers = {"Authorization": "Bearer " + token}
        assert connection.post(path, json={**body, "extra": True}, headers=headers).status_code == 422
        assert connection.post(path, json=body, headers=headers).status_code == 200
        assert connection.post(path.replace("fictional-budget", "other"), json=body, headers=headers).status_code == 404
        def transport(request):
            response = connection.request(request.method, str(request.url), content=request.content, headers=request.headers)
            return httpx.Response(response.status_code, content=response.content)
        remote = RemoteStore("https://localhost", token, "fictional-budget", transport=httpx.MockTransport(transport))
        proxy = LedgerService(remote)
        assert "fictional" in proxy.query("features", "personal")["data"]
        assert proxy.query("feature", "personal", parameters=body["parameters"])["data"]["values"] == []
        proxy.command("feature", "personal", {"pluginId": "fictional", "action": "append", "parameters": {"value": 42}}, request_id="proxy-one", expected_revision=proxy.revision)
        assert values == [42]
        remote.close()
