"""Plaid discovery, HTTP compatibility and plugin-owned account resources."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from whab_plaid.plugin import plugin as plaid_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import Plugin, PluginRegistry, UserInterface


def settings(plugins=(), *, home=Path("unused-test-home"), ui=""):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", plugins, ui=ui)


@pytest.fixture
def client(store, tmp_path):
    with TestClient(
        create_app(store=store, settings=settings(home=tmp_path), plugins=PluginRegistry((plaid_plugin,))),
        base_url="https://127.0.0.1:5173",
    ) as client:
        yield client


def test_missing_plaid_config_is_actionable_and_bad_webhook_is_rejected(client):
    response = client.post("/api/plaid/link-token", json={})
    assert response.status_code == 400
    assert "config" in response.json()["error"].lower()
    response = client.post(
        "/api/plaid/webhook", json={"webhook_type": "TRANSACTIONS", "item_id": "fake"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize("prefix", ["/api/plaid", "/api/banks/plaid"])
def test_webhook_host_exception_never_allows_plaintext_transport(client, prefix):
    response = client.post(
        "http://127.0.0.1:5173" + prefix + "/webhook",
        json={"item_id": "synthetic"},
        headers={"Host": "hooks.example", "X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 400 and response.json() == {"error": "HTTPS is required"}
    # HTTPS still requires an authentic provider signature, even with a public Host.
    assert client.post(prefix + "/webhook", json={}, headers={"Host": "hooks.example"}).status_code == 401


def test_bundled_plaid_is_loaded_through_entry_point_and_keeps_legacy_routes(tmp_path):
    with TestClient(
        create_app(settings=settings(("plaid",), home=tmp_path, ui="web")), base_url="https://127.0.0.1:5173"
    ) as client:
        descriptors = client.get("/api/plugins").json()["plugins"]
        assert descriptors[0]["id"] == "plaid"
        assert descriptors[0]["has_bank"] is True
        for prefix in ("/api/plaid", "/api/banks/plaid"):
            assert client.get(prefix + "/items").json() == {"items": []}
            assert client.post(prefix + "/link-token", json={}).status_code == 400
            assert client.post(prefix + "/webhook", json={}).status_code == 401
        page = client.get("/budgets/home/accounts/current-month/usd")
        assert "Finish Plaid setup" in page.text
        assert "/plugins/plaid/static/connect.js" in page.text
        assert client.get("/plugins/plaid/static/connect.js").status_code == 200


def test_alternate_ui_can_embed_plaid_without_default_ui_helpers(tmp_path, store):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_text(
        "{% for bank in bank_panels %}"
        "{% with status=bank.status, items=bank['items'] %}{% include bank.template %}{% endwith %}"
        "{% endfor %}"
        '{% for path in plugin_scripts %}<script type="module" src="{{ path }}"></script>{% endfor %}'
    )
    ui = UserInterface(templates, static, lambda *args, **kwargs: {})
    registry = PluginRegistry((Plugin("alternate", "Alternate UI", ui=ui), plaid_plugin))
    with TestClient(
        create_app(store=store, settings=settings(home=tmp_path, ui="alternate"), plugins=registry),
        base_url="https://127.0.0.1:5173",
    ) as client:
        response = client.get("/budgets/home/accounts/current-month/usd")
        assert response.status_code == 200
        assert "Finish Plaid setup" in response.text
        assert "/plugins/plaid/static/connect.js" in response.text
        assert client.get("/plugins/plaid/static/connect.js").status_code == 200
        assert client.get("/static/client.js").status_code == 404


def test_demo_pages_do_not_load_personal_bank_controls_or_connection_scripts(tmp_path):
    with TestClient(
        create_app(settings=settings(("plaid",), home=tmp_path, ui="web")), base_url="https://127.0.0.1:5173"
    ) as client:
        response = client.get("/budgets/demo/accounts/current-month/usd")
        assert response.status_code == 200
        assert "Finish Plaid setup" not in response.text
        assert "data-plaid-connect" not in response.text
        assert "/plugins/plaid/static/connect.js" not in response.text


def test_oauth_callback_uses_provider_asset_with_an_alternate_visual_interface(tmp_path, store):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_text("Alternate visual interface")
    alternate = Plugin("alternate", "Alternate UI", ui=UserInterface(templates, static, lambda *a, **k: {}))
    registry = PluginRegistry((alternate, plaid_plugin))
    with TestClient(
        create_app(store=store, settings=settings(home=tmp_path, ui="alternate"), plugins=registry),
        base_url="https://127.0.0.1:5173",
    ) as client:
        state = "synthetic-private-state"
        response = client.get("/", params={"oauth_state_id": state})
        assert response.status_code == 200
        assert '/budgets/home/plugins/plaid/static/oauth.js' in response.text
        assert state not in response.text
        assert client.get('/budgets/home/plugins/plaid/static/oauth.js').status_code == 200
        assert client.get('/budgets/home/static/oauth.js').status_code == 404
