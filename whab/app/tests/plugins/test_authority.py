"""Authority policy follows capabilities, including independently named providers."""

from dataclasses import replace

import pytest

from whab.app import create_app, storage_config
from whab.budgets.document import new_manifest, upgrade_manifest
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.plugins import (
    Capability,
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    Requirement,
)
from whab.plugins.management import PluginManager

from .test_management import Entry


def configuration(home):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), "renamed", "")


def test_independently_named_remote_storage_rejects_banks_before_any_factory(tmp_path):
    calls = []
    remote = Plugin("renamed", "Remote", storage=lambda _: calls.append("storage"),
                    contract=PluginContract(authority="remote"))
    bank = Plugin("fictional", "Bank", bank=lambda _: calls.append("bank"))
    with pytest.raises(PluginError, match="belongs on the server"):
        create_app(settings=configuration(tmp_path), plugins=PluginRegistry((bank, remote)))
    assert calls == []


def test_remote_storage_ignores_portable_credential_destination_hints(tmp_path, monkeypatch):
    manifest = new_manifest("Fictional", storage="renamed")
    manifest["plugins"][0]["settings"] = {"RENAMED_URL": "https://wrong.invalid", "RENAMED_BUDGET_ID": "wrong"}
    session = BudgetSession(tmp_path, "sandbox", manifest)
    plugin = Plugin("renamed", "Remote", storage=lambda _: None, contract=PluginContract(authority="remote"))
    monkeypatch.setenv("RENAMED_TOKEN", "synthetic-device-credential")
    assert storage_config(configuration(tmp_path), session, plugin) == {"RENAMED_TOKEN": "synthetic-device-credential"}


def test_management_resolves_required_capabilities_against_complete_selection(tmp_path, monkeypatch):
    storage = Plugin("renamed", "Local", storage=lambda _: None,
                     contract=PluginContract(provides=(Capability("fictional.source"),)))
    report = Plugin("report", "Report", insights=lambda _: (),
                    contract=PluginContract(requires=(Requirement("fictional.source"),)))
    entries = (Entry("report", report), Entry("renamed", storage))
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: entries)
    manager = PluginManager(configuration(tmp_path), PluginRegistry((storage,)))
    assert manager.save(("report",))["configured"] == ["report"]


def test_management_rejects_remote_bank_selection_without_persisting(tmp_path, monkeypatch):
    remote = Plugin("renamed", "Remote", storage=lambda _: None, contract=PluginContract(authority="remote"))
    bank = Plugin("bank", "Bank", bank=lambda _: None)
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (Entry("renamed", remote), Entry("bank", bank)))
    manager = PluginManager(configuration(tmp_path), PluginRegistry((remote,)))
    with pytest.raises(PluginError, match="belongs on the server"):
        manager.save(("bank",))
    assert not (tmp_path / "plugins.json").exists()


def test_management_checks_newly_enabled_saved_state_and_initializes_fresh_state(tmp_path, monkeypatch):
    storage = Plugin("renamed", "Local", storage=lambda _: None)
    plugin = Plugin("report", "Report", insights=lambda _: (), contract=PluginContract(state_version=4))
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (Entry("renamed", storage), Entry("report", plugin)))
    manifest = upgrade_manifest(new_manifest("Fictional", ("report",), storage="renamed"))
    manifest["plugins"][1].update(enabled=False, stateVersion=5)
    session = BudgetSession(tmp_path, "sandbox", manifest)
    manager = PluginManager(configuration(tmp_path), PluginRegistry((storage,)), budget_session=session)
    with pytest.raises(PluginError) as error:
        manager.save(("report",))
    assert error.value.code == "unsupported_state"
    assert not session.plugin_record("report")["enabled"]
    assert not session.storage_home.exists()
    fresh = BudgetSession(tmp_path, "sandbox", new_manifest("Fresh", storage="renamed"))
    fresh_manager = PluginManager(configuration(tmp_path), PluginRegistry((storage,)), budget_session=fresh)
    assert fresh_manager.save(("report",))["configured"] == ["report"]
    assert fresh.plugin_record("report")["stateVersion"] == 4
    projection = PluginManager(replace(configuration(tmp_path), plugins=("report",)), PluginRegistry((storage, plugin)))
    assert not projection.projection()["restart_required"]
    report_row = next(row for row in projection.projection()["plugins"] if row["id"] == "report")
    assert report_row["compatibility"]["state_version"] == 4
    assert not report_row["compatibility"]["legacy"]
