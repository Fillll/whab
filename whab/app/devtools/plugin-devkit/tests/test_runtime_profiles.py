"""Real public HTTPS runtime profiles and bounded process/protocol failure cases."""

import copy
import json
import subprocess
from importlib import metadata
from types import SimpleNamespace

import pytest
from packaging.version import Version
from whab_plugin_devkit import runtime_harness as harness
from whab_plugin_devkit import runtime_profiles as profiles

from whab.plugins import Insight, Plugin, PluginContract


def inputs():
    entries = {entry.name: entry for entry in metadata.entry_points(group="whab.plugins")}
    host = metadata.version("whab")
    if Version(host) >= Version("2.32.0") and "synthetic-report-a" not in entries:
        raise ValueError("Direct source toolkit tests require scripts/install.py --runtime-fixtures; installed gates supply the reviewed fixture wheel automatically.")
    candidate = (entries["synthetic-report-a"].load() if Version(host) >= Version("2.32.0") else
                 Plugin("synthetic-report-a", "Fictional", insights=lambda _: (Insight("Fictional", "1"),), contract=PluginContract(lifecycle="live")))
    artifacts = [{"distribution": "whab", "version": host, "filename": "whab-fixture.whl", "sha256": "0" * 64}]
    return candidate, entries["sqlite"].load(), artifacts


def test_ten_actual_peer_sets_reproduce_directions_sequences_and_precise_faults():
    candidate, reference, artifacts = inputs()
    manifests = profiles.sample_runtime_configurations(candidate, reference, artifacts, seed=2026)
    assert manifests == profiles.sample_runtime_configurations(candidate, reference, artifacts, seed=2026)
    assert len({tuple(row["peers"]) for row in manifests}) == 10
    assert {row["runtime"]["primary"] for row in manifests} == {"sqlite", "json"}
    assert len({row["runtime"]["sequence"] for row in manifests}) == 3
    assert len({row["runtime"]["fault"] for row in manifests}) == 4
    for manifest in manifests:
        assert profiles.validate_runtime_manifest(json.loads(json.dumps(manifest))) == manifest
    # Four real native processes cover every fault, both authority directions,
    # candidate removal/re-enable, graceful reopen and acknowledged-write SIGKILL.
    for manifest in manifests[:4]:
        result = profiles.replay_runtime_configuration(manifest, candidate, reference)
        expected = "passed" if Version(artifacts[0]["version"]) >= Version("2.32.0") else "not-applicable"
        assert result["runtime"]["status"] == expected
        if expected == "passed":
            assert result["runtime"]["cleanup_count"] == 2
    older = copy.deepcopy(artifacts)
    older[0]["version"] = "2.22.0"
    manifest = profiles.sample_runtime_configurations(candidate, reference, older, combinations_count=1)[0]
    result = profiles.replay_runtime_configuration(manifest, candidate, reference, native=lambda *_: pytest.fail("Old host must not launch a runtime profile"))
    assert result["runtime"]["status"] == "not-applicable"


@pytest.mark.parametrize("field,value", [
    ("profile", "unknown"), ("minimum_host", "2.22.0"), ("host", "0.0.0"),
    ("applicability", "invented"), ("candidate_applicability", "all-families"), ("primary", "community"), ("destination", "unknown"),
    ("steps", []), ("fault", "private-commit-boundary"), ("sequence", "execute arbitrary code"),
    ("selections", []), ("selections", [["unknown"]] * 3),
    ("selections", [["synthetic-report-a", "synthetic-report-a"]] * 3),
    ("candidate_selection", "always-supported"),
])
def test_runtime_manifest_rejects_unrecorded_execution_and_false_claims(field, value):
    candidate, reference, artifacts = inputs()
    manifest = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    manifest["runtime"][field] = value
    with pytest.raises(ValueError):
        profiles.validate_runtime_manifest(manifest)


def test_runtime_manifest_checks_version_required_fields_and_candidate_applicability():
    candidate, reference, artifacts = inputs()
    original = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    for mutate in (
        lambda row: row.update(formatVersion=True), lambda row: row.update(scope="unknown"),
        lambda row: row.update(unsupported=[]), lambda row: row["runtime"].pop("fault"),
        lambda row: row["runtime"].update(candidate_selection=("disable-enable" if original["runtime"]["candidate_selection"] != "disable-enable" else "not-applicable: candidate profile constraints")),
    ):
        value = copy.deepcopy(original)
        mutate(value)
        with pytest.raises(ValueError):
            profiles.validate_runtime_manifest(value)
    with pytest.raises(ValueError, match="exact host"):
        profiles.sample_runtime_configurations(candidate, reference, [], combinations_count=1)
    restart = Plugin("synthetic-report-a", "Restart", insights=lambda _: (), contract=PluginContract())
    assert profiles.sample_runtime_configurations(restart, reference, artifacts, combinations_count=1)[0]["runtime"]["candidate_selection"].startswith("not-applicable")


def test_native_harness_restricts_environment_and_cleans_failed_startup(monkeypatch):
    monkeypatch.setenv("UNCONVENTIONAL_PRIVATE_CREDENTIAL", "fictional-secret")
    monkeypatch.setenv("WHAB_BUDGET", "/private/not-read")
    with pytest.raises(ValueError):
        harness.NativeRuntime("community", ())
    runtime = harness.NativeRuntime("sqlite", ())
    assert "UNCONVENTIONAL_PRIVATE_CREDENTIAL" not in runtime.environment()
    assert "WHAB_BUDGET" not in runtime.environment()
    assert runtime.environment()["ALLOW_LIVE_PLAID"] == "false"
    home = runtime.home
    monkeypatch.setattr(runtime, "start", lambda: (_ for _ in ()).throw(RuntimeError("fictional startup failure")))
    with pytest.raises(RuntimeError):
        runtime.__enter__()
    assert not home.exists()


def test_native_harness_rejects_duplicate_start_timeout_wrong_status_and_failed_operation(monkeypatch):
    runtime = harness.NativeRuntime("json", (), timeout=0.01)
    try:
        runtime.process = object()
        with pytest.raises(ValueError):
            runtime.start()
        runtime.process = None
        ticks = iter((0, 1))
        monkeypatch.setattr(harness.time, "monotonic", lambda: next(ticks, 1))
        with pytest.raises(TimeoutError):
            runtime.wait(lambda: None)
        monkeypatch.setattr(runtime, "request", lambda *_: (409, {}))
        with pytest.raises(AssertionError):
            runtime.expect("GET", "/api/health")
        monkeypatch.setattr(runtime, "expect", lambda *_: {"operations": [{"id": "test", "state": "failed"}]})
        with pytest.raises(AssertionError):
            runtime.operation("/api/storage/status", "test")
    finally:
        runtime.__exit__(None, None, None)


@pytest.mark.parametrize("platform,kill,race,timeout", [
    ("posix", False, False, False), ("posix", True, True, False),
    ("nt", True, False, False), ("nt", False, False, True),
])
def test_native_cleanup_targets_only_owned_process_and_handles_exit_races(monkeypatch, platform, kill, race, timeout):
    runtime = harness.NativeRuntime("sqlite", ())
    calls = []
    waits = iter((subprocess.TimeoutExpired("fictional", 5), None)) if timeout else iter((None,))
    def wait(**_):
        result = next(waits)
        if result is not None:
            raise result
    process = SimpleNamespace(pid=12345, wait=wait, kill=lambda: calls.append("kill"), terminate=lambda: calls.append("terminate"))
    def group(pid, requested):
        assert pid == 12345
        calls.append(requested)
        if race:
            raise ProcessLookupError()
    monkeypatch.setattr(harness, "os", SimpleNamespace(name=platform, killpg=group))
    runtime.process = process
    try:
        runtime.stop(kill=kill)
        assert runtime.process is None and runtime.cleanups == 1
        assert calls
    finally:
        runtime.__exit__(None, None, None)


def test_native_http_response_is_bounded_and_connection_closes_after_invalid_json(monkeypatch):
    runtime = harness.NativeRuntime("sqlite", ())
    closed = []
    class Connection:
        def __init__(self, *_, **__):
            pass
        def request(self, *_, **__):
            pass
        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda _: b"x" * (2 * 1024 * 1024 + 1))
        def close(self):
            closed.append(True)
    monkeypatch.setattr(harness.http.client, "HTTPSConnection", Connection)
    try:
        with pytest.raises(ValueError, match="bound"):
            runtime.request("GET", "/api/health")
        monkeypatch.setattr(Connection, "getresponse", lambda _: SimpleNamespace(status=200, read=lambda _: b"not JSON"))
        with pytest.raises(ValueError):
            runtime.request("POST", "/api/test", {})
        assert len(closed) == 2
    finally:
        runtime.__exit__(None, None, None)


def test_cli_runtime_check_and_replay_use_the_versioned_profile(tmp_path, monkeypatch):
    from whab_plugin_devkit import cli

    candidate, reference, artifacts = inputs()
    monkeypatch.setattr(cli, "_selected", lambda *_, **__: (candidate, reference, artifacts))
    monkeypatch.setattr(profiles, "replay_runtime_configuration", lambda *_, **__: {"runtime": {"status": "passed"}})
    output = tmp_path / "runtime-check"
    assert cli.main(["check", "--runtime", "--plugin", candidate.id, "--combinations", "1", "--wheelhouse", "unused", "--output", str(output)]) == 0
    assert cli.main(["replay", str(output / "configuration-00.json"), "--wheelhouse", "unused"]) == 0


def test_runtime_cli_verifies_json_and_reviewed_fixture_closure_before_loading(monkeypatch):
    from whab_plugin_devkit import cli

    candidate, reference, _ = inputs()
    entries = [SimpleNamespace(name=row.id, dist=SimpleNamespace(metadata={"Name": row.id}), load=lambda row=row: row)
               for row in (candidate, reference)]
    monkeypatch.setattr(cli.metadata, "entry_points", lambda **_: entries)
    monkeypatch.setattr(cli.metadata, "version", lambda _: "2.32.0")
    observed = []
    monkeypatch.setattr(cli, "installed_artifacts", lambda _, required: observed.extend(required) or [])
    assert cli._selected(candidate.id, "unused", runtime=True)[:2] == (candidate, reference)
    assert {"whab_plugin_json", "whab_devkit_runtime_peers"} <= set(observed)


def test_startup_exit_is_bounded_and_never_exposes_subprocess_diagnostics(monkeypatch):
    runtime = harness.NativeRuntime("sqlite", ())
    monkeypatch.setattr(harness.subprocess, "run", lambda *_, **__: None)
    monkeypatch.setattr(harness.ssl, "create_default_context", lambda **_: None)
    monkeypatch.setattr(harness.subprocess, "Popen", lambda *_, **__: SimpleNamespace(poll=lambda: 7))
    try:
        with pytest.raises(RuntimeError, match="fictional host failed startup"):
            runtime.start()
    finally:
        runtime.process = None
        runtime.__exit__(None, None, None)


def test_failed_copy_and_wrong_active_selection_fail_observably():
    failed = SimpleNamespace(expect=lambda *_: {"copies": [{"id": "copy", "state": "failed"}]} )
    failed.wait = lambda observe: observe()
    with pytest.raises(AssertionError, match="copy did not synchronize"):
        profiles._copy_saved(failed, "copy")
    selected = SimpleNamespace(expect=lambda *_, **__: {"id": "ticket", "plugins": []}, operation=lambda *_: None)
    with pytest.raises(AssertionError, match="violated its public contract"):
        profiles._selection(selected, ["synthetic-report-a"])


def test_all_reviewed_peers_fall_back_from_replacement_without_claiming_a_new_provider(monkeypatch):
    candidate, reference, artifacts = inputs()
    candidate = Plugin("candidate", "Fictional", insights=lambda _: (), contract=PluginContract(lifecycle="live"))
    from whab_plugin_devkit.configurations import _PEERS, sample_configurations

    base = sample_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    base.update(peers=list(_PEERS), discovery_order=[candidate.id, reference.id, *_PEERS],
                configuration_order=[candidate.id, reference.id, *_PEERS], sequence="replace-peer")
    monkeypatch.setattr(profiles, "sample_configurations", lambda *_, **__: [base])
    result = profiles.sample_runtime_configurations(candidate, reference, artifacts)[0]
    assert result["runtime"]["sequence"] == "disable-enable-peer"


def test_mixed_provider_hooks_are_explicitly_inapplicable_and_never_start_factories():
    from dataclasses import replace

    candidate, reference, artifacts = inputs()
    candidate = replace(candidate, bank=lambda _: pytest.fail("Provider factory must never execute in a public-hook profile"))
    manifest = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    assert manifest["runtime"]["candidate_applicability"] == "not-applicable: unsupported candidate hooks"
    result = profiles.replay_runtime_configuration(manifest, candidate, reference, native=lambda *_: pytest.fail("Do not start an unsupported candidate family"))
    assert result["runtime"]["status"] == "not-applicable"
    manifest["runtime"]["candidate_applicability"] = "supported"
    with pytest.raises(ValueError, match="applicability declaration"):
        profiles.replay_runtime_configuration(manifest, candidate, reference)
