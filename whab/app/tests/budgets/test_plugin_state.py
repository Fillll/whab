"""Versioned state never changes active data while compatibility is checked."""

import copy
import io
import json
import zipfile
from dataclasses import replace

import pytest

from whab.app import create_app
from whab.budgets.container import decode_container, encode_container
from whab.budgets.document import encode_bundle, new_manifest, upgrade_manifest, validate_manifest
from whab.budgets.plugin_state import check_saved_states, stage_plugin_states
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.domain.errors import StoreError
from whab.plugins import Plugin, PluginContract, PluginError, PluginRegistry


def descriptor(version=1, edges=(), migrate=None):
    return Plugin("stateful", "Stateful", insights=lambda _: (),
                  contract=PluginContract(state_version=version, state_upgrades=edges),
                  migrate_state=migrate)


def saved():
    value = new_manifest("Fictional", ("stateful", "unavailable"))
    value["plugins"][1]["state"] = {"old": [1, 2]}
    value["plugins"][2].update(enabled=False, state={"opaque": ["preserve"]})
    return value


def test_explicit_manifest_upgrade_preserves_v1_and_all_opaque_state():
    original = saved()
    upgraded = upgrade_manifest(original)
    assert original["formatVersion"] == 1
    assert upgraded["formatVersion"] == 3
    assert all(record["stateVersion"] == 1 for record in upgraded["plugins"])
    assert upgrade_manifest(upgraded) == upgraded
    files = {"plugins/unavailable/model.bin": b"\x00opaque\xff"}
    restored = decode_container(encode_container(upgraded, files))
    assert restored.manifest == upgraded and restored.files == files
    with zipfile.ZipFile(io.BytesIO(encode_container(upgraded, files))) as archive:
        wire = json.loads(archive.read("manifest.json"))
        assert wire["formatVersion"] == 3
        assert wire["plugins"][1]["stateVersion"] == 1
        assert "state" not in wire["plugins"][1]


def test_v3_neutral_ledger_roundtrip_preserves_versions():
    manifest = upgrade_manifest(saved())
    ledger = {"formatVersion": 1, "budgetStartMonth": "2026-10", "accounts": [],
              "categories": [], "transactions": [], "assignments": []}
    result = decode_container(encode_bundle(manifest, ledger))
    assert result.manifest == manifest
    assert result.legacy_ledger["accounts"] == []


@pytest.mark.parametrize("version", [True, 0, -1, 65536, "1", None])
def test_v3_rejects_invalid_state_versions(version):
    manifest = upgrade_manifest(saved())
    manifest["plugins"][1]["stateVersion"] = version
    with pytest.raises(StoreError, match="manifest"):
        validate_manifest(manifest)


def test_exact_schemas_reject_unversioned_new_fields_and_missing_v3_fields():
    value = saved()
    value["plugins"][1]["stateVersion"] = 1
    with pytest.raises(StoreError):
        validate_manifest(value)
    value["formatVersion"] = 3
    with pytest.raises(StoreError):
        validate_manifest(value)


def test_saved_state_preflight_reports_newer_missing_path_and_required_upgrade():
    manifest = upgrade_manifest(saved())
    check_saved_states(manifest, (descriptor(),))
    for plugin, version, code in ((descriptor(), 2, "unsupported_state"),
                                  (descriptor(3), 1, "unsupported_state"),
                                  (descriptor(3, ((1, 2), (2, 3))), 1, "state_upgrade_required")):
        manifest["plugins"][1]["stateVersion"] = version
        with pytest.raises(PluginError) as error:
            check_saved_states(manifest, (plugin,))
        assert error.value.code == code and error.value.plugin_id == "stateful"
    manifest["plugins"][1]["enabled"] = False
    check_saved_states(manifest, (descriptor(),))
    check_saved_states(new_manifest("Empty"), (descriptor(3),))


def test_state_upgrade_is_detached_and_scoped_and_runs_each_declared_edge():
    manifest = saved()
    files = {"plugins/stateful/old.bin": b"before", "plugins/unavailable/model": b"opaque"}
    original = copy.deepcopy((manifest, files))
    calls = []

    def migrate(context, source, target):
        calls.append((source, target, dict(context.state)))
        assert context.read_file("old.bin") == (b"before" if source == 1 else None)
        context.save_state({"version": target})
        context.write_file("nested/new.bin", bytes([target]))
        context.delete_file("old.bin")

    plugin = descriptor(3, ((1, 2), (2, 3)), migrate)
    staged = stage_plugin_states(manifest, files, (plugin,))
    assert staged.migrated == ("stateful",)
    assert calls == [(1, 2, {"old": [1, 2]}), (2, 3, {"version": 2})]
    assert staged.manifest["plugins"][1]["stateVersion"] == 3
    assert staged.manifest["plugins"][1]["state"] == {"version": 3}
    assert staged.files == {"plugins/stateful/nested/new.bin": b"\x03", "plugins/unavailable/model": b"opaque"}
    assert (manifest, files) == original
    check_saved_states(staged.manifest, (plugin,))
    assert "opaque" not in repr(staged)
    assert stage_plugin_states(staged.manifest, staged.files, (plugin,)).migrated == ()


def assert_closed(context):
    for operation in (
        lambda: context.save_state({"late": True}),
        lambda: context.read_file("original"),
        lambda: context.write_file("late", b"unchecked"),
        lambda: context.delete_file("original"),
    ):
        with pytest.raises(ValueError, match="context has closed"):
            operation()


def test_each_edge_revokes_its_context_before_another_edge_or_owner_runs():
    manifest = saved()
    manifest["plugins"][2]["enabled"] = True
    retained = []

    def first(context, source, target):
        for old_context in retained:
            assert_closed(old_context)
        retained.append(context)
        context.save_state({"first": target})
        context.write_file("owned", b"first")

    def second(context, source, target):
        for old_context in retained:
            assert_closed(old_context)
        retained.append(context)
        context.save_state({"second": target})
        context.write_file("owned", b"second")

    first_plugin = descriptor(3, ((1, 2), (2, 3)), first)
    second_plugin = replace(descriptor(2, ((1, 2),), second), id="unavailable")
    result = stage_plugin_states(manifest, {}, (first_plugin, second_plugin))
    original = copy.deepcopy((result.manifest, result.files))
    for context in retained:
        assert_closed(context)
    assert (result.manifest, result.files) == original
    assert result.manifest["plugins"][1]["state"] == {"first": 3}
    assert result.manifest["plugins"][2]["state"] == {"second": 2}
    assert result.files == {"plugins/stateful/owned": b"first", "plugins/unavailable/owned": b"second"}


def test_context_mappings_and_saved_values_are_detached_from_returned_candidate():
    manifest = saved()
    manifest["plugins"][1]["settings"] = {"nested": {"rows": [1]}}
    retained = []
    payload = {"saved": [1]}

    def migrate(context, source, target):
        retained.append(context)
        with pytest.raises(TypeError):
            context.settings["new"] = True
        with pytest.raises(TypeError):
            context.state["new"] = True
        context.settings["nested"]["rows"].append(2)
        context.state["old"].append(3)
        context.save_state(payload)

    result = stage_plugin_states(manifest, {}, (descriptor(2, ((1, 2),), migrate),))
    payload["saved"].append(2)
    retained[0].settings["nested"]["rows"].append(3)
    retained[0].state["old"].append(4)
    assert result.manifest["plugins"][1]["settings"] == {"nested": {"rows": [1]}}
    assert result.manifest["plugins"][1]["state"] == {"saved": [1]}
    assert manifest["plugins"][1]["state"] == {"old": [1, 2]}


def test_failed_migrations_also_revoke_all_retained_callbacks():
    retained = []

    def migrate(context, source, target):
        retained.append(context)
        context.write_file("original", b"before")
        raise RuntimeError("fictional failure")

    with pytest.raises(PluginError, match="active state is unchanged"):
        stage_plugin_states(saved(), {}, (descriptor(2, ((1, 2),), migrate),))
    assert_closed(retained[0])


def test_every_compatibility_path_is_checked_before_any_migrator_runs():
    calls = []
    first = descriptor(2, ((1, 2),), lambda *args: calls.append("called"))
    other = replace(descriptor(2), id="unavailable")
    manifest = saved()
    manifest["plugins"][2]["enabled"] = True
    with pytest.raises(PluginError, match="no declared upgrade"):
        stage_plugin_states(manifest, {}, (first, other))
    assert calls == []
    with pytest.raises(PluginError) as error:
        stage_plugin_states(manifest, {}, (descriptor(2, ((1, 2),)),))
    assert error.value.code == "missing_state_migrator"


@pytest.mark.parametrize("action", ["raise", "traversal", "reserved", "metadata", "type", "return", "async", "state"])
def test_failed_migration_never_changes_inputs_or_leaks_errors(action):
    manifest, files = saved(), {"plugins/stateful/old": b"before"}
    original = copy.deepcopy((manifest, files))

    async def asynchronous():
        return None

    def migrate(context, source, target):
        context.save_state({"staged": True})
        context.write_file("safe", b"staged")
        if action == "raise":
            raise RuntimeError("private-fixture-details")
        if action == "traversal":
            context.write_file("../unavailable/model", b"invalid")
        if action == "reserved":
            context.write_file("settings.json", b"invalid")
        if action == "metadata":
            context.save_state({"accessToken": "private-fixture-details"})
        if action == "type":
            context.write_file("text", "not-bytes")
        if action == "return":
            return {"not": "a callback result"}
        if action == "async":
            return asynchronous()
        if action == "state":
            context.save_state([])

    with pytest.raises(PluginError) as error:
        stage_plugin_states(manifest, files, (descriptor(2, ((1, 2),), migrate),))
    assert error.value.code == "state_migration_failed"
    assert "private-fixture-details" not in str(error.value)
    assert (manifest, files) == original


def test_app_rejects_saved_state_before_binders_and_factories(tmp_path):
    manifest = upgrade_manifest(new_manifest("Fictional", storage="backend"))
    manifest["plugins"][0]["stateVersion"] = 2
    session = BudgetSession(tmp_path, "sandbox", manifest)
    calls = []
    plugin = Plugin("backend", "Backend", storage=lambda _: calls.append("factory"),
                    bind=lambda _: calls.append("binder"), contract=PluginContract())
    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), "backend", "")
    with pytest.raises(PluginError) as error:
        create_app(settings=configuration, plugins=PluginRegistry((plugin,)), budget_session=session)
    assert error.value.code == "unsupported_state"
    assert calls == []
    assert not session.storage_home.exists()


def test_v3_session_can_add_optional_plugin_without_dropping_versions(tmp_path):
    session = BudgetSession(tmp_path, "sandbox", upgrade_manifest(saved()))
    session.save_selection(("stateful", "new-plugin"))
    assert session.plugin_record("new-plugin")["stateVersion"] == 1
    assert session.plugin_record("unavailable")["state"] == {"opaque": ["preserve"]}
    assert not session.plugin_record("unavailable")["enabled"]


def test_v3_preserves_opaque_future_sdk_state_but_refuses_active_incompatible_plugin():
    manifest = upgrade_manifest(saved())
    manifest["plugins"][2].update(apiVersion=23, stateVersion=45)
    files = {"plugins/unavailable/future.model": b"opaque"}
    restored = decode_container(encode_container(manifest, files))
    assert restored.manifest == manifest and restored.files == files
    check_saved_states(restored.manifest, (descriptor(),))
    manifest["plugins"][1]["apiVersion"] = 2
    with pytest.raises(PluginError) as error:
        check_saved_states(manifest, (descriptor(),))
    assert error.value.code == "unsupported_saved_sdk"


def test_new_plugin_starts_at_its_current_state_schema_without_migrating_old_payload(tmp_path):
    session = BudgetSession(tmp_path, "sandbox", saved())
    session.save_selection(("stateful", "new-plugin"), state_versions={"new-plugin": 4})
    assert session.manifest["formatVersion"] == 3
    assert session.plugin_record("new-plugin")["stateVersion"] == 4
    assert session.plugin_record("stateful")["stateVersion"] == 1
    assert session.plugin_record("stateful")["state"] == {"old": [1, 2]}
