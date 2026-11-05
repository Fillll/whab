"""Local authentication precedes activation and retains financial guards."""

from fastapi.testclient import TestClient

from whab import hosting
from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


def test_local_authentication_and_revision_checks_cannot_be_bypassed(store, tmp_path, monkeypatch):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()))
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    activated = []
    async def activate(_):
        activated.append(1)
    monkeypatch.setattr(app.state.host, "activate", activate)
    identifier = "11111111-1111-4111-8111-111111111111"
    app.state.host.recent[identifier] = {"name": "Fictional", "path": "fictional.whab"}
    with TestClient(app, base_url="https://localhost:5173") as client:
        prefix = "/budgets/default/api/local/v1"
        assert client.get(prefix + "/queries/accounts").status_code == 401
        assert client.get(f"/budgets/{identifier}/api/local/v1/queries/accounts").status_code == 401
        assert not activated
        client.headers["Authorization"] = "Bearer " + owner.token
        response = client.get(prefix + "/queries/accounts")
        assert response.status_code == 200
        revision = response.json()["revision"]
        payload = {"operation": "add_account", "workspace": "personal", "payload": {
            "name": "Fictional checking", "type": "depository", "balance": 1000},
            "request_id": "fictional-intent", "expected_revision": "stale"}
        assert client.post(prefix + "/commands", json=payload).status_code == 409
        payload["expected_revision"] = revision
        assert client.post(prefix + "/commands", json=payload).status_code == 200
        assert client.post(prefix + "/commands", json=payload).status_code == 200
        assert len(store.accounts("personal")) == 1
        assert client.post("/budgets/demo/api/local/v1/sync", json={"plugin": "plaid"}).status_code == 403
        client.headers["Origin"] = "https://example.com"
        assert client.get(prefix + "/queries/accounts").status_code == 403
        client.headers.pop("Origin")
        assert client.post(prefix + "/commands", json={**payload, "unexpected": True}).status_code == 400
        assert len(store.accounts("personal")) == 1
