"""A local storage candidate is the primary, never a peer of the reference."""

import copy
import shutil
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import configurations, runtime_profiles
from whab_plugin_devkit import storage_profiles as profiles

from whab import plugins as sdk


def descriptor():
    return sdk.Plugin(
        "fictional-storage",
        "Fictional storage",
        storage=lambda _: None,
        contract=sdk.PluginContract(
            authority="local",
            lifecycle="live",
            provides=tuple(
                sdk.Capability(name)
                for name in ("storage.worker", "storage.authority", "storage.commands")
            ),
        ),
    )


def inputs():
    return (
        descriptor(),
        sdk.Plugin("sqlite", "Independent reference", storage=lambda _: None),
        [{"distribution": "whab", "version": "2.41.0", "filename": "host.whl", "sha256": "a" * 64}],
    )


def test_ten_storage_configs_resolve_one_primary_and_keep_reference_independent():
    candidate, reference, artifacts = inputs()
    manifests = profiles.sample_storage_configurations(candidate, reference, artifacts, seed=2026)
    assert manifests == profiles.sample_storage_configurations(
        candidate, reference, artifacts, seed=2026
    )
    assert len({frozenset(row["peers"]) for row in manifests}) == 10
    assert {row["fault"] for row in manifests} == set(profiles._FAULTS)
    for manifest in manifests:
        assert manifest["formatVersion"] == 4
        assert profiles.validate_storage_manifest(manifest) is manifest
        assert manifest["runtime"]["reference_role"] == "independent-conformance-only"
        assert "sqlite" not in manifest["discovery_order"] + manifest["configuration_order"]
        for selected in manifest["runtime"]["selections"]:
            sdk.resolve_plugins((candidate, *(profiles._peer(name) for name in selected)))
    assert (
        configurations.sample_configurations(candidate, reference, artifacts)[0]["runtime"][
            "applicability"
        ]
        == "not-requested"
    )
    assert (
        runtime_profiles.sample_runtime_configurations(candidate, reference, artifacts)[0][
            "runtime"
        ]["applicability"]
        == "supported"
    )
    assert runtime_profiles.validate_runtime_manifest(manifests[0]) is manifests[0]


@pytest.mark.parametrize(
    "changes",
    [{"combinations_count": 0}, {"combinations_count": True}, {"seed": "42"}, {"runtime": 1}],
)
def test_storage_sampling_rejects_unbounded_or_ambiguous_inputs(changes):
    candidate, reference, artifacts = inputs()
    with pytest.raises(ValueError):
        profiles.sample_storage_configurations(candidate, reference, artifacts, **changes)


def test_storage_sampling_checks_host_reference_and_available_constraints(monkeypatch):
    candidate, reference, artifacts = inputs()
    for invalid in (candidate, sdk.Plugin("other", "Wrong reference", storage=lambda _: None)):
        with pytest.raises(ValueError):
            profiles.sample_storage_configurations(candidate, invalid, artifacts)
    for invalid in ([], [*artifacts, *artifacts]):
        with pytest.raises(ValueError, match="one exact"):
            profiles.sample_storage_configurations(candidate, reference, invalid)
    monkeypatch.setattr(profiles, "_valid", lambda *_: False)
    with pytest.raises(ValueError, match="that many"):
        profiles.sample_storage_configurations(candidate, reference, artifacts)
    monkeypatch.setattr(profiles, "_valid", lambda _, names: len(names) >= 5)
    assert (
        profiles.sample_storage_configurations(
            candidate, reference, artifacts, combinations_count=1
        )[0]["sequence"]
        == "reorder-peers"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("formatVersion", True),
        ("formatVersion", 3),
        ("scope", "remote-client-lifecycle"),
        ("configuration_order", None),
        ("discovery_order", ["sqlite"]),
        ("runtime", None),
        ("unsupported", []),
        ("fault", "unimplemented-private-fault"),
    ],
)
def test_storage_reader_refuses_changed_schemas_or_unrecorded_claims(field, value):
    manifest = profiles.sample_storage_configurations(*inputs(), combinations_count=1)[0]
    manifest[field] = value
    with pytest.raises(ValueError):
        profiles.validate_storage_manifest(manifest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile", "native-local-v1"),
        ("minimum_host", "2.36.0"),
        ("host", "2.40.0"),
        ("requested", 1),
        ("applicability", "supported-ish"),
        ("reference_role", "also-a-primary"),
        ("steps", []),
        ("selections", []),
        ("selections", [[], [], []]),
        ("selections", [None, [], None]),
    ],
)
def test_storage_reader_binds_applicability_and_every_transition(field, value):
    manifest = profiles.sample_storage_configurations(*inputs(), combinations_count=1)[0]
    manifest["runtime"][field] = value
    with pytest.raises(ValueError):
        profiles.validate_storage_manifest(manifest)


def test_storage_reader_rejects_unknown_or_duplicate_middle_peers():
    original = profiles.sample_storage_configurations(*inputs(), combinations_count=1)[0]
    for middle in (None, ["unreviewed"], [1], ["synthetic-report-a", "synthetic-report-a"]):
        changed = copy.deepcopy(original)
        changed["runtime"]["selections"][1] = middle
        with pytest.raises(ValueError):
            profiles.validate_storage_manifest(changed)


class FakeNative:
    def __init__(self, storage, selection, *, storage_candidate):
        assert storage == storage_candidate.id and storage not in selection
        self.storage, self.selection, self.cleanups = storage, selection, 0
        self.home = None
        self.process = object()
        self.accounts, self.operations = [], []
        self.transition_timeout = 180
        self.active = {"environment": "fictional-worker-digest"}
        self.closed = False
        self.activated = False
        self.restarts = 0

    def __enter__(self):
        from pathlib import Path
        from tempfile import mkdtemp

        self.home = Path(mkdtemp(prefix="fictional-storage-profile-"))
        return self

    def __exit__(self, *_):
        self.stop()
        shutil.rmtree(self.home)

    def start(self):
        self.process = object()
        self.restarts += 1

    def stop(self, *, kill=False):
        self.cleanups += 1
        self.process = None

    def operation(self, path, identifier):
        assert path == "/api/plugins/runtime"
        return {"state": "complete"}

    def wait(self, observe, *, timeout):
        assert timeout == self.transition_timeout
        return observe()

    def expect(self, method, path, body=None, *, status=200):
        if status >= 400:
            return {}
        if path == "/api/health":
            return {"version": "2.41.0"}
        if path == "/api/accounts":
            if self.accounts:
                assert self.activated
            if len(self.accounts) == 2:
                assert self.restarts == 1
            identifier = f"fictional-wallet-{len(self.accounts)}"
            self.accounts.append({"id": identifier, "balance": body["balance"]})
            return {"accountId": identifier}
        if path == "/api/storage/status":
            return {
                "storage": self.storage,
                "code": {"mode": "worker", "active": self.active, "uncertain": False},
            }
        if path == "/api/plugins/management/transition":
            self.selection = body["plugins"]
            return {"id": "transition"}
        if path == "/api/plugins/management/upgrade":
            self.activated = True
            if body["wheels"][0]["sha256"] == "0" * 64:
                self.operations = [{"id": "transition", "state": "failed"}]
            return {"id": "transition"}
        if path == "/api/plugins":
            return {"plugins": [{"id": row} for row in [self.storage, *self.selection]]}
        if path == "/api/plugins/runtime":
            return {"operations": self.operations}
        if path.startswith("/api/workspace?"):
            return {"accounts": self.accounts.copy()}
        raise AssertionError("Unreviewed runtime call")


def lightweight(monkeypatch):
    monkeypatch.setattr(profiles, "storage_conformance", lambda _: {"status": "passed"})
    monkeypatch.setattr(profiles, "assert_storage_readiness", lambda _: {"status": "passed"})
    monkeypatch.setattr(profiles, "reference_conformance", lambda _: ["reference"])
    monkeypatch.setattr(profiles, "storage_platform", lambda _: "supported")
    monkeypatch.setattr(
        profiles, "api_worker_wheels", lambda *_: [{"path": "approved.whl", "sha256": "a" * 64}]
    )


def test_controlled_storage_runtime_executes_all_three_faults_and_both_public_dispatchers(
    monkeypatch,
):
    lightweight(monkeypatch)
    candidate, reference, artifacts = inputs()
    manifests = profiles.sample_storage_configurations(
        candidate, reference, artifacts, combinations_count=3
    )
    for manifest in manifests:
        result = runtime_profiles.replay_runtime_configuration(
            manifest, candidate, reference, native=FakeNative
        )
        assert result["runtime"]["status"] == "passed"
        assert result["runtime"]["cleanup_complete"] is True
        assert result["runtime"]["acknowledged_writes"] == 3
        assert result["runtime"]["extensions"]["copies"].startswith("not-applicable")
        assert "sqlite" not in result["resolved"]
    no_runtime = profiles.sample_storage_configurations(
        candidate, reference, artifacts, runtime=False, combinations_count=1
    )[0]
    assert (
        configurations.replay_configuration(no_runtime, candidate, reference)["runtime"]["status"]
        == "not-requested"
    )


def test_storage_profile_refuses_changed_descriptors_and_never_counts_unsupported_platform_as_native_pass(
    monkeypatch,
):
    lightweight(monkeypatch)
    candidate, reference, artifacts = inputs()
    manifest = profiles.sample_storage_configurations(
        candidate, reference, artifacts, combinations_count=1
    )[0]
    with pytest.raises(ValueError):
        profiles.replay_storage_configuration(manifest, reference, reference)
    monkeypatch.setattr(
        profiles, "storage_platform", lambda _: "not-applicable: package requires POSIX"
    )
    result = profiles.replay_storage_configuration(manifest, candidate, reference)
    assert result["runtime"]["status"].startswith("not-applicable")
    monkeypatch.setattr(profiles, "storage_platform", lambda _: "supported")
    declaration = profiles.storage_declaration(candidate)
    monkeypatch.setattr(
        profiles,
        "storage_declaration",
        lambda _: {**declaration, "native": "not-applicable: legacy"},
    )
    assert (
        profiles.replay_storage_configuration(manifest, candidate, reference)["runtime"]["status"]
        == "not-applicable: legacy"
    )


def test_storage_runtime_extensions_only_execute_advertised_public_contracts(monkeypatch):
    from whab_plugin_devkit import runtime_profiles

    calls = []
    monkeypatch.setattr(
        runtime_profiles, "_migrate", lambda _, destination: calls.append(("migrate", destination))
    )
    monkeypatch.setattr(
        runtime_profiles, "_copy_saved", lambda _, identifier: calls.append(("saved", identifier))
    )
    runtime = SimpleNamespace(
        expect=lambda method, path, *_, **__: {"preview_id": "preview", "id": "copy"},
        operation=lambda *args: calls.append(("operation", args)),
    )
    declaration = {"copies": "not-applicable", "same_device_transfer": "supported"}
    profiles._extensions(runtime, descriptor(), declaration)
    assert calls == [("migrate", "sqlite"), ("migrate", "fictional-storage")]
    calls.clear()
    declaration["copies"] = "supported"
    profiles._extensions(runtime, descriptor(), declaration)
    assert calls[-1] == ("migrate", "fictional-storage") and calls[0] == ("saved", "copy")


def test_invalid_runtime_observations_are_failures():
    with pytest.raises(AssertionError):
        profiles._check(False)


def test_cli_checks_and_replays_version_four_without_reinterpreting_prior_schemas(
    tmp_path, monkeypatch, capsys
):
    from whab_plugin_devkit import cli

    candidate, reference, artifacts = inputs()
    selected = []
    monkeypatch.setattr(
        cli,
        "_selected",
        lambda *args, **kwargs: (
            selected.append((args, kwargs)) or (candidate, reference, artifacts)
        ),
    )
    calls = []
    monkeypatch.setattr(
        profiles,
        "replay_storage_configuration",
        lambda manifest, *_args, **kwargs: (
            calls.append((manifest, kwargs)) or {"runtime": {"status": "passed"}}
        ),
    )
    output = tmp_path / "storage-manifests"
    assert (
        cli.main(
            [
                "check",
                "--plugin",
                candidate.id,
                "--runtime",
                "--combinations",
                "1",
                "--wheelhouse",
                str(tmp_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    path = output / "configuration-00.json"
    assert calls[-1][0]["formatVersion"] == 4
    assert cli.main(["replay", str(path), "--wheelhouse", str(tmp_path)]) == 0
    assert calls[-1][1] == {"wheelhouse": tmp_path}
    assert selected[-1][1] == {"runtime": True}
    assert "passed" in capsys.readouterr().out
