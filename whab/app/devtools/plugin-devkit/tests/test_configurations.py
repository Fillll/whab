import copy
import json
from dataclasses import replace
from importlib import metadata
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import configurations as configurations
from whab_plugin_devkit.configurations import (
    deterministic_negatives,
    replay_configuration,
    sample_configurations,
    validate_manifest,
)

from whab.plugins import (
    BankLogos,
    Categorization,
    ExportTable,
    FileImport,
    Insight,
    Plugin,
    PluginContract,
    Requirement,
    WorkspaceImport,
)
from whab.plugins.contracts import CapabilityError


@pytest.fixture
def candidate():
    return Plugin("candidate", "Fictional", insights=lambda _: (Insight("Count", "1"),),
                  export=lambda _: ExportTable(("minor_units",), ((125,),)))


@pytest.fixture
def reference():
    distribution = metadata.distribution("whab_plugin_sqlite")
    entry, = (entry for entry in distribution.entry_points if entry.group == "whab.plugins")
    return entry.load()


@pytest.fixture
def artifacts():
    return [{"distribution": "fictional", "version": "1", "filename": "fictional-1-py3-none-any.whl", "sha256": "0" * 64}]


def test_ten_distinct_configurations_are_reproducible_and_exercise_actual_reference_contracts(candidate, reference, artifacts):
    manifests = sample_configurations(candidate, reference, artifacts, seed=2026)
    assert manifests == sample_configurations(candidate, reference, artifacts, seed=2026)
    assert manifests != sample_configurations(candidate, reference, artifacts, seed=2027)
    assert len({tuple(manifest["peers"]) for manifest in manifests}) == 10
    for manifest in manifests:
        restored = configurations._parse(json.dumps(manifest))
        assert validate_manifest(restored) == manifest
        result = replay_configuration(restored, candidate, reference)
        assert "candidate" in result["resolved"] and "sqlite" in result["resolved"]
        assert result["candidate_hooks"] == ["insights", "export"]
        assert result["peer_hooks"]
        assert all(case["status"] == "passed" or (case["capability"] in {"native-replica", "durable-commands"} and case["status"] == "not-applicable")
                   for case in result["conformance"])
        assert len(result["deterministic_negatives"]) == 5
        assert "copies" in manifest["unsupported"] and "migration" in manifest["unsupported"]


def test_legacy_reference_reports_replica_not_applicable_without_hiding_broken_advertisements(candidate, reference, artifacts):
    legacy = replace(reference, contract=None)
    manifest = sample_configurations(candidate, legacy, artifacts, combinations_count=1)[0]
    result = replay_configuration(manifest, candidate, legacy)
    replicas = [case for case in result["conformance"] if case["capability"] in {"native-replica", "durable-commands"}]
    assert [case["status"] for case in replicas] == ["not-applicable", "not-applicable"]
    assert not any(case["status"] == "failed" for case in result["conformance"])


@pytest.mark.parametrize(("count", "seed"), [(True, 42), (0, 42), (33, 42), (10, True), (10, "42")])
def test_invalid_sampling_bounds_do_not_create_duplicate_or_unreplayable_runs(candidate, reference, artifacts, count, seed):
    with pytest.raises(ValueError):
        sample_configurations(candidate, reference, artifacts, combinations_count=count, seed=seed)


def test_unsatisfied_candidate_constraints_cannot_be_filled_by_counting_duplicates(candidate, reference, artifacts):
    candidate = replace(candidate, contract=PluginContract(requires=(Requirement("extension.not-provided"),)))
    with pytest.raises(ValueError, match="do not permit"):
        sample_configurations(candidate, reference, artifacts)


@pytest.mark.parametrize(("key", "value"), [
    ("formatVersion", True), ("formatVersion", 2), ("scope", "live"), ("seed", "42"), ("index", 32),
    ("python", "not-current"), ("candidate", "../escape"), ("candidate", None), ("reference", "community-plugin"),
    ("peers", ["unknown"]), ("peers", ["synthetic-report-a", "synthetic-report-a"]), ("peers", [{}]),
    ("discovery_order", []), ("configuration_order", [{}]), ("sequence", "live-upgrade"), ("fault", "disk-full"),
    ("unsupported", []), ("artifacts", []), ("artifacts", [{"arbitrary": "fields"}]),
    ("replay", "run arbitrary shell"),
])
def test_manifest_schema_refuses_unknown_runtime_claims_order_and_artifacts(candidate, reference, artifacts, key, value):
    manifest = sample_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    manifest[key] = value
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_manifest_rejects_duplicate_identity_and_invalid_hashes(candidate, reference, artifacts):
    manifest = sample_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    for field, value in (("sha256", "wrong"), ("filename", "../escape.whl"), ("version", 1), ("distribution", "x" * 201)):
        bad = copy.deepcopy(manifest)
        bad["artifacts"][0][field] = value
        with pytest.raises(ValueError):
            validate_manifest(bad)
    manifest["artifacts"] *= 2
    with pytest.raises(ValueError):
        validate_manifest(manifest)
    with pytest.raises(ValueError):
        validate_manifest({})
    with pytest.raises(ValueError):
        configurations._parse('{"seed":1,"seed":2}')
    with pytest.raises(ValueError):
        configurations._parse('{"seed":NaN}')


def test_replay_rejects_a_changed_candidate_identity(candidate, reference, artifacts):
    manifest = sample_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    with pytest.raises(ValueError, match="do not match"):
        replay_configuration(manifest, replace(candidate, id="different"), reference)


def test_all_deterministic_negatives_use_real_public_resolution_outcomes():
    assert deterministic_negatives() == ["missing_requirement", "dependency_cycle", "incompatible_requirement", "conflicting_providers", "unsupported_state"]


def test_negative_runner_detects_missing_or_wrong_rejections(monkeypatch):
    monkeypatch.setattr(configurations, "resolve_plugins", lambda _: ())
    with pytest.raises(ValueError, match="was accepted"):
        deterministic_negatives()

    def incorrect(_):
        raise CapabilityError("Fictional", code="wrong")

    monkeypatch.setattr(configurations, "resolve_plugins", incorrect)
    with pytest.raises(ValueError, match="unexpected"):
        deterministic_negatives()


@pytest.mark.parametrize("plugin", [
    Plugin("report", "Broken", insights=lambda _: (_ for _ in ()).throw(ValueError("synthetic-private-marker"))),
    Plugin("brand", "Broken", bank_logos=BankLogos(lambda _: 1, lambda _: None)),
    Plugin("brand", "Broken", bank_logos=BankLogos(lambda _: None, lambda _: object())),
    Plugin("file", "Broken", file_import=FileImport(lambda _: None)),
    Plugin("unsupported", "Unsupported", storage=lambda _: None),
])
def test_broken_advertised_candidate_hooks_fail_instead_of_being_skipped(plugin):
    with pytest.raises(ValueError):
        configurations.exercise_candidate(plugin)


@pytest.mark.parametrize("failure", ["nonempty", "endless", "empty-prediction", "wrong-prediction"])
def test_categorizer_checks_fresh_namespaces_cooperative_training_and_actual_predictions(failure):
    closed = []

    def train(*_):
        try:
            if failure == "endless":
                while True:
                    yield {"progress": 0}
            return {"model": "fictional"}
        finally:
            closed.append(True)

    engine = SimpleNamespace(index=lambda: {"models": ["unexpected"] if failure == "nonempty" else []},
                             train=train, save=lambda *_: {"id": "fictional"}, select=lambda _: None,
                             predict=lambda _: [] if failure == "empty-prediction" else [{"categoryId": "wrong"}])
    plugin = Plugin("categorizer", "Fictional", categorization=Categorization(lambda _: engine))
    with pytest.raises(ValueError):
        configurations.exercise_candidate(plugin)
    assert closed == ([] if failure == "nonempty" else [True])


def test_sampling_uses_ordering_when_every_removal_would_break_candidate_requirements(candidate, reference, artifacts, monkeypatch):
    monkeypatch.setattr(configurations, "_PEERS", ("synthetic-brand-a",))
    candidate = replace(candidate, contract=PluginContract(requires=(Requirement("branding.bank"),)))
    manifest, = sample_configurations(candidate, reference, artifacts, combinations_count=1)
    assert manifest["sequence"] == "reorder-peers"
    replay_configuration(manifest, candidate, reference)
    manifest["sequence"] = "disable-enable-peer"
    with pytest.raises(ValueError, match="cannot remove"):
        replay_configuration(manifest, candidate, reference)


def test_replay_detects_order_dependent_resolver_regressions(candidate, reference, artifacts, monkeypatch):
    manifest, = sample_configurations(candidate, reference, artifacts, combinations_count=1)
    monkeypatch.setattr(configurations, "resolve_plugins", lambda selection: tuple(selection))
    with pytest.raises(ValueError, match="configuration order"):
        replay_configuration(manifest, candidate, reference)


def test_report_fault_must_be_attributed_by_the_public_registry(candidate, reference, artifacts, monkeypatch):
    manifest, = sample_configurations(candidate, reference, artifacts, combinations_count=1)
    manifest["fault"] = "report-hook"
    actual = configurations.PluginRegistry.insights

    def suppress_fault(registry, snapshot):
        if registry.describe()[0].id == "synthetic-fault":
            return [SimpleNamespace(error=None)]
        return actual(registry, snapshot)

    monkeypatch.setattr(configurations.PluginRegistry, "insights", suppress_fault)
    with pytest.raises(ValueError, match="was not attributed"):
        replay_configuration(manifest, candidate, reference)


@pytest.mark.parametrize("failure", ["accepted", "mutated"])
def test_import_fault_detects_acceptance_and_non_atomic_public_writes(candidate, reference, artifacts, failure):
    manifest, = sample_configurations(candidate, reference, artifacts, combinations_count=1)
    manifest["fault"] = "import-atomicity"
    original = reference.storage

    class FaultyImport:
        def __init__(self, context):
            self.backend = original(context)

        def __getattr__(self, name):
            return getattr(self.backend, name)

        def import_workspace(self, workspace, source, digest, imported):
            if source != "synthetic-fault":
                return self.backend.import_workspace(workspace, source, digest, imported)
            if failure == "accepted":
                return None
            valid = WorkspaceImport(({"id": "fictional-account", "name": "Fictional", "institution": "", "type": "depository",
                                      "balance": 1, "source": "manual", "stale": False, "balanceUnknown": False},), (), (), (), "2026-10")
            self.backend.import_workspace(workspace, source, digest, valid)
            raise ValueError("Injected failure after public write")

    with pytest.raises(ValueError, match="was accepted|changed its fictional ledger"):
        replay_configuration(manifest, candidate, replace(reference, storage=FaultyImport))


def test_mixed_legacy_interface_keeps_its_public_hooks_without_executing_a_router(candidate, reference, artifacts):
    from whab.plugins import Interface

    legacy = Interface(lambda _: pytest.fail("A legacy router factory must never execute in hook conformance"))
    plugin = replace(candidate, interface=legacy)
    manifest, = sample_configurations(plugin, reference, artifacts, combinations_count=1)
    assert replay_configuration(manifest, plugin, reference)["candidate_hooks"] == ["insights", "export"]
    with pytest.raises(ValueError, match="declarative API"):
        configurations.exercise_candidate(Plugin("legacy-only", "Legacy", interface=legacy))
