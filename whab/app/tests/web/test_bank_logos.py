"""Optional branding stays local, deferred and separate from bank credentials."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

from tests.support.bank_logos import LOGO_PATH, PNG
from tests.support.bank_logos import imported_store as imported_store
from tests.support.ui import make_ui
from whab.app import create_app
from whab.config import Settings
from whab.plugins import BankLogos, LogoImage, Plugin, PluginAction, PluginRegistry, PluginSettings


def client_for(store, home, *optional):
    settings = Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui="fixture-ui")
    registry = PluginRegistry((sqlite_plugin, make_ui(home), *optional))
    return TestClient(
        create_app(store=store, settings=settings, plugins=registry),
        base_url="https://127.0.0.1:5173",
    )


def test_logo_route_returns_typed_bytes_with_local_guards_and_server_cache(store, tmp_path):
    fetched = []

    def fetch(key):
        fetched.append(key)
        return LogoImage(PNG, "image/png")

    plugin = Plugin("logos", "Fictional bank logos", bank_logos=BankLogos(lambda _: "chase", fetch))
    with client_for(store, tmp_path, plugin) as client:
        for headers in (
            {"host": "foreign.example"},
            {"origin": "https://foreign.example"},
            {"sec-fetch-site": "cross-site"},
        ):
            assert client.get(LOGO_PATH, headers=headers).status_code == 403
        assert fetched == []
        for _ in range(2):
            response = client.get(LOGO_PATH)
            assert response.status_code == 200 and response.content == PNG
            assert response.headers["content-type"] == "image/png"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["cache-control"] == "no-store"
        assert fetched == ["chase"]
        assert client.get("/api/plugins/disabled/bank-logos/chase").status_code == 404
        assert client.get("/api/plugins/logos/bank-logos/not!allowed").status_code == 404
        assert fetched == ["chase"]


def test_http_cache_flush_keeps_account_settings_and_retrieves_the_same_logo_again(store, tmp_path):
    fetched, actions = [], []
    plugin = Plugin(
        "logos", "Fictional bank logos",
        bank_logos=BankLogos(
            lambda _: None, lambda key: fetched.append(key) or LogoImage(PNG, "image/png"),
            resolve_domain=lambda _: "saved-website",
        ),
        settings=PluginSettings(
            lambda: (), (PluginAction("flush-cache", "Flush cache"),),
            lambda action: actions.append(action) or "Bank logo cache cleared.",
        ),
    )
    with client_for(store, tmp_path, plugin) as client:
        account_id = client.post("/api/accounts", json={
            "workspace": "personal", "name": "Fictional savings", "type": "depository",
            "subtype": "savings", "balance": 12300,
        }).json()["accountId"]
        assert client.patch("/api/accounts/" + account_id, json={
            "workspace": "personal", "bankWebsite": "bank.example.org",
        }).status_code == 200
        saved_accounts = store.accounts("personal")
        assert saved_accounts[0]["bankWebsite"] == "https://bank.example.org"
        preview = client.post("/api/plugins/bank-logo-preview", json={"website": "bank.example.org"}).json()
        assert preview["available"]
        assert client.get(preview["logoUrl"]).content == PNG
        assert fetched == ["saved-website"]
        result = client.post("/api/plugins/logos/settings/actions/flush-cache", json={})
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert result.json() == {
            "message": "Bank logo cache cleared.", "settings": {"insights": [], "actions": [{
                "id": "flush-cache", "label": "Flush cache", "description": "",
            }]},
        }
        assert actions == ["flush-cache"]
        assert store.accounts("personal") == saved_accounts
        for _ in range(2):
            assert client.get(preview["logoUrl"]).content == PNG
        assert fetched == ["saved-website", "saved-website"]


@pytest.mark.parametrize("data,media_type", [
    (b"<svg onload='private-fixture-error'></svg>", "image/svg+xml"),
    (b"<html>private-fixture-error</html>", "image/png"),
    (b"\x89PNG\r\n\x1a\n" + b"x" * (128 * 1024), "image/png"),
])
def test_logo_route_revalidates_plugin_image_bounds_and_type(store, tmp_path, data, media_type):
    # A trusted plugin can bypass frozen dataclasses; the host must revalidate output.
    invalid = object.__new__(LogoImage)
    object.__setattr__(invalid, "data", data)
    object.__setattr__(invalid, "media_type", media_type)
    plugin = Plugin(
        "logos", "Invalid bank logos", bank_logos=BankLogos(lambda _: "chase", lambda _: invalid)
    )
    with client_for(store, tmp_path, plugin) as client:
        result = client.get(LOGO_PATH)
        assert result.status_code == 404 and "private-fixture-error" not in result.text
        assert result.headers["x-content-type-options"] == "nosniff"
        assert client.get("/budgets/demo/budget/current-month/usd").status_code == 200


@pytest.mark.parametrize("failure", ["unknown", "exception"])
def test_unavailable_fetch_is_cached_without_breaking_budget(imported_store, tmp_path, failure):
    fetched = []

    def fetch(key):
        fetched.append(key)
        if failure == "exception":
            raise RuntimeError("PRIVATE_FIXTURE_PROVIDER_ERROR")
        return None

    plugin = Plugin("logos", "Unavailable logos", bank_logos=BankLogos(lambda _: "chase", fetch))
    with client_for(imported_store, tmp_path, plugin) as client:
        for _ in range(2):
            result = client.get(LOGO_PATH)
            assert result.status_code == 404 and "PRIVATE_FIXTURE_PROVIDER_ERROR" not in result.text
        assert fetched == ["chase"]
        assert client.get("/budgets/home/budget/2026-10/usd").status_code == 200


def test_saved_logo_selection_waits_for_restart(imported_store, tmp_path, monkeypatch):
    calls = []
    plugin = Plugin(
        "logos", "Pending logos",
        bank_logos=BankLogos(lambda name: calls.append(name) or "chase", lambda _: calls.append("fetch")),
    )
    entrypoint = SimpleNamespace(name="logos", group="whab.plugins", dist=None, load=lambda: plugin)
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (entrypoint,))
    with client_for(imported_store, tmp_path) as client:
        before = client.get("/api/plugins").json()
        saved = client.put("/api/plugins/management", json={"plugins": ["logos"]})
        assert saved.status_code == 200
        assert saved.json()["configured"] == ["logos"] and saved.json()["restart_required"]
        assert client.get("/api/plugins").json() == before
        assert client.get(LOGO_PATH).status_code == 404
        page = client.get("/budgets/home/accounts/2026-10/usd")
        assert page.status_code == 200 and "data-bank-logo" not in page.text
        assert calls == [] and (tmp_path / "plugins.json").is_file()


def test_optional_website_preview_is_guarded_cached_budget_scoped_and_does_not_save(store, tmp_path):
    seen, fetched = [], []
    plugin = Plugin("logos", "Fictional bank logos", bank_logos=BankLogos(
        lambda _: None, lambda key: fetched.append(key) or LogoImage(PNG, "image/png"),
        resolve_domain=lambda hostname: seen.append(hostname) or "manual-domain",
        provider_name="Synthetic artwork", provider_url="https://icons.example.org",
    ))
    with client_for(store, tmp_path, plugin) as client:
        endpoint = "/budgets/demo/api/plugins/bank-logo-preview"
        for headers in (
            {"host": "foreign.example"}, {"origin": "https://foreign.example"},
            {"sec-fetch-site": "cross-site"},
        ):
            assert client.post(endpoint, json={"website": "Robinhood.com"}, headers=headers).status_code == 403
        assert seen == fetched == []
        before = store.accounts("demo")
        response = client.post(endpoint, json={"website": " Robinhood.com/us/en/?ref=abc#top "})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        result = response.json()
        assert result == {
            "available": True, "website": "https://robinhood.com",
            "logoUrl": "/budgets/demo/api/plugins/logos/bank-logos/manual-domain",
            "provider": {"id": "logos", "display_name": "Fictional bank logos", "provider_name": "Synthetic artwork", "provider_url": "https://icons.example.org"},
        }
        assert seen == ["robinhood.com"] and fetched == ["manual-domain"]
        assert client.get(result["logoUrl"]).content == PNG
        assert client.post(endpoint, json={"website": "http://Robinhood.com"}).json() == result
        assert fetched == ["manual-domain"]
        assert store.accounts("demo") == before
        personal = client.post("/budgets/home/api/plugins/bank-logo-preview", json={"website": "Robinhood.com"}).json()
        assert personal["logoUrl"].startswith("/budgets/home/api/")


@pytest.mark.parametrize("website", [None, "", "random garbage", "localhost", "http://127.0.0.1", "bank.example.org:443", "https://user:secret@bank.example.org"])
def test_unusable_preview_is_neutral_without_external_requests(store, tmp_path, website):
    calls = []
    plugin = Plugin("logos", "Fictional bank logos", bank_logos=BankLogos(
        lambda _: None, lambda key: calls.append(key), resolve_domain=lambda host: calls.append(host),
    ))
    with client_for(store, tmp_path, plugin) as client:
        result = client.post("/api/plugins/bank-logo-preview", json={"website": website})
        assert result.status_code == 200
        assert result.json()["available"] is False and result.json()["website"] is None
        assert result.json()["logoUrl"] is None and calls == []


@pytest.mark.parametrize("failure", ["disabled", "unknown", "fetch", "resolve"])
def test_unavailable_preview_is_neutral_and_does_not_expose_plugin_errors(store, tmp_path, failure):
    def resolve(_):
        if failure == "resolve":
            raise RuntimeError("PRIVATE_FIXTURE_PROVIDER_ERROR")
        return None if failure == "unknown" else "manual-domain"

    def fetch(_):
        raise RuntimeError("PRIVATE_FIXTURE_PROVIDER_ERROR")

    plugin = Plugin("logos", "Fictional bank logos", bank_logos=BankLogos(lambda _: None, fetch, resolve_domain=resolve))
    with client_for(store, tmp_path, *((plugin,) if failure != "disabled" else ())) as client:
        result = client.post("/api/plugins/bank-logo-preview", json={"website": "bank.example.org"})
        assert result.status_code == 200 and "PRIVATE_FIXTURE_PROVIDER_ERROR" not in result.text
        assert result.json()["available"] is False
        assert result.json()["website"] == "https://bank.example.org" and result.json()["logoUrl"] is None
        assert (result.json()["provider"] is None) == (failure == "disabled")
