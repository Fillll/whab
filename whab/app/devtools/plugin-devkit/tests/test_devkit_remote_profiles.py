"""Remote manifests never promote local migration/copy or unknown-write claims."""

import copy
import shutil
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import configurations, runtime_profiles
from whab_plugin_devkit import remote_profiles as profiles

from whab import plugins as sdk

from .test_devkit_remote_client import descriptor


def inputs():
    return descriptor(), sdk.Plugin("sqlite", "Public reference slot", storage=lambda _: None), [
        {"distribution": "whab", "version": "2.40.0", "filename": "host.whl", "sha256": "a" * 64}]


def test_ten_remote_configurations_use_actual_distinct_peer_sets_and_keep_reference_separate():
    candidate, reference, artifacts = inputs()
    if candidate is None:
        assert profiles.MINIMUM_REMOTE_HOST == "2.40.0"
        return
    manifests = profiles.sample_remote_configurations(candidate, reference, artifacts, seed=2026)
    assert manifests == profiles.sample_remote_configurations(candidate, reference, artifacts, seed=2026)
    assert len({frozenset(row["peers"]) for row in manifests}) == 10
    assert {row["fault"] for row in manifests} == set(profiles._FAULTS)
    for row in manifests:
        assert row["formatVersion"] == 3 and profiles.validate_remote_manifest(row) is row
        assert row["runtime"]["reference_role"] == "fictional-api-authority-backend"
        assert "sqlite" not in row["discovery_order"] + row["configuration_order"]
        for selected in row["runtime"]["selections"]:
            sdk.resolve_plugins((candidate, *(profiles._peer(name) for name in selected)))
    assert configurations.sample_configurations(candidate, reference, artifacts)[0]["runtime"]["applicability"] == "not-requested"
    assert runtime_profiles.sample_runtime_configurations(candidate, reference, artifacts)[0]["runtime"]["applicability"] == "supported"
    assert runtime_profiles.validate_runtime_manifest(manifests[0]) is manifests[0]


@pytest.mark.parametrize("changes", [{"combinations_count": 0}, {"combinations_count": True}, {"seed": "42"}, {"runtime": 1}])
def test_remote_sampler_rejects_unbounded_or_ambiguous_configuration_requests(changes):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    with pytest.raises(ValueError):
        profiles.sample_remote_configurations(candidate, reference, artifacts, **changes)


def test_remote_sampler_checks_reference_host_and_available_constraints(monkeypatch):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    for invalid in (SimpleNamespace(id=candidate.id), SimpleNamespace(id="json")):
        with pytest.raises(ValueError):
            profiles.sample_remote_configurations(candidate, invalid, artifacts)
    for invalid in ([], [*artifacts, *artifacts]):
        with pytest.raises(ValueError, match="one exact"):
            profiles.sample_remote_configurations(candidate, reference, invalid)
    monkeypatch.setattr(profiles, "_valid", lambda *_: False)
    with pytest.raises(ValueError, match="that many"):
        profiles.sample_remote_configurations(candidate, reference, artifacts)
    monkeypatch.setattr(profiles, "_valid", lambda _, names: len(names) >= 5)
    assert profiles.sample_remote_configurations(candidate, reference, artifacts, combinations_count=1)[0]["sequence"] == "reorder-peers"


@pytest.mark.parametrize("field,value", [
    ("formatVersion", True), ("formatVersion", 2), ("scope", "runtime-lifecycle-storage"),
    ("configuration_order", None), ("discovery_order", ["sqlite"]),
    ("runtime", None), ("unsupported", []), ("fault", "private-commit-injection"),
])
def test_remote_reader_refuses_schema_changes_and_unrecorded_fault_claims(field, value):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    manifest = profiles.sample_remote_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    manifest[field] = value
    with pytest.raises(ValueError):
        profiles.validate_remote_manifest(manifest)


@pytest.mark.parametrize("field,value", [
    ("profile", "native-local-v1"), ("minimum_host", "2.39.0"), ("host", "2.41.0"), ("requested", 1),
    ("applicability", "supported-ish"), ("reference_role", "same-authority"), ("steps", []),
    ("selections", []), ("selections", [[], [], []]), ("selections", [None, [], None]),
])
def test_remote_reader_binds_exact_applicability_and_all_transition_fields(field, value):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    manifest = profiles.sample_remote_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    manifest["runtime"][field] = value
    with pytest.raises(ValueError):
        profiles.validate_remote_manifest(manifest)


@pytest.mark.parametrize("middle", [None, ["unknown"], [1], ["synthetic-report-a", "synthetic-report-a"]])
def test_remote_reader_rejects_foreign_or_duplicate_peer_selections(middle):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    manifest = profiles.sample_remote_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    manifest["runtime"]["selections"][1] = middle
    with pytest.raises(ValueError):
        profiles.validate_remote_manifest(manifest)


class PublicRuntime:
    def __init__(self, candidate, selected, *, root, host, fault=None):
        self.candidate, self.selection, self.host, self.fault = candidate, selected, host, fault
        self.home = root / "client"
        self.home.mkdir()
        server_home = root / "server"
        server_home.mkdir()
        self.server = SimpleNamespace(home=server_home, process=object(), cleanups=0)
        self.process, self.worker, self.cleanups, self.reopened = object(), None, 0, False
        self.operations, self.transition_timeout = [], 180
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.stop()
        self.server.process = None
        self.server.cleanups += 1
        shutil.rmtree(self.home)
        shutil.rmtree(self.server.home)
    def stop(self, **_):
        self.cleanups += int(self.process is not None)
        self.process = None
    def start(self):
        self.process, self.reopened = object(), True
    def operation(self, *_):
        pass
    def wait(self, observe, *, timeout):
        assert timeout == 180
        return observe()
    def expect(self, method, path, body=None, **options):
        if options.get("status", 200) not in {200, 202}:
            return {}
        if path == "/api/health":
            return {"version": self.host}
        if path.endswith("/upgrade"):
            failed = body["wheels"][0]["sha256"] == "0" * 64
            self.operations = [{"id": "operation", "state": "failed" if failed else "complete"}]
            if not failed:
                self.worker = None if self.fault == "worker" else {"version": "0.1.0", "environment": "a" * 64}
            return {"id": "operation"}
        if path == "/api/storage/remote/status":
            return {"worker": self.worker}
        if path.endswith("/transition"):
            self.selection = body["plugins"]
            return {"id": "selection"}
        if path == "/api/plugins/runtime":
            return {"operations": self.operations}
        if path == "/api/plugins":
            return {"plugins": [{"id": name} for name in [self.candidate.id, *self.selection]]}
        assert path.startswith("/api/workspace?")
        return {"accounts": [{"balance": int(self.fault == "changed" and self.reopened)}]}


@pytest.mark.parametrize("fault", profiles._FAULTS)
def test_remote_replay_uses_the_public_coordinator_and_retains_exact_worker(tmp_path, monkeypatch, fault):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    manifest = next(row for row in profiles.sample_remote_configurations(candidate, reference, artifacts) if row["fault"] == fault)
    monkeypatch.setattr(profiles, "api_worker_wheels", lambda *_: [{"path": "/approved/candidate.whl", "sha256": "a" * 64}])
    monkeypatch.setattr(profiles, "reference_conformance", lambda _: [{"status": "passed"}])
    monkeypatch.setattr(profiles, "assert_remote_client", lambda *_: {"read_write": "passed"})
    monkeypatch.setattr(profiles, "assert_lost_remote_result", lambda *_: {"lost_public_result": "passed"})
    def native(plugin, selection):
        return PublicRuntime(plugin, selection, root=tmp_path, host=artifacts[0]["version"])
    result = profiles.replay_remote_configuration(manifest, candidate, reference, wheelhouse=tmp_path, native=native)
    assert result["runtime"]["status"] == "passed" and result["runtime"]["cleanup_complete"]
    assert result["runtime"]["cleanup_count"] == 3
    assert result["candidate_hooks"] == ["storage.remote-declaration"]
    assert result["runtime"]["worker"] == {"version": "0.1.0", "environment": "a" * 64}


def test_old_host_and_unrequested_profiles_never_count_as_native_runtime_passes(monkeypatch):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    monkeypatch.setattr(profiles, "reference_conformance", lambda _: [])
    for host, requested in (("2.39.0", True), ("2.40.0", False)):
        older = copy.deepcopy(artifacts)
        older[0]["version"] = host  # Schema fixture, never a claimed installed artifact.
        manifest = profiles.sample_remote_configurations(candidate, reference, older, combinations_count=1, runtime=requested)[0]
        result = profiles.replay_remote_configuration(manifest, candidate, reference, native=lambda *_: pytest.fail("No inapplicable runtime"))
        assert result["runtime"]["status"] != "passed"
    with pytest.raises(ValueError, match="identities"):
        profiles.replay_remote_configuration(manifest, candidate, SimpleNamespace(id="different"))
    assert configurations.replay_configuration(manifest, candidate, reference)["runtime"]["status"] == "not-requested"
    assert runtime_profiles.replay_runtime_configuration(manifest, candidate, reference)["runtime"]["status"] == "not-requested"


def test_cli_replays_the_explicit_remote_schema_with_exact_artifacts(tmp_path, monkeypatch, capsys):
    import json

    from whab_plugin_devkit import cli

    candidate, reference, artifacts = inputs()
    if candidate is None:
        return
    manifest = profiles.sample_remote_configurations(candidate, reference, artifacts, combinations_count=1, runtime=False)[0]
    path = tmp_path / "configuration-00.json"
    path.write_text(json.dumps(manifest))
    calls = []
    monkeypatch.setattr(cli, "_selected", lambda *args, **options: (candidate, reference, artifacts))
    monkeypatch.setattr(profiles, "replay_remote_configuration", lambda *args, **options: calls.append(options) or {"runtime": {"status": "not-requested"}})
    assert cli.main(["replay", str(path), "--wheelhouse", str(tmp_path)]) == 0
    assert calls == [{"wheelhouse": tmp_path}] and "not-requested" in capsys.readouterr().out


def test_remote_failure_observation_cannot_turn_success_or_missing_status_into_failure_proof():
    runtime = SimpleNamespace(transition_timeout=180, expect=lambda *_: {"operations": []}, wait=lambda observe, **_: observe())
    assert profiles._failed_operation(runtime, "missing") is None
    runtime.expect = lambda *_: {"operations": [{"id": "operation", "state": "complete"}]}
    with pytest.raises(AssertionError):
        profiles._failed_operation(runtime, "operation")
    with pytest.raises(AssertionError):
        profiles._check(False)
