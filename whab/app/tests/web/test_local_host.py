"""Local authentication precedes activation and retains financial guards."""

import pytest
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


def test_local_plugin_restart_selection_is_saved_without_live_activation(store, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from whab.plugins import PluginError, runtime

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()))
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    monkeypatch.setattr(runtime, "load_descriptors", lambda *args: (SimpleNamespace(ui=None, _process=None),))
    manager = app.state.plugin_manager
    rows = [{"id": "fictional", "installed": True, "manageable": True, "enabled": True}]
    monkeypatch.setattr(manager, "projection", lambda: {"plugins": rows, "configured": ["fictional", "retained"]})
    preflight = Mock(side_effect=PluginError("Restart needed.", code="restart_required"))
    def changed(_):
        rows[0]["enabled"] = False
    save = Mock(side_effect=changed)
    live = SimpleNamespace(activate=AsyncMock(side_effect=changed))
    monkeypatch.setattr(manager, "preflight_live", preflight)
    monkeypatch.setattr(manager, "save", save)
    monkeypatch.setattr(manager, "live", live)
    with TestClient(app, base_url="https://localhost:5173") as client:
        client.headers["Authorization"] = "Bearer " + owner.token
        path = "/budgets/default/api/local/v1/plugins"
        body = {"action": "disable", "plugin": "fictional"}
        assert client.post(path, json=body).status_code == 200
        save.assert_called_once_with(("retained",))
        live.activate.assert_not_awaited()
        # Busy/invalid selections must fail without saving a startup fallback.
        save.reset_mock()
        rows[0]["enabled"] = True
        preflight.side_effect = PluginError("Busy.", code="transition_busy")
        assert client.post(path, json=body).status_code == 400
        save.assert_not_called()
        # A failure after activation starts must never trigger a second write.
        preflight.side_effect = None
        live.activate.side_effect = PluginError("Restart needed.", code="restart_required")
        assert client.post(path, json=body).status_code == 400
        save.assert_not_called()
        live.activate.side_effect = changed
        assert client.post(path, json=body).status_code == 200
        live.activate.assert_awaited_with(("retained",))
        # A transition that returns without applying the choice is not success.
        rows[0]["enabled"] = True
        live.activate.side_effect = None
        assert client.post(path, json=body).status_code == 400


@pytest.mark.parametrize("plugin,code", [("sqlite", "required_plugin"), ("cli", "management_tool")])
@pytest.mark.parametrize("action", ["enable", "disable"])
def test_local_protected_plugins_reject_changes_without_loading_or_transitioning(store, tmp_path, monkeypatch, plugin, code, action):
    from whab.plugins import runtime

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()))
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    monkeypatch.setattr(runtime, "load_descriptors", lambda *args: pytest.fail("Protected selection must not import code"))
    with TestClient(app, base_url="https://localhost:5173") as client:
        client.headers["Authorization"] = "Bearer " + owner.token
        path = "/budgets/default/api/local/v1/plugins"
        before = client.get(path).json()
        response = client.post(path, json={"action": action, "plugin": plugin})
        assert response.status_code == 409 and response.json()["code"] == code
        after = client.get(path).json()
        assert after == before
        assert not (tmp_path / "plugins.json").exists()


def test_disabling_an_inactive_visual_does_not_disable_the_current_interface(store, tmp_path, monkeypatch):
    from whab.plugins import runtime

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()))
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    state = {"plugins": [{"id": "other-ui", "installed": True, "manageable": True, "enabled": False}]}
    monkeypatch.setattr(app.state.plugin_manager, "projection", lambda: state)
    monkeypatch.setattr(runtime, "load_descriptors", lambda *args: pytest.fail("No-op must not load or transition visuals"))
    with TestClient(app, base_url="https://localhost:5173") as client:
        client.headers["Authorization"] = "Bearer " + owner.token
        result = client.post("/budgets/default/api/local/v1/plugins", json={"action": "disable", "plugin": "other-ui"})
        assert result.status_code == 200 and result.json()["plugins"] == state["plugins"]


def test_local_web_is_optional_and_disable_is_durable_without_disabling_storage(tmp_path, monkeypatch):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="web")
    app = create_app(settings=settings)
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    with TestClient(app, base_url="https://localhost:5173") as client:
        client.headers["Authorization"] = "Bearer " + owner.token
        path = "/budgets/default/api/local/v1/plugins"
        rows = {row["id"]: row for row in client.get(path).json()["plugins"]}
        assert rows["web"]["enabled"] and rows["web"]["manageable"] and not rows["web"]["required"]
        assert client.get("/budgets/home/budget/current-month/usd").status_code == 200
        result = client.post(path, json={"action": "disable", "plugin": "web"})
        assert result.status_code == 200
        rows = {row["id"]: row for row in result.json()["plugins"]}
        assert not rows["web"]["enabled"] and not rows["web"]["active"]
        assert rows["sqlite"]["required"] and rows["sqlite"]["enabled"]
        assert client.get("/budgets/home/budget/current-month/usd").status_code == 404
        generation = app.state.budget_runtime.status()["generation"]
        assert client.post(path, json={"action": "disable", "plugin": "web"}).status_code == 200
        assert app.state.budget_runtime.status()["generation"] == generation
        assert client.get(path.replace("/plugins", "/queries/accounts")).status_code == 200
    reopened = create_app(settings=settings)
    with TestClient(reopened, base_url="https://localhost:5173") as client:
        assert client.get("/budgets/home/budget/current-month/usd").status_code == 404


def test_local_budget_creation_is_authenticated_empty_and_preserves_paths(store, tmp_path, monkeypatch):
    from tests.budgets.helpers import decode_budget

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()))
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    path = tmp_path / "chosen" / "fictional.whab"
    with TestClient(app, base_url="https://localhost:5173") as client:
        url = "/api/local/v1/budgets"
        body = {"name": "Fictional CLI budget", "path": str(path)}
        assert client.post(url, json=body).status_code == 401
        assert not path.exists()
        client.headers["Authorization"] = "Bearer " + owner.token
        created = client.post(url, json=body)
        assert created.status_code == 200, created.text
        result = created.json()
        assert result["path"] == str(path) and result["storage"] == "sqlite"
        assert result["id"] in {row["id"] for row in client.get(url).json()["budgets"]}
        manifest, ledger = decode_budget(path.read_bytes())
        assert manifest["name"] == body["name"]
        assert ledger["accounts"] == ledger["transactions"] == ledger["assignments"] == []
        selection = client.get(f'/budgets/{result["id"]}/api/local/v1/plugins').json()
        assert selection["configured"] == []
        original = path.read_bytes()
        assert client.post(url, json=body).status_code == 400
        assert client.post(url, json={"name": result["id"]}).status_code == 400
        assert client.post(url, json={**body, "name": "Another name"}).status_code == 400
        assert path.read_bytes() == original
        assert client.post(url, json={"name": "default"}).status_code == 400
        assert client.post(url, json={"name": " ", "path": str(tmp_path / "blank.whab")}).status_code == 400
        assert client.post(url, json={"name": "Other", "path": "relative.whab"}).status_code == 400
        assert client.post(url, json={"name": "Other", "path": str(tmp_path / "wrong.txt")}).status_code == 400
        managed = client.post(url, json={"name": "Managed"})
        assert managed.status_code == 200
        assert managed.json()["path"].startswith(str(tmp_path / "budgets"))
        assert client.post("/budgets/demo/api/local/v1/budgets", json={"name": "Demo write"}).status_code == 403
        assert client.get("/budgets/demo/api/local/v1/budgets").status_code == 403
        # Creation leaves the original address and authority unchanged.
        assert app.state.host.default_id == "default"


def test_local_summary_uses_core_currency_projection_and_budget_lease(store, tmp_path, monkeypatch):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()))
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    store.add_account("personal", {"name": "USD account", "type": "depository", "balance": 1000, "currency": "USD"})
    store.add_account("personal", {"name": "EUR account", "type": "depository", "balance": 3000, "currency": "EUR"})
    with TestClient(app, base_url="https://localhost:5173") as client:
        client.headers["Authorization"] = "Bearer " + owner.token
        response = client.get("/budgets/default/api/local/v1/summary", params={"month": "2026-11", "currency": "EUR"})
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["budgetId"] == "default" and value["revision"]
        assert value["data"]["currency"] == "EUR" and value["data"]["month"] == "2026-11"
        expected = store.workspace("personal", "2026-11")["summariesByCurrency"]["EUR"]
        assert value["data"]["summary"] == expected
        assert client.get("/budgets/default/api/local/v1/summary", params={"currency": "bad"}).status_code == 400
        assert client.get("/budgets/default/api/local/v1/summary", params={"month": "not-a-month"}).status_code == 400
        assert client.get("/budgets/demo/api/local/v1/summary").status_code == 403
