"""Budget activation isolates ledgers and restores portable feature setups."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.budgets.document import decode_bundle, encode_bundle
from whab.budgets.host import BudgetHost
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.domain.errors import StoreError
from whab.plugins import PluginError

BASE = "http://127.0.0.1:5173"
ZIP = {"content-type": "application/zip"}


def test_budget_file_cannot_choose_destination_for_device_client_credential(tmp_path, monkeypatch):
    import httpx

    from whab.app import plugin_config

    monkeypatch.delenv("CLIENT_URL", raising=False)
    monkeypatch.delenv("CLIENT_BUDGET_ID", raising=False)
    monkeypatch.setenv("CLIENT_TOKEN", "synthetic-device-bearer-credential")
    session = BudgetSession.create(tmp_path, "sandbox", "Untrusted connection hint", storage="client")
    session.save_plugin_data("client", settings={"CLIENT_URL": "https://untrusted.invalid", "CLIENT_BUDGET_ID": "fixture"})
    configuration = settings(tmp_path)
    assert plugin_config(configuration, session, "client") == {"CLIENT_TOKEN": "synthetic-device-bearer-credential"}

    def no_transport(*args, **kwargs):
        pytest.fail("A budget file must not redirect device credentials to its own server.")

    monkeypatch.setattr(httpx, "Client", no_transport)
    with pytest.raises(PluginError) as error:
        create_app(settings=configuration, budget_session=session)
    assert error.value.code == "storage_startup_failed"
    monkeypatch.setenv("CLIENT_URL", "https://approved.invalid")
    monkeypatch.setenv("CLIENT_BUDGET_ID", "approved-budget")
    bound = plugin_config(configuration, session, "client")
    assert bound["CLIENT_URL"] == "https://approved.invalid"
    assert bound["CLIENT_BUDGET_ID"] == "approved-budget"


def settings(home, plugins=()):
    return Settings(
        home=home, port=5173, plaid_env="sandbox", plaid_client_id="", plaid_secret="",
        encryption_key="", allow_live=False, webhook_url="", redirect_uri="",
        plugins=plugins, ui="",
    )


def add_account(client, name):
    response = client.post("/api/accounts", json={
        "workspace": "personal", "name": name, "type": "depository", "balance": 10000,
    })
    assert response.status_code == 200, response.text
    return response.json()["accountId"]


def account_names(client):
    response = client.get("/api/workspace?workspace=personal")
    assert response.status_code == 200, response.text
    return {account["name"] for account in response.json()["accounts"]}


def new_budget(client, name):
    response = client.post("/api/budget/new", json={"name": name})
    assert response.status_code == 200, response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    return response.json()["id"]


def activate(client, budget_id):
    response = client.post(f"/api/budgets/{budget_id}/activate", json={})
    assert response.status_code == 200, response.text


def test_two_budgets_isolate_ledger_plugin_selection_and_legacy_state(tmp_path):
    app = create_app(settings=settings(tmp_path, ("bank-logos",)))
    with TestClient(app, base_url=BASE) as client:
        legacy = add_account(client, "Original wallet")
        first = new_budget(client, "Household")
        assert first != "default" and account_names(client) == set()
        add_account(client, "Household wallet")
        response = client.put("/api/budget/plugins/bank-logos", json={
            "settings": {"appearance": "compact"}, "state": {"customMappings": ["one"]},
        })
        assert response.status_code == 200
        disabled = client.put("/api/plugins/management", json={"plugins": []})
        assert disabled.status_code == 200 and disabled.json()["restart_required"] is True
        saved = client.get("/api/budget/plugins/bank-logos").json()
        assert saved["enabled"] is False and saved["state"] == {"customMappings": ["one"]}

        second = new_budget(client, "Travel")
        assert second != first and account_names(client) == set()
        assert client.get("/api/budget/plugins/bank-logos").json()["enabled"] is True
        add_account(client, "Travel wallet")
        activate(client, first)
        assert account_names(client) == {"Household wallet"}
        assert client.get("/api/budget/plugins/bank-logos").json() == saved
        activate(client, second)
        assert account_names(client) == {"Travel wallet"}
        activate(client, "default")
        assert account_names(client) == {"Original wallet"}
        assert app.state.store.accounts("personal")[0]["id"] == legacy
        budgets = client.get("/api/budgets").json()["budgets"]
        assert len(budgets) == 3 and next(row for row in budgets if row["active"])["id"] == "default"


def test_export_upload_restores_ledger_plugin_data_and_disabled_selection(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path / "source", ("bank-logos",))), base_url=BASE) as client:
        budget_id = new_budget(client, "Portable household")
        account = add_account(client, "Portable wallet")
        assert client.put("/api/budget/plugins/bank-logos", json={
            "settings": {"display": "small"}, "state": {"mapping": {"fixture": "logo"}},
        }).status_code == 200
        assert client.put("/api/plugins/management", json={"plugins": []}).status_code == 200
        response = client.get("/api/budget/export")
        assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
        raw = response.content
    with TestClient(create_app(settings=settings(tmp_path / "destination")), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=raw, headers=ZIP)
        assert response.status_code == 200 and response.json()["id"] == budget_id
        assert account_names(client) == {"Portable wallet"}
        data = client.get("/api/workspace?workspace=personal").json()
        assert data["accounts"][0]["id"] == account
        record = client.get("/api/budget/plugins/bank-logos").json()
        assert record["enabled"] is False
        assert record["settings"] == {"display": "small"}
        assert record["state"] == {"mapping": {"fixture": "logo"}}
        assert "bank-logos" not in client.get("/api/plugins/management").json()["active"]
        assert client.post("/api/budget/open", content=raw, headers=ZIP).status_code == 200


def test_open_budget_rejects_other_revision_without_changing_active_budget(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        budget_id = new_budget(client, "Household")
        add_account(client, "Cash")
        manifest, ledger = decode_bundle(client.get("/api/budget/export").content)
        ledger["accounts"][0]["balance"] += 100
        response = client.post("/api/budget/open", content=encode_bundle(manifest, ledger), headers=ZIP)
        assert response.status_code == 400 and "different revision" in response.text
        assert client.get("/api/budget").json()["id"] == budget_id
        assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["balance"] == 10000


def test_conflicting_snapshot_can_be_opened_as_explicit_separate_budget(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        original = new_budget(client, "Household")
        add_account(client, "Cash")
        manifest, ledger = decode_bundle(client.get("/api/budget/export").content)
        ledger["accounts"][0]["balance"] = 9000
        incoming = encode_bundle(manifest, ledger)
        rejected = client.post("/api/budget/open", content=incoming, headers=ZIP)
        assert rejected.status_code == 400 and "separate budget" in rejected.text
        copied = client.post("/api/budget/open?as_copy=true", content=incoming, headers=ZIP)
        assert copied.status_code == 200, copied.text
        separate = copied.json()["id"]
        assert separate != original
        assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["balance"] == 9000
        copied_manifest, copied_ledger = decode_bundle(client.get("/api/budget/export").content)
        assert copied_manifest == {**manifest, "id": separate} and copied_ledger == ledger
        activate(client, original)
        assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["balance"] == 10000


def test_missing_optional_plugin_is_visible_and_never_loaded(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path / "source")), base_url=BASE) as client:
        new_budget(client, "Future setup")
        manifest, ledger = decode_bundle(client.get("/api/budget/export").content)
    manifest["plugins"].append({
        "id": "missing-fixture-plugin", "enabled": True, "apiVersion": 1,
        "settings": {"feature": "saved"}, "state": {"history": [1, 2]},
    })
    with TestClient(create_app(settings=settings(tmp_path / "destination")), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=encode_bundle(manifest, ledger), headers=ZIP)
        assert response.status_code == 200, response.text
        assert client.get("/api/budget").json()["missingPlugins"] == ["missing-fixture-plugin"]
        catalog = client.get("/api/plugins/management").json()["plugins"]
        missing = next(row for row in catalog if row["id"] == "missing-fixture-plugin")
        assert missing["enabled"] is True and missing["active"] is False
        assert "Not installed" in missing["reason"]
        assert client.get("/api/budget/plugins/missing-fixture-plugin").json()["state"] == {"history": [1, 2]}
        configured = client.put("/api/plugins/management", json={"plugins": ["missing-fixture-plugin", "bank-logos"]})
        assert configured.status_code == 200
        assert set(configured.json()["configured"]) == {"missing-fixture-plugin", "bank-logos"}
        assert client.put("/api/plugins/management", json={"plugins": ["unrequested-missing"]}).status_code == 400
        assert client.put("/api/plugins/management", json={"plugins": []}).status_code == 200
        assert client.get("/api/budget/plugins/missing-fixture-plugin").json()["state"] == {"history": [1, 2]}


def test_open_upload_guards_preserve_current_budget(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        assert client.post("/api/budget/open", content=b"not zip").status_code == 415
        assert client.post("/api/budget/open", content=b"not zip", headers=ZIP).status_code == 400
        assert client.post("/api/budget/open", content=b"", headers=ZIP).status_code == 400
        assert client.post("/api/budget/open", content=b"x", headers={
            **ZIP, "content-length": str(50 * 1024 * 1024 + 1),
        }).status_code == 413
        assert client.post("/api/budget/open", content=b"x", headers={
            **ZIP, "content-length": "invalid",
        }).status_code == 400
        assert client.post("/api/budget/open", content=b"x", headers={
            **ZIP, "origin": "https://attacker.example",
        }).status_code == 403
        assert client.get("/api/budget").json()["id"] == "default"
        assert client.post("/api/budgets/unopened/activate", json={}).status_code == 404


def test_shutdown_closes_all_owned_budget_backends(tmp_path):
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    app = create_app(settings=configuration, budget_host=host)
    with TestClient(app, base_url=BASE) as client:
        new_budget(client, "First")
        new_budget(client, "Second")
        stores = [entry[0].state.store for entry in host.applications.values()]
        assert len(stores) == 3 and len(host.contexts) == 2
        assert all(store.accounts("personal") == [] for store in stores)
    assert host.contexts == []
    for store in stores:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            store.db.execute("SELECT 1")


def test_failed_interface_startup_closes_backend_without_activating_budget(tmp_path, monkeypatch):
    import whab.app as application

    monkeypatch.delenv("SERVER_TOKEN", raising=False)
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    app = create_app(settings=configuration, budget_host=host)
    opened = []
    original = application.open_storage

    def record_storage(*args, **kwargs):
        store = original(*args, **kwargs)
        opened.append(store)
        return store

    with TestClient(app, base_url=BASE) as client:
        manifest, ledger = decode_bundle(client.get("/api/budget/export").content)
        manifest["plugins"].append({
            "id": "server", "enabled": True, "apiVersion": 1, "settings": {}, "state": {},
        })
        monkeypatch.setattr(application, "open_storage", record_storage)
        response = client.post("/api/budget/open", content=encode_bundle(manifest, ledger), headers=ZIP)
        assert response.status_code == 400 and "Interface plugin could not start" in response.text
        assert client.get("/api/budget").json()["id"] == "default"
        assert len(host.applications) == 1 and host.contexts == []
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].db.execute("SELECT 1")


def test_recent_budget_is_listed_and_lazily_reopened_after_restart(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        budget_id = new_budget(client, "Remembered")
        add_account(client, "Saved wallet")
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        assert set(host.applications) == {"default"}
        recent = client.get("/api/budgets").json()["budgets"]
        assert any(row["id"] == budget_id and row["name"] == "Remembered" for row in recent)
        activate(client, budget_id)
        assert account_names(client) == {"Saved wallet"}
        assert set(host.applications) == {"default", budget_id}


def test_missing_recent_file_surfaces_error_without_substituting_budget(tmp_path):
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        budget_id = new_budget(client, "Remembered")
        path = host.applications[budget_id][1].path
    path.unlink()
    with TestClient(create_app(settings=configuration), base_url=BASE) as client:
        response = client.post(f"/api/budgets/{budget_id}/activate", json={})
        assert response.status_code == 400 and "could not be opened" in response.text
        assert client.get("/api/budget").json()["id"] == "default"


def test_concurrent_uploads_only_start_one_budget_runtime(tmp_path):
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        raw = client.get("/api/budget/export").content

        def upload(_):
            return client.post("/api/budget/open", content=raw, headers=ZIP)

        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(upload, range(2)))
        assert all(response.status_code == 200 for response in responses)
        assert len({response.json()["id"] for response in responses}) == 1
        assert len(host.contexts) == 1 and len(host.applications) == 2


def test_remote_path_dispatch_authenticates_loaded_budget_and_keeps_local_guards(tmp_path, monkeypatch):
    token = "synthetic-instance-token-" + "r" * 32
    monkeypatch.setenv("SERVER_TOKEN", token)
    with TestClient(create_app(settings=settings(tmp_path, ("server",))), base_url=BASE) as client:
        budget_id = new_budget(client, "Remote household")
        add_account(client, "Remote wallet")
        activate(client, "default")
        url = f"https://public.example/remote/v1/budgets/{budget_id}/queries/accounts?workspace=personal"
        headers = {"Authorization": "Bearer " + token}
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert {row["name"] for row in response.json()["data"]} == {"Remote wallet"}
        assert client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert client.get("https://public.example/api/workspace?workspace=personal", headers=headers).status_code == 403
        assert client.get(url.replace(budget_id, "unknown"), headers=headers).status_code == 404


def test_saved_selection_cookie_reopens_budget_after_restart(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        budget_id = new_budget(client, "Remembered")
        add_account(client, "Saved wallet")
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        client.cookies.set("whab-budget", budget_id)
        assert client.get("https://public.example/api/workspace?workspace=personal").status_code == 403
        assert set(host.applications) == {"default"}
        assert account_names(client) == {"Saved wallet"}
        assert set(host.applications) == {"default", budget_id}


def test_unknown_selection_requires_explicit_recovery(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        add_account(client, "Startup wallet")
        client.cookies.set("whab-budget", "unknown-budget")
        response = client.get("/api/workspace?workspace=personal")
        assert response.status_code == 409 and "Startup wallet" not in response.text
        response = client.get("/")
        assert response.status_code == 409 and "/budget-recovery" in response.text
        assert client.get("/api/budgets").status_code == 200
        response = client.get("/budget-recovery", follow_redirects=False)
        assert response.status_code == 303 and "Max-Age=0" in response.headers["set-cookie"]
        # Remove the test's domainless cookie, as browsers do when applying deletion.
        client.cookies.clear()
        assert account_names(client) == {"Startup wallet"}


@pytest.mark.parametrize("raw", [b"not json", b"{}", b'[{"id":"invalid","name":"bad","path":"/tmp/bad"}]'])
def test_corrupt_recent_index_fails_before_opening_storage(tmp_path, raw):
    path = tmp_path / "budgets" / "index-sandbox.json"
    path.parent.mkdir()
    path.write_bytes(raw)
    with pytest.raises(StoreError, match="recent budget list is unreadable"):
        create_app(settings=settings(tmp_path))
    assert not (tmp_path / "data").exists()
