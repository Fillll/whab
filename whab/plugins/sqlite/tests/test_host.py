"""Installed SQLite selection and factory persistence through the host."""

from fastapi.testclient import TestClient
from whab_sqlite.plugin import plugin as sqlite_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


def settings(home, storage="sqlite", plugins=()):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", plugins, storage, "")


def test_default_storage_selection_reopens_existing_database(tmp_path):
    # Real installed entry points select SQLite, with all runtime files in tmp_path.
    app = create_app(settings=settings(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:5173") as client:
        assert app.state.storage_plugin == "sqlite"
        selected = client.get("/api/plugins").json()["plugins"]
        assert [entry["id"] for entry in selected] == ["sqlite"]
        assert selected[0]["has_storage"] is True
        response = client.post(
            "/api/accounts",
            json={
                "workspace": "personal",
                "name": "Saved cash",
                "type": "depository",
                "balance": 9000,
            },
        )
        assert response.status_code == 200
        account_id = response.json()["accountId"]
    assert (tmp_path / "data" / "ihab-sandbox.sqlite").is_file()
    reopened_app = create_app(settings=settings(tmp_path))
    with TestClient(reopened_app, base_url="http://127.0.0.1:5173") as client:
        saved = client.get("/api/workspace?workspace=personal").json()["accounts"]
        assert len(saved) == 1
        assert (saved[0]["id"], saved[0]["name"], saved[0]["balance"]) == (
            account_id,
            "Saved cash",
            9000,
        )
    assert not (tmp_path / "data" / "whab-sandbox.sqlite").exists()


def test_explicit_sqlite_registry_constructs_backend_through_storage_factory(tmp_path):
    app = create_app(settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin,)))
    with TestClient(app, base_url="http://127.0.0.1:5173") as client:
        assert client.get("/api/workspace?workspace=demo").status_code == 200
        assert app.state.storage_plugin == "sqlite"
    assert (tmp_path / "data" / "ihab-sandbox.sqlite").is_file()
