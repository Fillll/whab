"""The generated-wheel gate requires independent installs and distinct selections."""

import importlib
import json
import sys
from importlib import metadata, util
from pathlib import Path
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import artifacts


@pytest.fixture
def checker():
    path = Path(__file__).resolve().parents[1] / "scripts/check_plugin_generation.py"
    spec = util.spec_from_file_location("whab_generation_gate", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generation_gate_pins_real_minimum_host_and_uses_shared_isolation(checker, monkeypatch, tmp_path):
    helper = checker.helpers()
    assert helper.MINIMUM_HOST_VERSION == "2.22.0"
    assert helper.MINIMUM_HOST_COMMIT == "bac249ca0e62638a40a6effc44af741c283e83d7"
    monkeypatch.setenv("PYTHONPATH", "/fictional/source")
    monkeypatch.setenv("WHAB_BUDGET", "/fictional/private.whab")
    environment = helper.isolated_environment(tmp_path)
    assert "PYTHONPATH" not in environment and "WHAB_BUDGET" not in environment
    assert environment["WHAB_HOME"] == str(tmp_path)
    assert environment["ALLOW_LIVE_PLAID"] == "false"


def test_saved_generation_evidence_cannot_count_order_only_changes_as_distinct(checker, tmp_path):
    for index in range(10):
        (tmp_path / f"configuration-{index:02d}.json").write_text(json.dumps({"peers": [f"peer-{index}", "common"]}))
    assert len(checker.configuration_manifests(tmp_path)) == 10
    (tmp_path / "configuration-09.json").write_text(json.dumps({"peers": ["common", "peer-0"]}))
    with pytest.raises(ValueError, match="ten distinct"):
        checker.configuration_manifests(tmp_path)
    (tmp_path / "configuration-09.json").unlink()
    with pytest.raises(ValueError, match="ten distinct"):
        checker.configuration_manifests(tmp_path)


@pytest.mark.parametrize("failed", [False, True])
def test_artifact_workspace_preserves_only_wheels_and_replay_files_even_after_failure(checker, tmp_path, failed):
    destination = tmp_path / "evidence"
    try:
        with checker.artifact_workspace(destination) as base:
            wheels = base / "wheels"
            wheels.mkdir()
            (wheels / "reviewed.whl").write_bytes(b"fictional artifact")
            replay = base / "external-2.25.0-reporting/configurations"
            replay.mkdir(parents=True)
            (replay / "configuration-00.json").write_text('{"seed":42}')
            runtime = base / "runtime"
            runtime.mkdir()
            (runtime / "private-record").write_text("must not be archived")
            if failed:
                raise RuntimeError("fictional failure")
    except RuntimeError:
        assert failed
    assert not base.exists()
    assert (destination / "wheels/reviewed.whl").read_bytes() == b"fictional artifact"
    assert (destination / "external-2.25.0-reporting/configurations/configuration-00.json").exists()
    assert not (destination / "runtime").exists()
    with pytest.raises(FileExistsError):
        with checker.artifact_workspace(destination):
            pytest.fail("Existing evidence must never be overwritten")


@pytest.mark.parametrize("failure", [None, "source-import", "wrong-version", "extra-plugin", "missing-plugin", "runtime-toolkit-entry", "entrypoint-owner", "descriptor-id", "missing-source"])
def test_installed_gate_verifies_module_locations_versions_and_entrypoint_ownership(checker, monkeypatch, tmp_path, failure):
    root = tmp_path / "environment"
    root.mkdir()
    monkeypatch.setattr(sys, "prefix", str(root))
    monkeypatch.setattr(sys, "argv", ["verify", json.dumps({"whab": "2.22.0", "whab_plugin_devkit": "0.2.0"}),
                                    "generated-report", "fictional", "fictional", str(tmp_path), str(tmp_path / "installation.json")])
    monkeypatch.setattr(artifacts, "installed_artifacts", lambda *_: [])
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(__file__=str(
        (tmp_path / "checkout" if failure == "source-import" else root / "site-packages") / name / "__init__.py")))
    monkeypatch.setattr(metadata, "version", lambda name: "9" if failure == "wrong-version" else {"whab": "2.22.0", "whab_plugin_devkit": "0.2.0"}[name])
    names = {"sqlite", "generated-report"}
    if failure == "extra-plugin":
        names.add("unexpected-plugin")
    if failure == "missing-plugin":
        names.remove("sqlite")
    monkeypatch.setattr(metadata, "entry_points", lambda **_: [SimpleNamespace(
        name=name, dist=SimpleNamespace(metadata={"Name": "wrong" if failure == "entrypoint-owner" else "fictional"}),
        load=lambda: SimpleNamespace(id="wrong" if failure == "descriptor-id" else "generated-report"),
    ) for name in names])
    entry = SimpleNamespace(group="whab.plugins" if failure == "runtime-toolkit-entry" else "console_scripts",
                            name="whab-plugin", value="whab_plugin_devkit.cli:main")
    monkeypatch.setattr(metadata, "distribution", lambda _: SimpleNamespace(entry_points=[entry], locate_file=lambda name: root / name))
    monkeypatch.setattr(metadata, "files", lambda _: [] if failure == "missing-source" else [Path("fictional.py")])
    if failure is None:
        exec(checker.VERIFY_PROJECT, {})
    else:
        with pytest.raises(AssertionError):
            exec(checker.VERIFY_PROJECT, {})


def test_generation_gate_sanitizes_unexpected_build_failures(checker, monkeypatch, capsys):
    def failed():
        raise RuntimeError("synthetic-private-payload-and-path")

    monkeypatch.setattr(checker, "helpers", failed)
    assert checker.main([]) == 1
    output = capsys.readouterr().err
    assert "RuntimeError" in output and "synthetic-private" not in output
