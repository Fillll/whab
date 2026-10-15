"""Local plugin options expose only validated status and advertised user actions."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.plugins import (
    BankLogos,
    Insight,
    LogoImage,
    Plugin,
    PluginAction,
    PluginError,
    PluginRegistry,
    PluginSettings,
)

PNG = b"\x89PNG\r\n\x1a\nfixture"


def client_for(store, home, *optional):
    config = Settings(home, 5173, "sandbox", "FICTIONAL_CLIENT", "FICTIONAL_SECRET", "FICTIONAL_KEY", False, "", "", (), ui="")
    return TestClient(
        create_app(store=store, settings=config, plugins=PluginRegistry(optional)),
        base_url="http://127.0.0.1:5173",
    )


def test_settings_action_http_refreshes_status_and_keeps_ledger_and_credentials_scoped(store, tmp_path):
    calls = []
    def read():
        calls.append("read")
        return (Insight("Cache", str(calls.count("clear")), "One year"),)
    plugin = Plugin(
        "options", "Options", insights=lambda _: (),
        settings=PluginSettings(read, (PluginAction("clear", "Flush cache", "Download again on request."),), lambda action: calls.append(action) or "Cache cleared."),
    )
    with client_for(store, tmp_path, plugin) as client:
        assert calls == []
        result = client.get("/api/plugins/options/settings")
        assert result.status_code == 200
        assert result.json() == {"insights": [{"title": "Cache", "value": "0", "detail": "One year"}], "actions": [{"id": "clear", "label": "Flush cache", "description": "Download again on request."}]}
        assert result.headers["cache-control"] == "no-store"
        assert calls == ["read"]
        result = client.post("/api/plugins/options/settings/actions/clear", json={})
        assert result.status_code == 200
        assert result.json()["message"] == "Cache cleared."
        assert result.json()["settings"]["insights"][0]["value"] == "1"
        assert calls == ["read", "clear", "read"]
        assert "FICTIONAL" not in result.text
        assert client.get("/api/workspace?workspace=demo").status_code == 200


def test_active_plugins_without_options_have_an_empty_page_but_cannot_run_actions(store, tmp_path):
    plugin = Plugin("plain", "Plain", insights=lambda _: ())
    with client_for(store, tmp_path, plugin) as client:
        assert client.get("/api/plugins/plain/settings").json() == {"insights": [], "actions": []}
        assert client.post("/api/plugins/plain/settings/actions/clear", json={}).status_code == 404
        for path in ("/api/plugins/missing/settings", "/api/plugins/missing/settings/actions/clear", "/api/plugins/plain/settings/actions/unadvertised"):
            result = client.post(path, json={}) if "/actions/" in path else client.get(path)
            assert result.status_code == 404


def test_settings_operations_keep_local_host_origin_content_type_and_size_guards(store, tmp_path):
    calls = []
    plugin = Plugin("options", "Options", insights=lambda _: (), settings=PluginSettings(lambda: calls.append("read") or (), (PluginAction("clear", "Clear"),), lambda _: calls.append("act") or "Done"))
    with client_for(store, tmp_path, plugin) as client:
        path = "/api/plugins/options/settings/actions/clear"
        assert client.get("/api/plugins/options/settings", headers={"host": "foreign.example"}).status_code == 403
        for headers in ({"origin": "https://foreign.example"}, {"sec-fetch-site": "cross-site"}):
            assert client.post(path, json={}, headers=headers).status_code == 403
        assert client.post(path, content="{}", headers={"content-type": "text/plain"}).status_code == 415
        assert client.post(path, content="x" * (32 * 1024 + 1), headers={"content-type": "application/json"}).status_code == 413
        assert client.get(path).status_code == 405
        assert calls == []


def test_settings_and_action_failures_are_sanitized_without_breaking_core_routes(store, tmp_path, caplog):
    def fail(*args):
        raise RuntimeError("FICTIONAL_PRIVATE_PLUGIN_ERROR")
    plugin = Plugin("broken", "Broken", insights=lambda _: (), settings=PluginSettings(fail, (PluginAction("clear", "Clear"),), fail))
    with client_for(store, tmp_path, plugin) as client:
        for result in (client.get("/api/plugins/broken/settings"), client.post("/api/plugins/broken/settings/actions/clear", json={})):
            assert result.status_code == 400
            assert "FICTIONAL_PRIVATE" not in result.text + caplog.text
        assert client.get("/api/workspace?workspace=demo").status_code == 200


def test_disabled_plugin_settings_and_cache_hooks_never_run_on_installed_discovery(store, tmp_path, monkeypatch):
    def fail(*args):
        pytest.fail("Disabled plugin code must not execute")
    entry = SimpleNamespace(name="inactive", group="whab.plugins", value="inactive:plugin", load=fail)
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (entry,))
    with client_for(store, tmp_path) as client:
        assert client.get("/api/plugins/management").status_code == 200
        assert client.get("/api/plugins/inactive/settings").status_code == 404
        assert client.post("/api/plugins/inactive/settings/actions/clear", json={}).status_code == 404
        assert not (tmp_path / "cache").exists()


def test_logo_cache_configuration_happens_once_before_app_uses_the_plugin(store, tmp_path):
    configured = []
    plugin = Plugin("logos", "Logos", bank_logos=BankLogos(lambda _: "bank", lambda _: LogoImage(PNG, "image/png"), configured.append))
    with client_for(store, tmp_path, plugin) as client:
        assert configured == [tmp_path / "cache" / "plugins" / "logos"]
        assert client.get("/api/plugins/logos/bank-logos/bank").status_code == 200
        assert configured == [tmp_path / "cache" / "plugins" / "logos"]
    assert not (tmp_path / "data").exists()


def test_logo_configuration_failure_is_sanitized_before_any_owned_storage_opens(tmp_path, monkeypatch):
    def fail(*args):
        raise PermissionError("FICTIONAL_PRIVATE_PATH")
    plugin = Plugin("logos", "Logos", bank_logos=BankLogos(lambda _: None, lambda _: None, fail))
    monkeypatch.setattr("whab.app.open_storage", lambda *_: pytest.fail("Cache failure must precede storage opening"))
    config = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    with pytest.raises(PluginError) as error:
        create_app(settings=replace(config, storage="not-opened"), plugins=PluginRegistry((plugin,)))
    assert error.value.code == "logo_cache_startup_failed"
    assert "FICTIONAL_PRIVATE" not in str(error.value)
