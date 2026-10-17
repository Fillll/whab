"""Budget addresses select their own dataset and runtime independently of cookies."""

import json
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.budgets.helpers import decode_budget
from whab.app import create_app
from whab.budgets.container import decode_container
from whab.budgets.host import BudgetHost
from whab.config import Settings

BASE = "https://127.0.0.1:5173"


def settings(home, plugins=()):
    return Settings(
        home=home, port=5173, plaid_env="sandbox", plaid_client_id="", plaid_secret="",
        encryption_key="", allow_live=False, webhook_url="", redirect_uri="",
        plugins=plugins, ui="web",
    )


def scoped(key, operation):
    return f"/budgets/{key}/api/{operation}"


def create_budget(client, name):
    response = client.post(scoped("home", "budget/new"), json={"name": name})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["url"] == f'/budgets/{result["id"]}/budget/current-month/usd'
    return result


def add_account(client, key, name, workspace="personal"):
    return client.post(scoped(key, "accounts"), json={
        "workspace": workspace, "name": name, "type": "depository", "balance": 10000,
    })


def accounts(client, key, workspace="personal"):
    response = client.get(scoped(key, "workspace"), params={"workspace": workspace})
    assert response.status_code == 200, response.text
    return {row["name"] for row in response.json()["accounts"]}


def page_context(response):
    assert response.status_code == 200, response.text
    return json.loads(re.search(
        r'<script id="whab-context" type="application/json">(.*?)</script>', response.text,
    )[1])


def test_root_selects_demo_or_cookie_budget_and_rejects_old_view_parameters(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 303
        assert root.headers["location"] == "/budgets/demo/budget/current-month/usd"
        created = create_budget(client, "Household")
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 303 and root.headers["location"] == created["url"]
        for name, value in (("page", "budget"), ("workspace", "demo"),
                            ("month", "2026-10"), ("currency", "USD")):
            assert client.get("/", params={name: value}).status_code == 400


@pytest.mark.parametrize("path", [
    "/budgets/Home/budget/current-month/usd",
    "/budgets/home/Budget/current-month/usd",
    "/budgets/home/budget/Current-Month/usd",
    "/budgets/home/budget/current-month/USD",
    "/budgets/missing/budget/current-month/usd",
    "/budgets/home/settings/current-month/usd",
])
def test_noncanonical_or_unknown_page_paths_are_rejected(tmp_path, path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        assert client.get(path).status_code == 404


def test_explicit_paths_isolate_reads_writes_plugins_exports_and_page_assets(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path, ("bank-logos",))), base_url=BASE) as client:
        first = create_budget(client, "Household")
        second = create_budget(client, "Travel")
        a, b = first["id"], second["id"]
        # The cookie now selects Travel; a tab showing Household still owns its writes.
        assert add_account(client, a, "Household wallet").status_code == 200
        assert add_account(client, b, "Travel wallet").status_code == 200
        assert add_account(client, "home", "Original wallet").status_code == 200
        assert accounts(client, a) == {"Household wallet"}
        assert accounts(client, b) == {"Travel wallet"}
        assert accounts(client, "home") == {"Original wallet"}
        assert client.put(scoped(a, "budget/plugins/bank-logos"), json={
            "state": {"fixture": "Household state"},
        }).status_code == 200
        assert client.get(scoped(a, "budget/plugins/bank-logos")).json()["state"] == {
            "fixture": "Household state",
        }
        assert client.get(scoped(b, "budget/plugins/bank-logos")).json()["state"] != {
            "fixture": "Household state",
        }
        exported = client.get(scoped(a, "budget/export"))
        assert exported.status_code == 200
        assert decode_container(exported.content).manifest["name"] == "Household"
        response = client.get(first["url"])
        context = page_context(response)
        assert context["apiPrefix"] == f"/budgets/{a}/api"
        assert context["budgetUrl"] == f"/budgets/{a}"
        assert "Household wallet" in response.text and "Travel wallet" not in response.text
        assert f'/budgets/{a}/static/app.js' in response.text
        assert client.get(f"/budgets/{a}/static/app.js").status_code == 200
        activated = client.post(scoped(a, f"budgets/{b}/activate"), json={})
        assert activated.status_code == 200 and activated.json()["url"] == second["url"]
        assert accounts(client, a) == {"Household wallet"}


@pytest.mark.parametrize("key,workspace", [("home", "demo"), ("demo", "personal")])
def test_route_dataset_rejects_conflicting_query_and_body_workspace(tmp_path, key, workspace):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        before = client.get("/api/workspace?workspace=personal").json()
        assert client.get(scoped(key, "workspace"), params={"workspace": workspace}).status_code == 400
        response = add_account(client, key, "Wrong dataset", workspace)
        assert response.status_code == 400
        assert client.get("/api/workspace?workspace=personal").json() == before


def test_demo_is_global_and_cannot_use_personal_only_operations(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path, ("plaid", "ynab"))), base_url=BASE) as client:
        original_demo = accounts(client, "demo", "demo")
        assert original_demo
        created = create_budget(client, "Household")
        assert add_account(client, created["id"], "Private fixture wallet").status_code == 200
        assert accounts(client, "demo", "demo") == original_demo
        response = client.get("/budgets/demo/accounts/current-month/usd")
        assert "Private fixture wallet" not in response.text and "Sample data" in response.text
        assert add_account(client, "demo", "Sample fixture wallet", "demo").status_code == 200
        assert "Sample fixture wallet" in accounts(client, "demo", "demo")
        assert accounts(client, created["id"]) == {"Private fixture wallet"}
        for operation in ("banks/plaid/link-token", "budget/save", "budget/copies"):
            assert client.post(scoped("demo", operation), json={}).status_code == 403
        assert client.put(scoped("demo", "plugins/management"), json={"plugins": []}).status_code == 403
        assert client.post(scoped("demo", "plugins/ynab/import/preview"), content=b"fixture", headers={
            "Content-Type": "application/zip", "X-Import-Currency": "USD",
        }).status_code == 403


def test_current_month_alias_rolls_forward_and_navigation_preserves_its_meaning(tmp_path, monkeypatch):
    monkeypatch.setattr("whab.web.routes.pages.current_month", lambda: "2026-10")
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        address = "/budgets/demo/budget/current-month/eur"
        october = client.get(address)
        context = page_context(october)
        assert context["month"] == "2026-10" and context["monthToken"] == "current-month"
        assert context["currency"] == "EUR"
        assert '/budgets/demo/transactions/current-month/eur' in october.text
        monkeypatch.setattr("whab.web.routes.pages.current_month", lambda: "2026-11")
        november = page_context(client.get(address))
        assert november["month"] == "2026-11" and november["monthToken"] == "current-month"
        fixed = client.get("/budgets/demo/budget/2026-10/eur")
        assert page_context(fixed)["month"] == "2026-10"
        assert 'href="/budgets/demo/budget/current-month/eur">This month</a>' in fixed.text
        assert 'href="/budgets/demo/budget/2026-11/eur"' in fixed.text


def test_saved_budget_path_reopens_its_runtime_despite_unknown_cookie(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        created = create_budget(client, "Remembered household")
        assert add_account(client, created["id"], "Saved fixture wallet").status_code == 200
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        client.cookies.set("whab-budget", "unknown-budget")
        response = client.get(created["url"])
        assert response.status_code == 200 and "Saved fixture wallet" in response.text
        assert created["id"] in host.applications
        assert accounts(client, "home") == set()


@pytest.mark.parametrize("url,headers,status", [
    ("http://127.0.0.1:5173", {"X-Forwarded-Proto": "https"}, 400),
    ("https://untrusted.invalid", {}, 403),
    (BASE, {"Origin": "https://untrusted.invalid"}, 403),
    (BASE, {"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_invalid_transport_or_origin_cannot_activate_scoped_saved_budget(tmp_path, monkeypatch, url, headers, status):
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    key = str(uuid4())
    host.recent[key] = {"id": key, "name": "Fixture", "path": str(tmp_path / "fixture.whab")}

    async def forbidden(*args, **kwargs):
        pytest.fail("Rejected requests must not activate a budget runtime")

    monkeypatch.setattr(host, "activate", forbidden)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        assert client.get(url + f"/budgets/{key}/budget/current-month/usd", headers=headers).status_code == status
        assert set(host.applications) == {"default"}


def test_scoped_api_preserves_json_and_upload_limits_and_denies_remote_namespaces(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path, ("ynab",))), base_url=BASE) as client:
        path = scoped("home", "accounts")
        assert client.post(path, content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
        assert client.post(path, content=b"{}", headers={
            "Content-Type": "application/json", "Content-Length": str(32 * 1024 + 1),
        }).status_code == 413
        response = client.post(path, content=iter([b" " * 20000, b" " * 20000]), headers={
            "Content-Type": "application/json",
        })
        assert response.status_code == 413
        # An invalid ZIP larger than the JSON limit must reach ZIP parsing, not the JSON limit.
        assert client.post(scoped("home", "budget/open"), content=b" " * 40000, headers={
            "Content-Type": "application/zip",
        }).status_code == 400
        assert client.post(scoped("home", "plugins/ynab/import/preview"), content=b"fixture", headers={
            "Content-Type": "application/zip", "X-Import-Currency": "USD",
            "Content-Length": str(10 * 1024 * 1024 + 1),
        }).status_code == 413
        assert client.get("/budgets/home/remote/v1/budgets/default/queries/accounts?workspace=personal").status_code == 404
        assert client.post(scoped("home", "banks/plaid/webhook"), json={}, headers={
            "Host": "untrusted.invalid",
        }).status_code == 403


def test_lowercase_filter_encoding_preserves_case_sensitive_identifiers():
    from urllib.parse import parse_qs, urlsplit

    from whab.web.navigation import page_url

    category = "Mixed CASE/£"
    address = page_url("home", "transactions", "2026-10", "USD", category=category)
    assert address == address.lower()
    assert urlsplit(address).path == "/budgets/home/transactions/2026-10/usd"
    assert parse_qs(urlsplit(address).query) == {"category": [category]}


@pytest.mark.parametrize("unknown_cookie", [False, True])
def test_oauth_callback_bridge_ignores_other_tab_cookie_and_omits_callback_state(tmp_path, unknown_cookie):
    configuration = settings(tmp_path, ("plaid",))
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        created = create_budget(client, "Other tab budget")
        if unknown_cookie:
            client.cookies.clear()
            client.cookies.set("whab-budget", "unknown-budget")
        callback_state = "synthetic-private-callback-state"
        response = client.get("/", params={"oauth_state_id": callback_state}, follow_redirects=False)
        assert response.status_code == 200
        assert '/budgets/home/plugins/plaid/static/oauth.js' in response.text
        assert created["id"] not in response.text
        assert callback_state not in response.text
        assert response.headers["cache-control"] == "no-store"


def test_oauth_callback_without_enabled_bank_provider_is_unavailable(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        client.cookies.set("whab-budget", "unknown-budget")
        response = client.get("/?oauth_state_id=synthetic-state")
        assert response.status_code == 400
        assert "synthetic-state" not in response.text


def test_oauth_bridge_can_use_enabled_provider_from_an_already_open_budget(tmp_path, store):
    from whab.budgets.session import BudgetSession

    configuration = settings(tmp_path / "runtime")
    host = BudgetHost(configuration)
    portable = BudgetSession.create(tmp_path / "seed", "sandbox", "Private portable fixture", plugins=("plaid",))
    portable.store = store
    raw = portable.bundle()
    app = create_app(settings=configuration, budget_host=host)
    with TestClient(app, base_url=BASE) as client:
        assert app.state.plugins.bank_plugins() == ()
        opened = client.post("/api/budget/open", content=raw, headers={"Content-Type": "application/zip"})
        assert opened.status_code == 200
        key = opened.json()["id"]
        client.cookies.clear()
        client.cookies.set("whab-budget", "unknown-budget")
        response = client.get("/?oauth_state_id=synthetic-private-state")
        assert response.status_code == 200
        assert f'/budgets/{key}/plugins/plaid/static/oauth.js' in response.text
        assert "Private portable fixture" not in response.text
        assert "synthetic-private-state" not in response.text
        assert set(host.applications) == {"default", key}


def test_explicit_startup_budget_landing_uses_its_stable_key_and_keeps_demo_available(tmp_path):
    from whab.budgets.session import BudgetSession

    startup = BudgetSession.create(tmp_path, "sandbox", "Startup household")
    app = create_app(settings=settings(tmp_path), budget_session=startup)
    with TestClient(app, base_url=BASE) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/budgets/{startup.id}/budget/current-month/usd"
        assert client.get("/budgets/home/budget/current-month/usd").status_code == 404
        demo = client.get("/budgets/demo/budget/current-month/usd")
        assert demo.status_code == 200
        assert "Sample data" in demo.text and "Demo budget" in demo.text
        breadcrumb = re.search(r'<div class="breadcrumb">(.*?)</div>', demo.text, re.S)[1]
        assert "Startup household" not in breadcrumb
        assert f'href="/budgets/{startup.id}/budget/current-month/usd"' in demo.text


@pytest.mark.parametrize("target_key", ["home", "portable"])
def test_catalog_download_address_owns_target_and_ignores_other_tab_cookie(tmp_path, target_key):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        assert add_account(client, "home", "Original wallet").status_code == 200
        first = create_budget(client, "Household")
        assert add_account(client, first["id"], "Household wallet").status_code == 200
        second = create_budget(client, "Other tab")
        assert add_account(client, second["id"], "Other tab wallet").status_code == 200
        key = "home" if target_key == "home" else first["id"]
        identifier = "default" if target_key == "home" else first["id"]
        catalog = client.get(scoped(second["id"], "budgets")).json()
        response = client.get(scoped(key, f"budgets/{identifier}/export"))
        assert response.status_code == 200, response.text
        assert "set-cookie" not in response.headers
        manifest, ledger = decode_budget(response.content)
        expected_name = "Original wallet" if key == "home" else "Household wallet"
        assert {account["name"] for account in ledger["accounts"]} == {expected_name}
        assert manifest["name"] == ("My budget" if key == "home" else "Household")
        assert client.get("/api/budget").json()["id"] == second["id"]
        assert client.get(scoped(second["id"], "budgets")).json() == catalog
        mismatched = client.get(scoped(key, f'budgets/{second["id"]}/export'))
        assert mismatched.status_code == 400 and "set-cookie" not in mismatched.headers
        assert client.get("/api/budget").json()["id"] == second["id"]


def test_demo_download_exports_only_global_sample_ledger_without_selecting_it(tmp_path):
    configuration = settings(tmp_path, ("bank-logos",))
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        private = create_budget(client, "Private household")
        identifier = private["id"]
        assert add_account(client, identifier, "Private wallet").status_code == 200
        assert add_account(client, "demo", "Global demo wallet", "demo").status_code == 200
        # A portable runtime's own sample ledger is not the global demo destination.
        assert add_account(client, identifier, "Local sample wallet", "demo").status_code == 400
        assert client.post("/api/accounts", json={
            "workspace": "demo", "name": "Other sample wallet", "type": "depository", "balance": 2000,
        }).status_code == 200
        assert client.put(scoped(identifier, "budget/plugins/bank-logos"), json={
            "state": {"privateMarker": "personal plugin state"},
        }).status_code == 200
        response = client.get(scoped("demo", "budget/export"))
        assert response.status_code == 200, response.text
        assert "set-cookie" not in response.headers
        manifest, ledger = decode_budget(response.content)
        assert manifest["name"] == "Demo budget" and manifest["id"] != identifier
        assert manifest["storage"] == "sqlite"
        assert {plugin["id"] for plugin in manifest["plugins"]} == {"sqlite"}
        assert ledger == host.applications[host.default_id][0].state.store.export_budget("demo")
        names = {account["name"] for account in ledger["accounts"]}
        assert "Global demo wallet" in names
        assert "Private wallet" not in names and "Other sample wallet" not in names
        assert client.get("/api/budget").json()["id"] == identifier
        for query, expected_status in (({"workspace": "personal"}, 400), ({"include_credentials": "true"}, 400)):
            rejected = client.get(scoped("demo", "budget/export"), params=query)
            assert rejected.status_code == expected_status and "set-cookie" not in rejected.headers
        assert client.get(scoped("demo", f"budgets/{identifier}/export")).status_code == 400
