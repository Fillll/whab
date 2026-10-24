"""Plugin options use scoped, validated plain text and advertised local actions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from importlib.metadata import EntryPoint
from threading import Event

import pytest

from whab.plugins import (
    BankLogos,
    Insight,
    LogoImage,
    Plugin,
    PluginAction,
    PluginError,
    PluginRegistry,
    PluginSettings,
    load_plugins,
)
from whab.plugins.management import PluginManager

from .test_management import settings
from .test_runtime import FakeEntryPoint

PNG = b"\x89PNG\r\n\x1a\nfixture"


def registration(options=None, logos=None):
    return Plugin("example", "Example", insights=lambda _: (), settings=options, bank_logos=logos)


def test_settings_are_additive_frozen_and_do_not_run_during_loading_or_discovery():
    calls = []
    action = PluginAction("clear", "Clear local cache", "Fetch again when requested.")
    options = PluginSettings(lambda: calls.append("read") or (Insight("Cached", "1"),), (action,), lambda action: calls.append(action) or "Done")
    descriptor = registration(options)
    registry = load_plugins(("example",), (FakeEntryPoint("example", descriptor),))
    assert registry.describe()[0].has_settings
    assert calls == []
    assert registry.settings("example") == {"insights": (Insight("Cached", "1"),), "actions": (action,)}
    assert calls == ["read"]
    assert registry.settings_action("example", "clear") == "Done"
    assert calls == ["read", "clear"]
    with pytest.raises(FrozenInstanceError):
        options.actions = ()
    with pytest.raises(FrozenInstanceError):
        action.id = "different"
    assert PluginRegistry((registration(),)).settings("example") == {"insights": (), "actions": ()}


@pytest.mark.parametrize("action", [
    ("../private", "Clear", ""), ("", "Clear", ""), ("clear", "", ""),
    ("clear", 1, ""), ("clear", "x" * 121, ""), ("clear", "Clear", "x" * 2001),
])
def test_action_descriptor_rejects_unsafe_ids_and_unbounded_text(action):
    with pytest.raises(ValueError):
        PluginAction(*action)


@pytest.mark.parametrize("read,actions,act", [
    (None, (), None), (lambda: (), [], None), (lambda: (), ({},), None),
    (lambda: (), (PluginAction("clear", "Clear"),), None),
    (lambda: (), (), "not callable"),
    (lambda: (), (PluginAction("clear", "Clear"),) * 2, lambda _: "Done"),
    (lambda: (), tuple(PluginAction(f"clear-{i}", "Clear") for i in range(51)), lambda _: "Done"),
])
def test_settings_descriptor_rejects_bad_hooks_duplicate_and_unbounded_actions(read, actions, act):
    with pytest.raises(ValueError):
        PluginSettings(read, actions, act)


def test_plugin_descriptor_revalidates_mutated_settings_and_cache_callbacks():
    with pytest.raises(ValueError):
        registration({})
    action = PluginAction("clear", "Clear")
    object.__setattr__(action, "id", "../private")
    with pytest.raises(ValueError):
        PluginSettings(lambda: (), (action,), lambda _: "Done")
    options = PluginSettings(lambda: ())
    object.__setattr__(options, "read", None)
    with pytest.raises(ValueError):
        registration(options)
    with pytest.raises(ValueError):
        BankLogos(lambda _: None, lambda _: None, configure_cache="bad")
    logos = BankLogos(lambda _: None, lambda _: None)
    object.__setattr__(logos, "configure_cache", "bad")
    with pytest.raises(ValueError):
        registration(logos=logos)


@pytest.mark.parametrize("output", [[], ("not an insight",), (Insight("A", "B"),) * 51])
def test_settings_outputs_are_bounded_and_sanitized(output):
    registry = PluginRegistry((registration(PluginSettings(lambda: output)),))
    with pytest.raises(PluginError) as error:
        registry.settings("example")
    assert error.value.code == "hook_failed"


def test_settings_mutated_outputs_and_exceptions_never_escape(caplog):
    def fail(*args):
        raise RuntimeError("FICTIONAL_SECRET")
    invalid = Insight("A", "B")
    object.__setattr__(invalid, "detail", "x" * 2001)
    for read in (fail, lambda: (invalid,)):
        registry = PluginRegistry((registration(PluginSettings(read)),))
        with pytest.raises(PluginError) as error:
            registry.settings("example")
        assert "FICTIONAL_SECRET" not in str(error.value)
    registry = PluginRegistry((registration(PluginSettings(lambda: (), (PluginAction("clear", "Clear"),), fail)),))
    with pytest.raises(PluginError) as error:
        registry.settings_action("example", "clear")
    assert error.value.code == "hook_failed"
    assert "FICTIONAL_SECRET" not in str(error.value) + caplog.text


@pytest.mark.parametrize("message", [None, "", "x" * 2001, object()])
def test_action_outputs_require_bounded_plain_text(message):
    options = PluginSettings(lambda: (), (PluginAction("clear", "Clear"),), lambda _: message)
    registry = PluginRegistry((registration(options),))
    with pytest.raises(PluginError) as error:
        registry.settings_action("example", "clear")
    assert error.value.code == "hook_failed"


def test_only_available_settings_and_advertised_action_ids_can_execute():
    calls = []
    options = PluginSettings(lambda: (), (PluginAction("clear", "Clear"),), lambda action: calls.append(action) or "Done")
    registry = PluginRegistry((registration(options),))
    for plugin_id, action_id in (("missing", "clear"), ("example", "../private"), ("example", "unadvertised")):
        with pytest.raises(PluginError) as error:
            registry.settings_action(plugin_id, action_id)
        assert error.value.code == "unavailable"
    with pytest.raises(PluginError) as error:
        registry.settings("../private")
    assert error.value.code == "unavailable"
    with pytest.raises(PluginError):
        PluginRegistry((registration(),)).settings_action("example", "clear")
    assert calls == []
    object.__setattr__(options, "read", None)
    with pytest.raises(PluginError) as error:
        registry.settings_action("example", "clear")
    assert error.value.code == "hook_failed"


def test_successful_settings_action_invalidates_only_that_plugins_host_logo_cache():
    calls = []
    logos = BankLogos(lambda _: "bank", lambda _: calls.append("fetch") or LogoImage(PNG, "image/png"))
    options = PluginSettings(lambda: (), (PluginAction("clear", "Clear"),), lambda _: "Done")
    registry = PluginRegistry((registration(options, logos),))
    registry._logo_cache[("other", "bank")] = (float("inf"), None)
    registry.logo_image("example", "bank")
    registry.logo_image("example", "bank")
    assert calls == ["fetch"]
    registry.settings_action("example", "clear")
    registry.logo_image("example", "bank")
    assert calls == ["fetch", "fetch"]
    assert ("other", "bank") in registry._logo_cache


def test_settings_action_invalidates_an_in_flight_legacy_logo_fetch():
    started, release = Event(), Event()
    stale = LogoImage(b"GIF89astale", "image/gif")
    fresh = LogoImage(PNG, "image/png")
    calls = []

    def fetch(_):
        calls.append("fetch")
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
            return stale
        return fresh

    options = PluginSettings(lambda: (), (PluginAction("clear", "Clear"),), lambda _: "Done")
    registry = PluginRegistry((registration(options, BankLogos(lambda _: "bank", fetch)),))
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(registry.logo_image, "example", "bank")
        assert started.wait(5)
        registry.settings_action("example", "clear")
        release.set()
        assert pending.result(5) == stale
    assert registry.logo_image("example", "bank") == fresh
    assert registry.logo_image("example", "bank") == fresh
    assert calls == ["fetch", "fetch"]


def test_cache_owners_receive_only_their_path_and_control_fetch_expiry(tmp_path):
    calls, configured = [], []
    logos = BankLogos(lambda _: "bank", lambda _: calls.append("fetch") or LogoImage(PNG, "image/png"), configured.append)
    registry = PluginRegistry((registration(logos=logos), Plugin("other", "Other", insights=lambda _: ())))
    registry.configure_logo_caches(tmp_path)
    assert configured == [tmp_path / "cache" / "plugins" / "example"]
    for _ in range(2):
        assert registry.logo_image("example", "bank") == LogoImage(PNG, "image/png")
    assert calls == ["fetch", "fetch"] and not registry._logo_cache
    object.__setattr__(logos, "fetch", lambda _: None)
    assert registry.logo_image("example", "bank") is None
    def fail(*args):
        raise RuntimeError("FICTIONAL_SECRET")
    object.__setattr__(logos, "fetch", fail)
    assert registry.logo_image("example", "bank") is None
    object.__setattr__(logos, "configure_cache", fail)
    with pytest.raises(PluginError) as error:
        registry.configure_logo_caches(tmp_path)
    assert error.value.code == "logo_cache_startup_failed"
    assert "FICTIONAL_SECRET" not in str(error.value)


def test_installed_categories_use_entrypoint_metadata_without_importing_disabled_code(tmp_path, monkeypatch):
    entries = tuple(EntryPoint(name, target, "whab.plugins") for name, target in (
        ("bank-logos", "whab_plugin_bank_logos.plugin:plugin"), ("web", "whab_plugin_web.plugin:plugin"),
        ("plaid", "whab_plugin_plaid.plugin:plugin"), ("ynab", "whab_plugin_ynab.plugin:plugin"),
        ("sqlite", "whab_plugin_sqlite.plugin:plugin"), ("example", "whab_plugin_example:plugin"),
        ("unknown", "does_not_exist:plugin"), ("bank-logos", "does_not_exist:plugin"),
    ))
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: entries)
    instance = PluginManager(settings(tmp_path), PluginRegistry())
    categories = {row["id"]: row["category"] for row in instance.projection()["plugins"]}
    assert categories == {"bank-logos": "extension", "web": "interface", "plaid": "ingestion", "ynab": "ingestion", "sqlite": "storage", "example": "example", "unknown": "extension"}
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: entries[:1])
    assert instance.projection()["plugins"][0]["category"] == "interface"


def test_active_external_plugin_category_follows_capabilities(tmp_path, monkeypatch):
    from whab.plugins import FileImport, UserInterface
    from whab.plugins.management import plugin_category
    registry = PluginRegistry((
        Plugin("ui", "UI", ui=UserInterface(tmp_path, tmp_path, lambda: {})),
        Plugin("storage", "Storage", storage=lambda _: None),
        Plugin("bank", "Bank", bank=lambda _: None),
        Plugin("importer", "Importer", file_import=FileImport(lambda _: None)),
        registration(),
    ))
    assert {description.id: plugin_category(description.id, (), description) for description in registry.describe()} == {
        "ui": "interface", "storage": "storage", "bank": "ingestion", "importer": "ingestion", "example": "insight",
    }
