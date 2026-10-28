"""View defaults persist without changing ledger money or account currencies."""

import json

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.domain.errors import StoreError
from whab.services.categorization import local_context
from whab.web.ui_preferences import PreferencesBody, UIPreferences


def test_preferences_validate_saved_values_and_isolate_workspace_environment_and_budget(tmp_path):
    def preferences(budget="one", environment="sandbox"):
        return UIPreferences(lambda **_: local_context(tmp_path / environment, budget, "web"))

    first = preferences()
    assert first.read("personal") == {"defaultCurrency": "USD"}
    first.write(PreferencesBody(workspace="personal", defaultCurrency="JPY"))
    assert preferences().read("personal") == {"defaultCurrency": "JPY"}
    assert first.read("demo") == preferences("two").read("personal") == preferences(environment="production").read("personal") == {"defaultCurrency": "USD"}
    context = local_context(tmp_path / "sandbox", "one", "web")
    for raw in (b"not json", b'[]', b'{"defaultCurrency":"BOGUS"}', b'{"defaultCurrency":"USD","unknown":1}'):
        context.write_file("view-preferences-personal.json", raw)
        with pytest.raises(StoreError, match="could not be read"):
            first.read("personal")
    assert UIPreferences(lambda: None).read("personal") == {"defaultCurrency": "USD"}


def test_currency_preference_is_portable_and_preserves_ledger(tmp_path):
    session = BudgetSession.create(tmp_path, "sandbox", "Fictional budget", plugins=("web",))
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    with TestClient(create_app(settings=settings, budget_session=session), base_url="https://127.0.0.1:5173") as client:
        assert client.get("/api/ui/preferences?workspace=personal").json() == {"defaultCurrency": "USD"}
        before = client.get("/api/workspace?workspace=personal&month=2026-10").json()
        assert client.put("/api/ui/preferences", json={"workspace": "personal", "defaultCurrency": "JPY"}).json() == {"defaultCurrency": "JPY"}
        after = client.get("/api/workspace?workspace=personal&month=2026-10").json()
        assert before == after
        landing = client.get("/", follow_redirects=False)
        assert landing.headers["location"].endswith("/jpy")
        assert client.get("/api/ui/preferences?workspace=demo").json() == {"defaultCurrency": "USD"}
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
        assert client.get("/api/ui/preferences?workspace=personal").json() == {"defaultCurrency": "USD"}
        assert session.plugin_record("web") is None
        assert client.put("/api/ui/preferences", json={"workspace": "personal", "defaultCurrency": "EUR"}).status_code == 200
        assert session.plugin_record("web")["enabled"] is True
        assert client.get("/api/ui/preferences?workspace=personal").json() == {"defaultCurrency": "EUR"}
