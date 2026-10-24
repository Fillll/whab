"""Public capability resolution uses synthetic descriptors and no runtime data."""

from dataclasses import FrozenInstanceError, replace
from itertools import permutations
from types import SimpleNamespace

import pytest

from whab.plugins import (
    Capability,
    CapabilityError,
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    Requirement,
    contract_for,
    load_descriptors,
    load_plugins,
    resolve_plugins,
    state_upgrade_path,
)


def plugin(identifier, *, provides=(), requires=(), conflicts=()):
    return Plugin(identifier, identifier, insights=lambda _: (), contract=PluginContract(
        provides=provides, requires=requires, conflicts=conflicts,
    ))


@pytest.mark.parametrize("name", [None, True, "", "Upper", "a/secret", "a..b", "a." , "a" * 101])
def test_invalid_capability_identifiers_are_rejected(name):
    with pytest.raises(ValueError):
        Capability(name)
    with pytest.raises(ValueError):
        Requirement(name)


@pytest.mark.parametrize("version", [True, False, 0, -1, 1.0, "1", 65536])
def test_contract_versions_are_exact_positive_integers(version):
    with pytest.raises(ValueError):
        Capability("test", version)
    with pytest.raises(ValueError):
        Requirement("test", version)
    with pytest.raises(ValueError):
        Requirement("test", max_version=version)
    with pytest.raises(ValueError):
        PluginContract(state_version=version)


def test_requirement_bounds_are_inclusive_and_records_are_immutable():
    requirement = Requirement("test", 2, 3)
    assert [requirement.accepts(version) for version in (1, 2, 3, 4)] == [False, True, True, False]
    assert Requirement("test", 2).accepts(100)
    with pytest.raises(ValueError):
        requirement.accepts(True)
    with pytest.raises(ValueError):
        Requirement("test", 3, 2)
    with pytest.raises(FrozenInstanceError):
        requirement.min_version = 1


@pytest.mark.parametrize("composition", [True, "", "priority"])
def test_unknown_compositions_are_rejected(composition):
    with pytest.raises(ValueError):
        Capability("test", composition=composition)


def test_authoritative_writers_cannot_claim_additive_composition():
    with pytest.raises(ValueError, match="authoritative ledger writer"):
        Capability("ledger.mutate")
    assert Capability("ledger.mutate", composition="singleton").composition == "singleton"


@pytest.mark.parametrize("fields", [
    {"provides": []}, {"requires": []}, {"provides": (object(),)},
    {"requires": (object(),)}, {"provides": (Capability("a"),) * 101},
    {"provides": (Capability("a"), Capability("a", 2))},
    {"requires": (Requirement("a"), Requirement("a", 2))},
    {"conflicts": []}, {"conflicts": ("a",) * 101}, {"conflicts": ("a", "a")},
    {"conflicts": ("private/secret",)},
    {"provides": (Capability("a"),), "conflicts": ("a",)},
    {"lifecycle": "automatic"}, {"lifecycle": True},
    {"authority": "both"}, {"authority": True},
    {"state_upgrades": []}, {"state_upgrades": ((1, 2),) * 101},
    {"state_version": 2, "state_upgrades": ([1, 2],)},
    {"state_version": 2, "state_upgrades": ((1,),)},
    {"state_version": 2, "state_upgrades": ((1, 2, 3),)},
    {"state_version": 2, "state_upgrades": ((0, 2),)},
    {"state_version": 2, "state_upgrades": ((1, True),)},
    {"state_version": 2, "state_upgrades": ((2, 1),)},
    {"state_version": 2, "state_upgrades": ((1, 1),)},
    {"state_version": 2, "state_upgrades": ((1, 3),)},
    {"state_version": 2, "state_upgrades": ((1, 2), (1, 2))},
])
def test_invalid_contract_records_fail_before_selection(fields):
    with pytest.raises(ValueError):
        PluginContract(**fields)


def test_legacy_storage_authority_uses_connection_hook_without_implementation_names():
    local = Plugin("arbitrary", "Local", storage=lambda _: pytest.fail("Must not open storage"))
    legacy = contract_for(local)
    assert legacy.lifecycle == "restart" and legacy.authority == "unknown"
    assert {row.name for row in legacy.provides} == {
        "ledger.read", "ledger.mutate", "provider.state", "provider.sync",
    }
    remote = replace(local, budget_connection=lambda _: {})
    remote_contract = contract_for(remote)
    assert remote_contract.authority == "remote"
    assert {row.name for row in remote_contract.provides} == {"ledger.read", "ledger.mutate"}
    assert remote.contract is None


def test_explicit_contract_preserves_lifecycle_and_authority():
    declared = PluginContract(provides=(Capability("reports"),), lifecycle="live", authority="local")
    descriptor = replace(plugin("reports"), contract=declared)
    effective = contract_for(descriptor)
    assert replace(effective, provides=declared.provides) == declared
    assert {row.name for row in effective.provides} == {"reports", "reporting.insights"}
    assert resolve_plugins((descriptor,)) == (descriptor,)


@pytest.mark.parametrize("name", [
    "ledger.read", "ledger.mutate", "provider.state", "provider.sync", "receipts",
    "transfer.neutral", "transfer.prepare", "interface.ui", "interface.api",
    "reporting.insights", "reporting.export", "ingestion.file", "ingestion.bank",
    "branding.bank", "branding.merchant", "categorization",
])
def test_host_consumed_contracts_cannot_override_the_hosts_supported_version(name):
    composition = "singleton" if name == "ledger.mutate" else "additive"
    descriptor = plugin("future", provides=(Capability(name, 2, composition),))
    with pytest.raises(CapabilityError, match="supports version 1") as raised:
        resolve_plugins((descriptor,))
    assert raised.value.code == "incompatible_host_contract"
    assert raised.value.plugin_id == "future"
    with pytest.raises(PluginError) as raised:
        PluginRegistry((descriptor,))
    assert raised.value.code == "incompatible_host_contract"
    entry = SimpleNamespace(name="future", group="whab.plugins", load=lambda: descriptor)
    with pytest.raises(PluginError) as raised:
        load_plugins(("future",), (entry,))
    assert raised.value.code == "incompatible_host_contract"


def test_custom_contract_versions_remain_negotiable():
    provider = plugin("provider", provides=(Capability("extension.custom", 2),))
    consumer = plugin("consumer", requires=(Requirement("extension.custom", 2, 2),))
    assert resolve_plugins((consumer, provider)) == (provider, consumer)


def test_bind_only_future_contract_is_rejected_before_binding():
    def forbidden(_):
        pytest.fail("An incompatible bound contract must fail before binding")

    descriptor = Plugin("future", "Future", bind=forbidden, contract=PluginContract(
        provides=(Capability("reporting.insights", 2),),
    ))
    with pytest.raises(PluginError) as raised:
        PluginRegistry((descriptor,))
    assert raised.value.code == "incompatible_host_contract"


def test_direct_registry_construction_rejects_unsupported_sdk_versions():
    descriptor = replace(plugin("future"), api_version=2)
    with pytest.raises(CapabilityError) as raised:
        resolve_plugins((descriptor,))
    assert raised.value.code == "incompatible_api" and raised.value.plugin_id == "future"
    with pytest.raises(PluginError) as raised:
        PluginRegistry((descriptor,))
    assert raised.value.code == "incompatible_api"


def test_resolution_is_independent_of_discovery_and_configuration_order():
    storage = plugin("z-storage", provides=(Capability("ledger.read", composition="singleton"),))
    parser = plugin("a-parser", provides=(Capability("ingestion.file"),), requires=(Requirement("ledger.read"),))
    export = plugin("m-export", provides=(Capability("reporting.export"),), requires=(Requirement("ingestion.file"),))
    peer = plugin("n-brand", provides=(Capability("branding.bank"),))
    expected = (peer, storage, parser, export)
    for selection in permutations((storage, parser, export, peer)):
        assert resolve_plugins(selection) == expected


def test_additive_requirements_order_after_all_compatible_providers():
    first = plugin("a", provides=(Capability("reports", 2),))
    second = plugin("z", provides=(Capability("reports", 3),))
    consumer = plugin("b", requires=(Requirement("reports", 2, 3),))
    assert resolve_plugins((consumer, second, first)) == (first, second, consumer)
    self_contained = plugin("self", provides=(Capability("reports"),), requires=(Requirement("reports"),))
    assert resolve_plugins((self_contained,)) == (self_contained,)


@pytest.mark.parametrize(("selection", "code", "identifier"), [
    ((plugin("needs", requires=(Requirement("absent"),)),), "missing_requirement", "needs"),
    ((plugin("needs", requires=(Requirement("thing", 2),)), plugin("old", provides=(Capability("thing"),))), "incompatible_requirement", "needs"),
    ((plugin("needs", requires=(Requirement("thing", 1, 2),)), plugin("new", provides=(Capability("thing", 3),))), "incompatible_requirement", "needs"),
    ((plugin("a", provides=(Capability("one", composition="singleton"),)), plugin("b", provides=(Capability("one", composition="singleton"),))), "conflicting_providers", "a"),
    ((plugin("a", provides=(Capability("one"),)), plugin("b", provides=(Capability("one", composition="singleton"),))), "incompatible_composition", "a"),
    ((plugin("a", conflicts=("one",)), plugin("b", provides=(Capability("one"),))), "capability_conflict", "a"),
    ((plugin("a", provides=(Capability("one"),), requires=(Requirement("two"),)), plugin("b", provides=(Capability("two"),), requires=(Requirement("one"),))), "dependency_cycle", "a"),
    ((plugin("same"), plugin("same")), "duplicate_plugin", "same"),
])
def test_invalid_selections_fail_deterministically(selection, code, identifier):
    for ordering in permutations(selection):
        with pytest.raises(CapabilityError) as raised:
            resolve_plugins(ordering)
        assert raised.value.code == code
        assert raised.value.plugin_id == identifier


@pytest.mark.parametrize("selection", [[], (None,), (plugin("many"),) * 101])
def test_invalid_selection_shapes_are_sanitized(selection):
    with pytest.raises(CapabilityError) as raised:
        resolve_plugins(selection)
    assert raised.value.code == "invalid_contract"


def test_tampered_nested_records_and_descriptors_are_revalidated():
    capability = Capability("safe")
    descriptor = plugin("safe", provides=(capability,))
    object.__setattr__(capability, "name", "private/fixture-secret")
    with pytest.raises(CapabilityError) as raised:
        resolve_plugins((descriptor,))
    assert raised.value.code == "invalid_contract" and raised.value.plugin_id == "safe"
    assert "fixture-secret" not in str(raised.value)
    object.__setattr__(descriptor, "id", "private/fixture-secret")
    with pytest.raises(CapabilityError) as raised:
        resolve_plugins((descriptor,))
    assert raised.value.plugin_id is None and "fixture-secret" not in str(raised.value)


def test_resolution_and_failure_never_call_factories_binders_or_migrations():
    def must_not_run(*args):
        pytest.fail("Pure resolution invoked executable plugin behavior")

    descriptor = Plugin("writer", "Writer", storage=must_not_run, bind=must_not_run,
                        migrate_state=must_not_run, contract=PluginContract(
                            requires=(Requirement("absent"),), state_version=2, state_upgrades=((1, 2),),
                        ))
    with pytest.raises(PluginError) as raised:
        PluginRegistry((descriptor,))
    assert raised.value.code == "missing_requirement" and raised.value.plugin_id == "writer"


def test_entrypoint_loading_resolves_before_budget_binding():
    def must_not_run(*args):
        pytest.fail("Selection failure must precede budget binding")

    descriptor = replace(plugin("consumer", requires=(Requirement("absent"),)), bind=must_not_run)
    entry = SimpleNamespace(name="consumer", group="whab.plugins", load=lambda: descriptor)
    with pytest.raises(PluginError) as raised:
        load_plugins(("consumer",), (entry,))
    assert raised.value.code == "missing_requirement"
    assert load_descriptors(("consumer",), (entry,)) == (descriptor,)
    provider = plugin("provider", provides=(Capability("absent"),))
    assert PluginRegistry((*load_descriptors(("consumer",), (entry,)), provider)).descriptors() == (provider, descriptor)


def test_binding_cannot_change_an_explicit_capability_contract():
    bound = plugin("safe", provides=(Capability("unexpected"),))
    original = replace(plugin("safe"), bind=lambda _: bound)
    with pytest.raises(PluginError) as raised:
        PluginRegistry((original,)).bind_budget(lambda _: None)
    assert raised.value.code == "budget_plugin_startup_failed"


def test_binding_cannot_replace_or_drop_state_migration_behavior():
    def migration(*_):
        return None
    bound = plugin("safe")
    original = replace(bound, bind=lambda _: bound, migrate_state=migration)
    with pytest.raises(PluginError) as raised:
        PluginRegistry((original,)).bind_budget(lambda _: None)
    assert raised.value.code == "budget_plugin_startup_failed"
    complete = replace(bound, migrate_state=migration)
    original = replace(complete, bind=lambda _: complete)
    assert PluginRegistry((original,)).bind_budget(lambda _: None).descriptors() == (complete,)


def test_explicit_metadata_cannot_hide_storage_writers_or_change_ui_composition(tmp_path):
    from whab.plugins import UserInterface

    storage = Plugin("one", "One", storage=lambda _: None, contract=PluginContract())
    other = replace(storage, id="two")
    with pytest.raises(CapabilityError) as raised:
        resolve_plugins((storage, other))
    assert raised.value.code == "conflicting_providers"
    ui = Plugin("visual", "Visual", ui=UserInterface(tmp_path, tmp_path, lambda *_: {}),
                contract=PluginContract(provides=(Capability("interface.ui"),)))
    with pytest.raises(CapabilityError) as raised:
        resolve_plugins((ui,))
    assert raised.value.code == "invalid_contract"


def test_description_reports_declared_and_legacy_compatibility_without_running_hooks():
    legacy = Plugin("legacy", "Legacy", insights=lambda _: pytest.fail("Must not execute"))
    declared = replace(plugin("declared", requires=(Requirement("reporting.insights"),)),
                       contract=PluginContract(requires=(Requirement("reporting.insights"),),
                                               lifecycle="live", authority="local", state_version=2))
    descriptions = {row.id: row for row in PluginRegistry((declared, legacy)).describe()}
    assert descriptions["legacy"].legacy and descriptions["legacy"].lifecycle == "restart"
    assert descriptions["legacy"].authority == "unknown"
    explicit = descriptions["declared"]
    assert not explicit.legacy and explicit.lifecycle == "live" and explicit.authority == "local"
    assert explicit.state_version == 2 and explicit.requirements == (Requirement("reporting.insights"),)
    assert explicit.capabilities == (Capability("reporting.insights"),)


def test_saved_state_paths_are_deterministic_and_do_not_execute_migrations():
    contract = PluginContract(state_version=5, state_upgrades=((3, 5), (1, 3), (2, 5), (1, 2), (1, 4), (4, 5)))
    assert state_upgrade_path(contract, 1) == (1, 2, 5)
    assert state_upgrade_path(contract, 5) == (5,)
    assert state_upgrade_path(contract, 3) == (3, 5)


@pytest.mark.parametrize("version", [1, 4])
def test_unsupported_saved_state_preserves_identity_and_has_actionable_error(version):
    with pytest.raises(CapabilityError, match="preserve the existing budget") as raised:
        state_upgrade_path(PluginContract(state_version=3), version, plugin_id="safe")
    assert raised.value.code == "unsupported_state" and raised.value.plugin_id == "safe"


def test_saved_state_rejects_invalid_contracts_and_versions():
    with pytest.raises(ValueError):
        state_upgrade_path(None, 1)
    with pytest.raises(ValueError):
        state_upgrade_path(PluginContract(), True)


def test_state_migration_callback_requires_explicit_metadata_and_callable():
    for fields in ({"contract": object()}, {"migrate_state": True, "contract": PluginContract()}, {"migrate_state": lambda *_: None}):
        with pytest.raises(ValueError):
            Plugin("safe", "Safe", insights=lambda _: (), **fields)
    descriptor = replace(plugin("safe"), migrate_state=lambda *_: None)
    assert callable(descriptor.migrate_state)
    assert Plugin("legacy", "Legacy", 1, insights=lambda _: ()).contract is None


def test_legacy_hook_inference_does_not_claim_optional_backend_features():
    descriptor = Plugin("report", "Report", insights=lambda _: (), export=lambda _: None)
    assert {row.name for row in contract_for(descriptor).provides} == {"reporting.insights", "reporting.export"}
    object.__setattr__(descriptor, "contract", {})
    with pytest.raises(ValueError):
        contract_for(descriptor)
