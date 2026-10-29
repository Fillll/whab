"""View defaults persist without changing ledger money or account currencies."""

import json

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.domain.errors import StoreError
from whab.services.categorization import local_context
from whab.web.ui_preferences import PreferencesBody, SidebarAccountBody, UIPreferences


def test_preferences_validate_saved_values_and_isolate_workspace_environment_and_budget(tmp_path):
    def preferences(budget="one", environment="sandbox"):
        return UIPreferences(lambda **_: local_context(tmp_path / environment, budget, "web"))

    first = preferences()
    assert first.read("personal") == {"defaultCurrency": "USD", "hiddenAccountIds": []}
    first.write(PreferencesBody(workspace="personal", defaultCurrency="JPY"))
    assert preferences().read("personal") == {"defaultCurrency": "JPY", "hiddenAccountIds": []}
    assert first.read("demo") == preferences("two").read("personal") == preferences(environment="production").read("personal") == {"defaultCurrency": "USD", "hiddenAccountIds": []}
    context = local_context(tmp_path / "sandbox", "one", "web")
    for raw in (b"not json", b'[]', b'{"defaultCurrency":"BOGUS"}', b'{"defaultCurrency":"USD","unknown":1}'):
        context.write_file("view-preferences-personal.json", raw)
        with pytest.raises(StoreError, match="could not be read"):
            first.read("personal")
    assert UIPreferences(lambda: None).read("personal") == {"defaultCurrency": "USD", "hiddenAccountIds": []}


def test_currency_preference_is_portable_and_preserves_ledger(tmp_path):
    session = BudgetSession.create(tmp_path, "sandbox", "Fictional budget", plugins=("web",))
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    with TestClient(create_app(settings=settings, budget_session=session), base_url="https://127.0.0.1:5173") as client:
        assert client.get("/api/ui/preferences?workspace=personal").json() == {"defaultCurrency": "USD", "hiddenAccountIds": []}
        before = client.get("/api/workspace?workspace=personal&month=2026-10").json()
        assert client.put("/api/ui/preferences", json={"workspace": "personal", "defaultCurrency": "JPY"}).json() == {"defaultCurrency": "JPY", "hiddenAccountIds": []}
        after = client.get("/api/workspace?workspace=personal&month=2026-10").json()
        assert before == after
        landing = client.get("/", follow_redirects=False)
        assert landing.headers["location"].endswith("/jpy")
        assert client.get("/api/ui/preferences?workspace=demo").json() == {"defaultCurrency": "USD", "hiddenAccountIds": []}
        for body in ({"workspace": "personal", "defaultCurrency": "ZZZ"}, {"workspace": "other", "defaultCurrency": "EUR"}, {"workspace": "personal", "defaultCurrency": 123}, {"workspace": "personal", "defaultCurrency": "USD", "extra": True}):
            assert client.put("/api/ui/preferences", json=body).status_code == 400
        raw = session.read_file("web", "view-preferences-personal.json")
        assert json.loads(raw) == {"defaultCurrency": "JPY"}
        # The existing portable archive carries this selected UI's namespace.
        assert session.files_for("web")["view-preferences-personal.json"] == raw


def test_initial_visual_without_manifest_record_can_save_its_preferences(tmp_path):
    session = BudgetSession.create(tmp_path, "sandbox", "Fictional old budget")
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    with TestClient(create_app(settings=settings, budget_session=session), base_url="https://127.0.0.1:5173") as client:
        assert client.get("/api/ui/preferences?workspace=personal").json() == {"defaultCurrency": "USD", "hiddenAccountIds": []}
        assert session.plugin_record("web") is None
        assert client.put("/api/ui/preferences", json={"workspace": "personal", "defaultCurrency": "EUR"}).status_code == 200
        assert session.plugin_record("web")["enabled"] is True
        assert client.get("/api/ui/preferences?workspace=personal").json() == {"defaultCurrency": "EUR", "hiddenAccountIds": []}


def test_sidebar_visibility_is_portable_isolated_and_preserves_currency(tmp_path):
    preferences = UIPreferences(lambda **_: local_context(tmp_path, "one", "web"))
    preferences.write(PreferencesBody(workspace="personal", defaultCurrency="JPY"))
    body = SidebarAccountBody(workspace="personal", visible=False)
    assert preferences.sidebar("fictional-one", body)["hiddenAccountIds"] == ["fictional-one"]
    preferences.sidebar("fictional-two", body)
    # A repeated request and changing the budget currency preserve both choices.
    preferences.sidebar("fictional-one", body)
    preferences.write(PreferencesBody(workspace="personal", defaultCurrency="EUR"))
    assert preferences.read("personal") == {"defaultCurrency": "EUR", "hiddenAccountIds": ["fictional-one", "fictional-two"]}
    assert preferences.read("demo")["hiddenAccountIds"] == []
    assert UIPreferences(lambda **_: local_context(tmp_path, "two", "web")).read("personal")["hiddenAccountIds"] == []
    assert preferences.sidebar("fictional-one", SidebarAccountBody(workspace="personal", visible=True))["hiddenAccountIds"] == ["fictional-two"]
    # Old preference files upgrade when read; malformed hidden identifiers fail closed.
    context = local_context(tmp_path, "one", "web")
    context.write_file("view-preferences-personal.json", b'{"defaultCurrency":"USD"}')
    assert preferences.read("personal")["hiddenAccountIds"] == ["fictional-two"]
    assert json.loads(context.read_file("view-preferences-personal.json")) == {"defaultCurrency": "USD"}
    for raw in (b'[""]', b'[123]', json.dumps(["x" * 201]).encode()):
        context.write_file("sidebar-accounts-personal.json", raw)
        with pytest.raises(StoreError):
            preferences.read("personal")


def test_sidebar_route_retains_ledger_and_accounts_page(tmp_path):
    session = BudgetSession.create(tmp_path, "sandbox", "Fictional budget", plugins=("web",))
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    with TestClient(create_app(settings=settings, budget_session=session), base_url="https://127.0.0.1:5173") as client:
        account = client.post("/api/accounts", json={"workspace": "personal", "name": "Fictional daily", "type": "depository", "balance": 100, "currency": "USD"}).json()["accountId"]
        before = client.get("/api/workspace?workspace=personal&month=2026-10").json()
        route = f"/api/ui/preferences/accounts/{account}"
        response = client.patch(route, json={"workspace": "personal", "visible": False})
        assert response.status_code == 200 and response.json()["hiddenAccountIds"] == [account]
        page_url = client.get("/", follow_redirects=False).headers["location"].replace("/budget/", "/accounts/")
        page_response = client.get(page_url)
        assert page_response.status_code == 200
        page = page_response.text
        sidebar, content = page.split('class="sidebar-accounts"', 1)[1].split('<main', 1)
        assert f'data-account-id="{account}"' not in sidebar
        assert f'data-inline-account="{account}"' in content
        assert client.get("/api/workspace?workspace=personal&month=2026-10").json() == before
        assert client.patch(route, json={"workspace": "personal", "visible": True}).json()["hiddenAccountIds"] == []
        assert f'data-account-id="{account}"' in client.get(page_url).text
        for body in ({"workspace":"personal", "visible":"false"}, {"workspace":"other", "visible":False}, {"workspace":"personal", "visible":False, "extra":1}):
            assert client.patch(route, json=body).status_code == 400
