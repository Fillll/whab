"""Settings persistence, safe discovery and restart boundaries with fake installed plugins."""

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from whab.config import Settings
from whab.plugins import (
    ENTRYPOINT_GROUP,
    BankUI,
    Plugin,
    PluginError,
    PluginRegistry,
    UserInterface,
)
from whab.plugins.configuration import read_selection, write_selection
from whab.plugins.management import PluginManager


@dataclass
class Entry:
    name: str
    descriptor: object
    group: str = ENTRYPOINT_GROUP
    loaded: int = 0
    value: str = ""

    def load(self):
        self.loaded += 1
        if isinstance(self.descriptor, Exception):
            raise self.descriptor
        return self.descriptor


def settings(home, plugins=(), ui="web"):
    return Settings(home, 5173, "sandbox", "fixture-client", "fixture-secret", "fixture-key", False, "", "", plugins, ui=ui)


def forbidden(*args, **kwargs):
    pytest.fail("Plugin management must not invoke factories, hooks or UI builders")


@pytest.fixture
def installed(monkeypatch, tmp_path):
    entries = (
        Entry("sqlite", Plugin("sqlite", "SQLite", storage=forbidden)),
        Entry("web", Plugin("web", "Web UI", ui=UserInterface(tmp_path, tmp_path, forbidden))),
        Entry("report", Plugin("report", "Report", insights=forbidden)),
        Entry("bank", Plugin("bank", "Bank", bank=forbidden)),
        Entry("broken", RuntimeError("FICTIONAL_PRIVATE_PLUGIN_ERROR")),
    )
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **kwargs: entries)
    return entries


def manager(home, entries, optional=()):
    descriptors = tuple(entry.descriptor for entry in entries if entry.name in {"sqlite", "web", *optional})
    return PluginManager(settings(home, optional), PluginRegistry(descriptors))


def test_discovery_lists_inactive_plugins_without_importing_or_exposing_settings(tmp_path, installed):
    instance = manager(tmp_path, installed)
    state = instance.projection()
    rows = {row["id"]: row for row in state["plugins"]}
    assert rows["sqlite"]["required"] and rows["web"]["required"]
    assert rows["sqlite"]["enabled"] and not rows["sqlite"]["manageable"]
    assert rows["report"]["manageable"] and not rows["report"]["active"]
    assert rows["report"]["kinds"] == []  # Unknown until explicitly loaded.
    assert rows["broken"]["manageable"]  # Discovery never executes its broken import.
    assert not state["restart_required"] and state["error"] is None
    assert all(entry.loaded == 0 for entry in installed)
    assert not (tmp_path / "plugins.json").exists()
    assert "fixture-secret" not in json.dumps(state)
    assert "fixture-key" not in json.dumps(state)


def test_disabled_bundled_plugins_keep_canonical_names_without_importing(tmp_path, monkeypatch):
    registrations = (
        ("bank-logos", "whab_plugin_bank_logos.plugin:plugin", "Bank logos", "interface"),
        ("web", "whab_plugin_web.plugin:plugin", "WHAB web", "interface"),
        ("plaid", "whab_plugin_plaid.plugin:plugin", "Plaid", "ingestion"),
        ("ynab", "whab_plugin_ynab.plugin:plugin", "Import from YNAB ZIP file", "ingestion"),
        ("sqlite", "whab_plugin_sqlite.plugin:plugin", "SQLite", "storage"),
        ("example", "whab_plugin_example:plugin", "Month totals example", "example"),
    )
    entries = tuple(
        Entry(name, RuntimeError("Disabled plugins must not be imported"), value=value)
        for name, value, _, _ in registrations
    )
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **kwargs: entries)
    instance = PluginManager(settings(tmp_path, ui=""), PluginRegistry())
    rows = {row["id"]: row for row in instance.projection()["plugins"]}
    for name, _, display_name, category in registrations:
        assert rows[name]["display_name"] == display_name
        assert rows[name]["category"] == category
        assert not rows[name]["active"]
        assert rows[name]["kinds"] == []
    assert all(entry.loaded == 0 for entry in entries)


def test_bundled_names_require_exact_unambiguous_identity_and_preserve_active_names(tmp_path, monkeypatch):
    active = Plugin("plaid", "Active provider name", bank=forbidden)
    entries = (
        Entry("plaid", active, value="whab_plugin_plaid.plugin:plugin"),
        Entry("web", RuntimeError("do not import"), value="third_party.plugin:plugin"),
        Entry("ynab", RuntimeError("do not import"), value="whab_plugin_ynab.plugin:plugin"),
        Entry("ynab", RuntimeError("do not import"), value="another.plugin:plugin"),
        Entry("custom", RuntimeError("do not import"), value="whab_plugin_web.plugin:plugin"),
    )
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **kwargs: entries)
    instance = PluginManager(settings(tmp_path, ui=""), PluginRegistry((active,)))
    rows = {row["id"]: row for row in instance.projection()["plugins"]}
    assert rows["plaid"]["display_name"] == "Active provider name"
    for name in ("web", "ynab", "custom"):
        assert rows[name]["display_name"] == name
        assert rows[name]["category"] == "extension"
    assert rows["sqlite"]["display_name"] == "sqlite"  # Missing metadata stays unknown.
    assert all(entry.loaded == 0 for entry in entries)


def test_save_enables_next_start_without_running_code_or_mutating_registry(tmp_path, installed):
    instance = manager(tmp_path, installed)
    env = tmp_path / ".env"
    ledger = tmp_path / "data" / "ledger.db"
    env.write_text("FICTIONAL_CREDENTIALS_UNCHANGED")
    ledger.parent.mkdir()
    ledger.write_bytes(b"FICTIONAL_LEDGER_UNCHANGED")
    state = instance.save(("report", "bank"))
    assert state["configured"] == ["report", "bank"]
    assert state["active"] == [] and state["restart_required"]
    assert read_selection(tmp_path) == ("report", "bank")
    assert [row.id for row in instance.plugins.describe()] == ["sqlite", "web"]
    assert installed[0].loaded == installed[1].loaded == installed[4].loaded == 0
    assert installed[2].loaded == installed[3].loaded == 1
    assert env.read_text() == "FICTIONAL_CREDENTIALS_UNCHANGED"
    assert ledger.read_bytes() == b"FICTIONAL_LEDGER_UNCHANGED"
    assert list(tmp_path.glob(".plugins-*")) == []


def test_disabling_retains_running_bank_until_restart_and_pending_save_can_be_reverted(tmp_path, installed):
    instance = manager(tmp_path, installed, ("bank",))
    state = instance.save(())
    assert state["active"] == ["bank"] and state["configured"] == []
    assert state["restart_required"] and instance.plugins.bank_plugins()
    state = instance.save(("bank",))
    assert not state["restart_required"]
    assert instance.plugins.bank_plugins()[0].bank is forbidden


def test_settings_load_uses_saved_selection_after_restart_and_environment_without_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WHAB_HOME", str(tmp_path))
    monkeypatch.setenv("WHAB_PLUGINS", "plaid")
    monkeypatch.setenv("WHAB_STORAGE", "sqlite")
    monkeypatch.setenv("WHAB_UI", "web")
    monkeypatch.setenv("WHAB_ENV", "sandbox")
    monkeypatch.setenv("PORT", "5173")
    assert Settings.load().plugins == ("plaid",)
    write_selection(tmp_path, ())
    reloaded = Settings.load()
    assert reloaded.plugins == ()
    assert reloaded.enabled_plugins() == ("sqlite", "web")
    write_selection(tmp_path, ("report",))
    assert Settings.load().plugins == ("report",)


@pytest.mark.parametrize("contents", [
    "not json", '{"version":true,"plugins":[]}', '{"version":2,"plugins":[]}',
    '{"version":1,"plugins":["report","report"]}',
    '{"version":1,"plugins":["../FICTIONAL_PRIVATE"]}',
    '{"version":1,"plugins":[],"secret":"FICTIONAL_PRIVATE"}',
    "x" * (16 * 1024 + 1),
])
def test_malformed_file_fails_startup_safely_but_explicit_save_repairs_it(tmp_path, installed, monkeypatch, contents):
    (tmp_path / "plugins.json").write_text(contents)
    monkeypatch.setenv("WHAB_HOME", str(tmp_path))
    monkeypatch.setenv("WHAB_ENV", "sandbox")
    monkeypatch.setenv("PORT", "5173")
    with pytest.raises(PluginError) as error:
        Settings.load()
    assert error.value.code == "invalid_saved_configuration"
    assert "FICTIONAL_PRIVATE" not in str(error.value)
    instance = manager(tmp_path, installed)
    assert instance.projection()["error"]
    assert instance.save(())["error"] is None
    assert read_selection(tmp_path) == ()


@pytest.mark.parametrize(("selection", "code"), [
    (("INVALID",), "invalid_configuration"),
    (("report", "report"), "duplicate_configuration"),
    (("missing",), "missing"),
    (("sqlite",), "required_plugin"),
    (("web",), "required_plugin"),
    (("broken",), "load_failed"),
])
def test_invalid_save_preserves_original_selection_and_sanitizes_errors(tmp_path, installed, selection, code):
    write_selection(tmp_path, ())
    previous = (tmp_path / "plugins.json").read_bytes()
    instance = manager(tmp_path, installed)
    with pytest.raises(PluginError) as error:
        instance.save(selection)
    assert error.value.code == code
    assert "FICTIONAL_PRIVATE" not in str(error.value)
    assert (tmp_path / "plugins.json").read_bytes() == previous


def test_duplicate_or_unavailable_registration_is_diagnosed_without_importing(tmp_path, installed, monkeypatch):
    duplicate = Entry("report", RuntimeError("do not import"))
    entries = (*installed, duplicate)
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **kwargs: entries)
    instance = manager(tmp_path, installed)
    write_selection(tmp_path, ("missing",))
    rows = {row["id"]: row for row in instance.projection()["plugins"]}
    assert not rows["report"]["manageable"] and "more than once" in rows["report"]["reason"]
    assert not rows["missing"]["manageable"] and "Not installed" in rows["missing"]["reason"]
    with pytest.raises(PluginError) as error:
        instance.save(("report",))
    assert error.value.code == "duplicate_entrypoint"
    assert all(entry.loaded == 0 for entry in entries)


def test_optional_selection_cannot_switch_storage_or_ui_and_headless_rejects_ui(tmp_path, installed, monkeypatch):
    second_storage = Entry("alternate_storage", Plugin("alternate_storage", "Other storage", storage=forbidden))
    second_ui = Entry("alternate_ui", Plugin("alternate_ui", "Other UI", ui=UserInterface(tmp_path, tmp_path, forbidden)))
    entries = (*installed, second_storage, second_ui)
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **kwargs: entries)
    instance = manager(tmp_path, entries)
    for name, code in (("alternate_storage", "multiple_storage_plugins"), ("alternate_ui", "multiple_ui_plugins")):
        with pytest.raises(PluginError) as error:
            instance.save((name,))
        assert error.value.code == code
    headless = PluginManager(replace(settings(tmp_path), ui=""), PluginRegistry((installed[0].descriptor,)))
    with pytest.raises(PluginError) as error:
        headless.save(("alternate_ui",))
    assert error.value.code == "invalid_configuration"
    assert headless.save(("report",))["restart_required"]


def test_discovery_read_and_atomic_write_failures_are_safe_and_leave_prior_file(tmp_path, installed, monkeypatch):
    instance = manager(tmp_path, installed)
    write_selection(tmp_path, ())
    previous = (tmp_path / "plugins.json").read_bytes()
    def failed_discovery(**kwargs):
        raise RuntimeError("FICTIONAL_PRIVATE_DISCOVERY")
    with monkeypatch.context() as patch:
        patch.setattr("whab.plugins.management.metadata.entry_points", failed_discovery)
        state = instance.projection()
        assert state["error"] and "FICTIONAL_PRIVATE" not in json.dumps(state)
        with pytest.raises(PluginError) as error:
            instance.save(("report",))
        assert error.value.code == "discovery_failed"
    def failed_replace(*args):
        raise PermissionError("FICTIONAL_PRIVATE_PATH")
    monkeypatch.setattr("whab.plugins.configuration.os.replace", failed_replace)
    with pytest.raises(PluginError) as error:
        instance.save(("report",))
    assert error.value.code == "saved_configuration_write_failed"
    assert "FICTIONAL_PRIVATE" not in str(error.value)
    assert (tmp_path / "plugins.json").read_bytes() == previous
    assert list(tmp_path.glob(".plugins-*")) == []


def test_save_reuses_custom_mandatory_descriptors_without_reimporting_them(tmp_path, installed):
    required = (
        Plugin("custom_storage", "Custom storage", storage=forbidden),
        Plugin("custom_ui", "Custom UI", ui=UserInterface(tmp_path, tmp_path, forbidden)),
    )
    selected = replace(settings(tmp_path), storage="custom_storage", ui="custom_ui")
    instance = PluginManager(selected, PluginRegistry(required))
    assert instance.save(("report",))["configured"] == ["report"]
    assert all(entry.loaded == 0 for entry in installed if entry.name != "report")


def test_missing_optional_bank_resources_are_rejected_before_persistence(tmp_path, installed, monkeypatch):
    entry = Entry("missing_bank_ui", Plugin("missing_bank_ui", "Bank UI", bank=forbidden, bank_ui=BankUI(tmp_path, tmp_path)))
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **kwargs: (*installed, entry))
    instance = manager(tmp_path, installed)
    with pytest.raises(PluginError) as error:
        instance.save((entry.name,))
    assert error.value.code == "missing_resources"
    assert not (tmp_path / "plugins.json").exists()
    # Headless operation does not depend on a bank's optional browser files.
    headless = PluginManager(replace(settings(tmp_path), ui=""), PluginRegistry((installed[0].descriptor,)))
    assert headless.save((entry.name,))["restart_required"]


def test_unreadable_selection_is_sanitized_and_does_not_break_projection(tmp_path, installed, monkeypatch):
    write_selection(tmp_path, ())
    original = Path.open
    def denied(path, *args, **kwargs):
        if path.name == "plugins.json":
            raise PermissionError("FICTIONAL_PRIVATE_PATH")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", denied)
        with pytest.raises(PluginError) as error:
            read_selection(tmp_path)
        assert error.value.code == "saved_configuration_read_failed"
        state = manager(tmp_path, installed).projection()
        assert state["error"] and "FICTIONAL_PRIVATE" not in json.dumps(state)
    assert manager(tmp_path, installed).save(())["error"] is None
