"""Plugin settings use the same local request guards and preserve running APIs."""

import pytest
from fastapi.testclient import TestClient
from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


@pytest.fixture
def client(tmp_path, store):
    settings = Settings(tmp_path, 5173, "sandbox", "", "FICTIONAL_SECRET", "", False, "", "", (), ui="")
    with TestClient(
        create_app(store=store, settings=settings, plugins=PluginRegistry((sqlite_plugin,))),
        base_url="https://127.0.0.1:5173",
    ) as client:
        yield client


def test_management_api_saves_pending_selection_and_keeps_existing_discovery_compatible(client, tmp_path):
    before = client.get("/api/plugins").json()
    response = client.get("/api/plugins/management")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "FICTIONAL_SECRET" not in response.text
    saved = client.put("/api/plugins/management", json={"plugins": ["plaid"]})
    assert saved.status_code == 200
    assert saved.json()["configured"] == ["plaid"] and saved.json()["restart_required"]
    assert client.get("/api/plugins").json() == before
    assert client.get("/api/plaid/items").status_code == 404  # Activation waits for restart.
    assert (tmp_path / "plugins.json").is_file()
    assert client.get("/api/workspace?workspace=demo").status_code == 200


@pytest.mark.parametrize("payload", [
    {}, {"plugins": "plaid"}, {"plugins": [1]}, {"plugins": [], "ui": "web"},
    {"plugins": ["plaid", "plaid"]}, {"plugins": ["sqlite"]},
    {"plugins": ["web"]}, {"plugins": ["missing-plugin"]},
])
def test_management_body_validation_cannot_change_required_plugins(client, tmp_path, payload):
    assert client.put("/api/plugins/management", json=payload).status_code == 400
    assert not (tmp_path / "plugins.json").exists()


def test_management_keeps_host_origin_content_type_and_body_size_guards(client, tmp_path):
    path = "/api/plugins/management"
    assert client.get(path, headers={"host": "foreign.example"}).status_code == 403
    assert client.put(path, json={"plugins": []}, headers={"origin": "https://foreign.example"}).status_code == 403
    assert client.put(path, content='{"plugins":[]}', headers={"content-type": "text/plain"}).status_code == 415
    assert client.put(path, content="x" * (32 * 1024 + 1), headers={"content-type": "application/json"}).status_code == 413
    assert not (tmp_path / "plugins.json").exists()


def test_headless_management_keeps_ledger_and_never_loads_an_inactive_ui(tmp_path, store, monkeypatch):
    import whab.plugins.management as management
    real = management.metadata.entry_points
    class UnloadedUI:
        name = "inactive-ui"
        group = "whab.plugins"
        def load(self):
            pytest.fail("Headless discovery must not load UI plugins")
    entries = (*real(group="whab.plugins"), UnloadedUI())
    monkeypatch.setattr(management.metadata, "entry_points", lambda **kwargs: entries)
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    with TestClient(
        create_app(store=store, settings=settings, plugins=PluginRegistry((sqlite_plugin,))),
        base_url="https://127.0.0.1:5173",
    ) as client:
        assert client.get("/").status_code == 404
        state = client.get("/api/plugins/management").json()
        assert "inactive-ui" in {row["id"] for row in state["plugins"]}
        assert client.put("/api/plugins/management", json={"plugins": []}).status_code == 200
        assert client.get("/api/workspace?workspace=demo").status_code == 200
        assert [(row["id"], row["kinds"]) for row in client.get("/api/plugins").json()["plugins"]] == [("sqlite", ["storage"])]
