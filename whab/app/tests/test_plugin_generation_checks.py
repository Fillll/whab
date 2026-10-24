"""The generated-wheel gate requires independent installs and distinct selections."""

import importlib
import json
import subprocess
import sys
import warnings
import zipfile
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


def historical_wheel_builder(tmp_path, *, second="same"):
    """Emit controlled wheel members as two independent historical builds."""
    projects = [tmp_path / name for name in ("host-2.34", "host-2.36")]
    for project in projects:
        project.mkdir()
        (project / "pyproject.toml").write_text('[build-system]\nrequires=["setuptools>=68"]\n')
    wheels = tmp_path / "approved"
    wheels.mkdir()
    calls = []

    def run(command, **_):
        if "wheel" not in command:
            return
        calls.append(command)
        repeated = len(calls) == 2
        if repeated and second == "build-failure":
            raise subprocess.CalledProcessError(7, command)
        destination = Path(command[command.index("--wheel-dir") + 1])
        names = ["fictional-1.0-py3-none-any.whl"]
        if repeated and second == "empty-output":
            names = []
        if repeated and second == "multiple-wheels":
            names.append("other-1.0-py3-none-any.whl")
        for name in names:
            members = [("fictional/__init__.py", b"value = 1\n"),
                       ("fictional-1.0.dist-info/METADATA", b"Name: fictional\nVersion: 1.0\n")]
            if repeated and second == "different-code":
                members[0] = (members[0][0], b"value = 2\n")
            if repeated and second == "extra-member":
                members.append(("fictional/extra.py", b"extra = True\n"))
            if repeated and second == "duplicate-name":
                members.append(members[0])
            with zipfile.ZipFile(destination / name, "w") as archive, warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)  # Deliberate duplicate-member fixture.
                for member, payload in members:
                    entry = zipfile.ZipInfo(member, date_time=(2026, 10, 23, 0, 0, 2 * len(calls)))
                    entry.create_system = 3
                    mode = 0o100755 if repeated and second == "different-mode" else 0o100644
                    entry.external_attr = mode << 16
                    archive.writestr(entry, payload)

    return SimpleNamespace(run=run), projects, wheels, calls


def test_identical_historical_reference_wheels_reuse_the_original_approved_bytes(checker, tmp_path):
    helper, projects, wheels, calls = historical_wheel_builder(tmp_path)
    first = checker.build_project(helper, "reviewed-python", projects[0], wheels, tmp_path, {})
    original = first.read_bytes()
    second = checker.build_project(helper, "reviewed-python", projects[1], wheels, tmp_path, {})
    assert second == first and second.read_bytes() == original
    assert list(wheels.iterdir()) == [first]
    assert len(calls) == 2 and all(Path(call[call.index("--wheel-dir") + 1]) != wheels for call in calls)


@pytest.mark.parametrize("second", ["different-code", "different-mode", "extra-member", "duplicate-name",
                                   "build-failure", "multiple-wheels", "empty-output"])
def test_conflicting_or_failed_historical_build_never_overwrites_approved_artifacts(checker, tmp_path, second):
    helper, projects, wheels, _ = historical_wheel_builder(tmp_path, second=second)
    first = checker.build_project(helper, "reviewed-python", projects[0], wheels, tmp_path, {})
    original = first.read_bytes()
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        checker.build_project(helper, "reviewed-python", projects[1], wheels, tmp_path, {})
    assert list(wheels.iterdir()) == [first] and first.read_bytes() == original
