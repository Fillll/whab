"""File parsers are opt-in ingestion hooks without access to live capabilities."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from whab.config import Settings
from whab.plugins import FileImport, Plugin, PluginError, PluginRegistry, load_plugins
from whab.plugins.management import PluginManager

from .test_runtime import FakeEntryPoint


def test_file_import_is_an_optional_ingestion_capability_without_executing_parser(tmp_path):
    calls = []
    capability = FileImport(lambda raw: calls.append(raw), tmp_path, tmp_path)
    plugin = Plugin("dump", "Dump", file_import=capability)
    entry = FakeEntryPoint("dump", plugin)
    registry = load_plugins(("dump",), (entry,))
    assert calls == []
    assert registry.file_import_plugins() == (plugin,)
    description, = registry.describe()
    assert description.has_file_import and not description.has_bank
    assert description.kinds == ("data_ingestion",)
    assert PluginRegistry().file_import_plugins() == ()
    with pytest.raises(FrozenInstanceError):
        capability.parse = None


@pytest.mark.parametrize("args", [
    (None,), (lambda raw: None, Path("/tmp"), None),
    (lambda raw: None, None, Path("/tmp")),
    (lambda raw: None, "templates", Path("/tmp")),
    (lambda raw: None, Path("/tmp"), "static"),
])
def test_file_import_rejects_invalid_hook_and_partial_or_untyped_ui(args):
    with pytest.raises(ValueError):
        FileImport(*args)


def test_plugin_rejects_untyped_import_descriptor_and_revalidates_frozen_bypass():
    with pytest.raises(ValueError):
        Plugin("dump", "Dump", file_import={"parse": lambda raw: None})
    capability = FileImport(lambda raw: None)
    object.__setattr__(capability, "parse", None)
    with pytest.raises(ValueError):
        Plugin("dump", "Dump", file_import=capability)


def test_plugin_manager_checks_import_ui_before_saving_without_calling_parser(tmp_path, monkeypatch):
    from whab_sqlite.plugin import plugin as sqlite_plugin
    from whab_web.plugin import plugin as web_plugin

    def forbidden(raw):
        pytest.fail("Selection must never execute a file parser")

    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    plugin = Plugin("dump", "Dump", file_import=FileImport(forbidden, templates, static))
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (FakeEntryPoint("dump", plugin),))
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "")
    manager = PluginManager(settings, PluginRegistry((web_plugin, sqlite_plugin)))
    with pytest.raises(PluginError, match="resources are missing"):
        manager.save(("dump",))
    assert not (tmp_path / "plugins.json").exists()
    (templates / "accounts.html").write_text("Import")
    with pytest.raises(PluginError, match="resources are missing"):
        manager.save(("dump",))
    (static / "import.js").write_text("// Import")
    assert manager.save(("dump",))["configured"] == ["dump"]
