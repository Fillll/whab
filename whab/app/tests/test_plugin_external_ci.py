"""Reusable CI checks installed behavior and real immutable source artifacts."""

import configparser
import importlib.util
import os
import re
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def checker():
    path = Path(__file__).resolve().parents[1] / "scripts/check_plugin_generation.py"
    spec = importlib.util.spec_from_file_location("whab_external_project_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_wheel(tmp_path, entries="fictional = different_package.plugin:plugin", dependency=None):
    path = tmp_path / "fictional-1-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        metadata = "Name: fictional\nVersion: 1\n"
        if dependency:
            metadata += "Requires-Dist: " + dependency + "\n"
        archive.writestr("fictional-1.dist-info/METADATA", metadata)
        archive.writestr("fictional-1.dist-info/entry_points.txt", "[whab.plugins]\n" + entries)
    return path


def test_candidate_registration_uses_wheel_metadata_without_importing_project(checker, tmp_path):
    assert checker.candidate_registration(candidate_wheel(tmp_path), "fictional") == "different_package"


@pytest.mark.parametrize("entries", ["other = package:plugin", "fictional = package:plugin\nother = package:plugin",
                                   "fictional = package:plugin [extra]", "fictional = ../escape:plugin"])
def test_ambiguous_or_dynamic_candidate_entrypoints_reject_before_install(checker, tmp_path, entries):
    with pytest.raises((ValueError, configparser.Error)):
        checker.candidate_registration(candidate_wheel(tmp_path, entries), "fictional")


@pytest.mark.parametrize("dependency", ["fictional @ https://example.invalid/plugin.whl", "fictional[extra]>=1"])
def test_offline_install_cannot_follow_candidate_url_or_extra_requirements(checker, tmp_path, dependency):
    with pytest.raises(ValueError, match="offline-resolvable"):
        checker.candidate_registration(candidate_wheel(tmp_path, dependency=dependency), "fictional")


def test_real_source_archive_is_pinned_even_when_current_source_has_another_version(checker, tmp_path):
    helper = checker.helpers()
    repository = tmp_path / "repository"
    project = repository / "whab/app"
    source = project / "src/whab/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text('__version__ = "2.22.0"\n')
    for root in (project, project / "plugins/storage/sqlite"):
        root.mkdir(parents=True, exist_ok=True)
        (root / "pyproject.toml").write_text('[project]\nname="fictional"\nversion="1"\n')
        (root / "README.md").write_text("# Fictional\n")
        (root / "LICENSE").write_text("Fictional fixture\n")
        (root / "src").mkdir(exist_ok=True)
        (root / "src/fixture.py").write_text("# reviewed synthetic fixture\n")
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "-c", "user.name=Fictional", "-c", "user.email=fictional@example.invalid", "commit", "--quiet", "-m", "Fixture"], cwd=repository, check=True)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()
    source.write_text('__version__ = "9.0.0"\n')
    helper.ROOT = project
    base = tmp_path / "archive"
    base.mkdir()
    archived = helper.source_at_revision(base, os.environ.copy(), revision)
    assert helper.host_version(archived) == "2.22.0" and helper.host_version(project) == "9.0.0"
    for invalid in ("main", revision.upper(), "a" * 39):
        with pytest.raises(ValueError, match="full lowercase"):
            helper.source_at_revision(base, os.environ.copy(), invalid)


def test_missing_or_skipped_project_behavior_cannot_pass_reference_success(checker, tmp_path):
    helper = checker.helpers()
    path = tmp_path / "tests.xml"
    for count, skipped in ((0, 0), (1, 1)):
        path.write_text(f'<testsuites><testsuite tests="{count}" skipped="{skipped}" failures="0" errors="0"/></testsuites>')
        with pytest.raises(ValueError, match="run completely"):
            helper.validate_test_results(path, minimum_count=1)
    path.write_text('<testsuites><testsuite tests="2" skipped="0" failures="0" errors="0"/></testsuites>')
    assert helper.validate_test_results(path, minimum_count=1)["tests"] == 2


def test_reference_selection_uses_pinned_candidate_host_not_tooling_checkout_version(checker, tmp_path):
    helper = checker.helpers()
    candidate, minimum, host = tmp_path / "candidate-sqlite", tmp_path / "minimum", tmp_path / "candidate-host"
    baseline = minimum / "plugins/storage/sqlite"
    for path, version, requirement in ((candidate, "2", "whab>=2.30,<3"), (baseline, "1", "whab>=2.22,<3")):
        path.mkdir(parents=True)
        (path / "pyproject.toml").write_text(f'[project]\nversion="{version}"\ndependencies=["{requirement}"]\n')
    source = host / "src/whab/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text('__version__="2.30.0"\n')
    assert helper.reference_projects(candidate, minimum, candidate_host=host) == {"candidate": candidate, "minimum": baseline}


def test_project_coverage_requires_every_installed_module_not_only_entrypoint(checker, tmp_path):
    helper = checker.helpers()
    source, omitted = tmp_path / "plugin.py", tmp_path / "engine.py"
    report = {"meta": {"branch_coverage": True}, "files": {str(source): {"summary": {"missing_lines": 0, "missing_branches": 0}}}}
    with pytest.raises(ValueError, match="Candidate plugin source was omitted"):
        helper.validate_toolkit_coverage(report, [str(source), str(omitted)], tmp_path, label="Candidate plugin")


def test_only_explicit_regular_dependency_wheels_are_copied(checker, tmp_path):
    source, destination = tmp_path / "approved", tmp_path / "wheels"
    source.mkdir()
    destination.mkdir()
    wheel = source / "reviewed.whl"
    wheel.write_bytes(b"reviewed-wheel")
    checker.approved_extra_wheels(source, destination)
    assert (destination / wheel.name).read_bytes() == wheel.read_bytes()
    (source / "alias.whl").symlink_to(wheel)
    with pytest.raises(ValueError, match="regular wheels"):
        checker.approved_extra_wheels(source, destination)
    (source / "alias.whl").unlink()
    (destination / wheel.name).write_bytes(b"conflicting")
    with pytest.raises(ValueError, match="Conflicting"):
        checker.approved_extra_wheels(source, destination)


def test_external_project_build_never_resolves_dependencies_online(checker, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[build-system]\nrequires=["setuptools>=77"]\n')
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "wheel" in command:
            (wheels / "fixture.whl").write_bytes(b"fictional wheel")

    assert checker.build_project(SimpleNamespace(run=run), Path("python"), tmp_path, wheels, tmp_path, {}) == wheels / "fixture.whl"
    assert all("--no-index" in command for command in calls)
    assert "--no-build-isolation" in calls[-1] and "--no-deps" in calls[-1]


def test_caller_configuration_cannot_skip_tests_hide_coverage_or_select_private_home(checker, monkeypatch, tmp_path):
    for key in ("PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE", "PIP_EXTRA_INDEX_URL", "WHAB_BUDGET", "PYTHONPATH"):
        monkeypatch.setenv(key, "/fictional/private-or-bypass")
    environment = checker.project_environment(checker.helpers(), tmp_path)
    assert all(key not in environment for key in ("PYTEST_ADDOPTS", "COVERAGE_RCFILE", "COVERAGE_FILE", "PIP_EXTRA_INDEX_URL", "WHAB_BUDGET", "PYTHONPATH"))
    assert environment["WHAB_HOME"] == str(tmp_path) and environment["ALLOW_LIVE_PLAID"] == "false"
    assert environment["PIP_INDEX_URL"] == "https://pypi.org/simple"


def test_workflow_call_has_pinned_actions_read_only_permissions_and_no_secrets():
    path = Path(__file__).resolve().parents[3] / ".github/workflows/plugin-conformance.yml"
    source = path.read_text()
    assert "workflow_call:" in source and "contents: read" in source
    assert "secrets:" not in source and "pull_request_target" not in source
    actions = re.findall(r"uses: ([^\s]+)", source)
    assert actions and all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action) for action in actions)
    assert "persist-credentials: false" in source and "fetch-depth: 0" in source
    assert source.index("Validate immutable source") < source.index("Check out caller")
    assert "platform/whab/app/scripts/check_plugin_generation.py" in source
    # Embedded Python is executable, and runtime expressions are passed through
    # environment variables rather than interpolated into shell/Python source.
    embedded = source.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    compile("\n".join(line[10:] for line in embedded.splitlines()), "workflow", "exec")


@pytest.mark.parametrize("project", [".", "../outside"])
def test_workflow_passes_inputs_as_arguments_and_blocks_checkout_path_escape(tmp_path, monkeypatch, project):
    source = (Path(__file__).resolve().parents[3] / ".github/workflows/plugin-conformance.yml").read_text()
    embedded = source.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    code = "\n".join(line[10:] for line in embedded.splitlines())
    (tmp_path / "plugin").mkdir()
    monkeypatch.chdir(tmp_path)
    for name, value in {"WHAB_CI_PROJECT": project, "WHAB_CI_REF": "a" * 40, "WHAB_CI_PLUGIN": "fictional; literal argument",
                        "WHAB_CI_EXTRA_ARTIFACT": "", "WHAB_CI_EXTRA_WHEELS": "", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    if project != ".":
        with pytest.raises(SystemExit, match="within the caller checkout"):
            exec(code, {})
        assert calls == []
    else:
        exec(code, {})
        assert len(calls) == 1 and calls[0][1] == {"check": True}
        assert "fictional; literal argument" in calls[0][0]
        assert calls[0][0][1] == "-I"


@pytest.mark.parametrize("arguments", [["--project", "/fictional"], ["--plugin", "fictional"], ["--candidate-ref", "a" * 40]])
def test_invalid_external_invocation_fails_before_build_or_import(checker, monkeypatch, capsys, arguments):
    monkeypatch.setattr(checker, "helpers", lambda: pytest.fail("Must validate inputs before build helpers"))
    assert checker.main(arguments) == 1
    assert "required runtime/test/build wheel" in capsys.readouterr().err
