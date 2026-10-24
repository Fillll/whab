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


def test_project_minimum_recipe_is_explicit_and_must_match_actual_declared_host_floor(checker, tmp_path):
    project = tmp_path / "api"
    project.mkdir()
    path = project / "pyproject.toml"
    path.write_text('[project]\ndependencies=["whab>=2.34,<3"]\n[tool.whab.plugin-devkit]\nminimum-host="2.34.0"\n')
    assert checker.project_minimum(project) == "2.34.0"
    assert checker.MINIMUM_RECIPES["2.34.0"] == "ebbff0d464dd67c0269428a6d4a339778e21ec57"
    for dependency, floor in [("whab>=2.22,<3", "2.34.0"), ("whab>=2.30,<3", "2.34.0"), ("whab>=2.34,<3", "2.22.0"), ("whab>=2.34,<3", "2.30.0"), ("whab[optional]>=2.34", "2.34.0")]:
        path.write_text(f'[project]\ndependencies=["{dependency}"]\n[tool.whab.plugin-devkit]\nminimum-host="{floor}"\n')
        with pytest.raises(ValueError):
            checker.project_minimum(project)


@pytest.mark.parametrize(("version", "revision"), [
    ("2.34.0", "ebbff0d464dd67c0269428a6d4a339778e21ec57"),
    ("2.36.0", "a866eda580c6e363a20636785bc4b4003db0652f"),
    ("2.38.0", "dd52ec9554ee3c36395f8f2a7c1d2ab5ff00353f"),
    ("2.40.0", "e8fa8585c16d2f8a93b1966315c0fee34eb11d35"),
    ("2.41.0", "c10bf14a96cfd3707124fc2916d794da1b69f41f"),
])
def test_interface_minimum_uses_its_genuine_immutable_source_recipe_without_version_rewriting(checker, tmp_path, version, revision):
    calls = []
    helper = SimpleNamespace(minimum_host_source=lambda base, env: calls.append(("legacy", base)) or "legacy-source",
                             source_at_revision=lambda base, env, revision: calls.append(("api", revision)) or "api-source",
                             host_version=lambda _: version)
    assert checker.minimum_source(helper, tmp_path / "older", {}, "2.22.0") == "legacy-source"
    assert checker.minimum_source(helper, tmp_path / "interface", {}, version) == "api-source"
    assert calls[-1] == ("api", revision) and checker.MINIMUM_RECIPES[version] == revision
    helper.host_version = lambda _: "2.22.0"
    with pytest.raises(ValueError, match="advertised host"):
        checker.minimum_source(helper, tmp_path / "wrong", {}, version)


def test_bank_fixture_approval_is_static_explicit_and_bound_to_its_actual_host_floor(checker, tmp_path):
    metadata = tmp_path / "pyproject.toml"
    metadata.write_text('[project]\ndependencies=["whab>=2.38,<3"]\n')
    assert checker.bank_fixture_arguments(tmp_path) == []
    metadata.write_text(metadata.read_text() + '[tool.whab.plugin-devkit]\nminimum-host="2.38.0"\noffline-bank-fixture="fictional-v1"\n')
    assert checker.bank_fixture_arguments(tmp_path) == ["--bank-fixture", "fictional-v1"]
    metadata.write_text(metadata.read_text().replace('"fictional-v1"', '"real-provider"'))
    with pytest.raises(ValueError, match="reviewed"):
        checker.bank_fixture_arguments(tmp_path)


def test_remote_fixture_approval_has_a_genuine_provisioning_floor(checker, tmp_path):
    metadata = tmp_path / "pyproject.toml"
    metadata.write_text('[project]\ndependencies=["whab>=2.40,<3"]\n')
    assert not checker.remote_fixture_enabled(tmp_path)
    metadata.write_text(metadata.read_text() + '[tool.whab.plugin-devkit]\nminimum-host="2.40.0"\noffline-remote-fixture="fictional-v1"\n')
    assert checker.remote_fixture_enabled(tmp_path)
    assert checker.MINIMUM_RECIPES["2.40.0"] == "e8fa8585c16d2f8a93b1966315c0fee34eb11d35"
    for version, fixture in (("2.40", "real-server"), ("2.39", "fictional-v1")):
        metadata.write_text(f'[project]\ndependencies=["whab>={version},<3"]\n[tool.whab.plugin-devkit]\nminimum-host="{version}.0"\noffline-remote-fixture="{fixture}"\n')
        with pytest.raises(ValueError):
            checker.remote_fixture_enabled(tmp_path)
