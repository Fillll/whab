"""Optional branding stays local, deferred and separate from bank credentials."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from whab_sqlite.plugin import plugin as sqlite_plugin

from tests.support.bank_logos import LOGO_PATH, PNG
from tests.support.bank_logos import imported_store as imported_store
from tests.support.ui import make_ui
from whab.app import create_app
from whab.config import Settings
from whab.plugins import BankLogos, LogoImage, Plugin, PluginRegistry


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
        assert client.get("/?page=budget&workspace=demo").status_code == 200


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
        assert client.get("/?page=budget&workspace=personal&month=2026-10").status_code == 200


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
        page = client.get("/?page=accounts&workspace=personal&month=2026-10")
        assert page.status_code == 200 and "data-bank-logo" not in page.text
        assert calls == [] and (tmp_path / "plugins.json").is_file()
