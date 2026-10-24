import importlib.util
import json
import runpy
import sys

import pytest
from whab_plugin_devkit.configurations import exercise_candidate
from whab_plugin_devkit.generator import FAMILIES, generate_project

from whab import plugins as sdk


@pytest.mark.parametrize("family", FAMILIES)
def test_complete_generated_project_uses_public_sdk_and_has_working_fictional_hooks(tmp_path, family):
    identifier = "fictional-" + family
    project = generate_project(identifier, family, tmp_path / identifier)
    module_name = "whab_plugin_" + identifier.replace("-", "_")
    source = project / "src" / module_name / "__init__.py"
    minimum = {"api": "2.34", "ui": "2.36", "bank": "2.38", "remote-client": "2.40", "storage": "2.41"}.get(family, "2.22")
    assert f"whab>={minimum},<3" in (project / "pyproject.toml").read_text()
    if family == "api" and not hasattr(sdk, "RemoteLedgerInterface"):
        assert "minimum-host = \"2.34.0\"" in (project / "pyproject.toml").read_text()
        return
    if family == "ui" and "view_protocol" not in sdk.UserInterface.__dataclass_fields__:
        assert 'minimum-host = "2.36.0"' in (project / "pyproject.toml").read_text()
        return
    if family == "bank" and not hasattr(sdk, "BankAdapter"):
        assert 'minimum-host = "2.38.0"' in (project / "pyproject.toml").read_text()
        return
    if family == "remote-client" and not hasattr(sdk, "RemoteLedgerClient"):
        assert 'minimum-host = "2.40.0"' in (project / "pyproject.toml").read_text()
        return
    if family == "storage" and importlib.util.find_spec("whab.plugins.storage_model") is None:
        assert 'minimum-host = "2.41.0"' in (project / "pyproject.toml").read_text()
        assert (project / "src" / module_name / "backend.py").is_file()
        return
    spec = importlib.util.spec_from_file_location(module_name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.plugin.id == identifier
    if family == "storage":
        sys.modules[module_name] = module
        try:
            assert exercise_candidate(module.plugin) == ["storage"]
        finally:
            for name in tuple(sys.modules):
                if name == module_name or name.startswith(module_name + "."):
                    sys.modules.pop(name)
    else:
        assert exercise_candidate(module.plugin)
    assert (project / "LICENSE").is_file() and (project / "README.md").is_file()
    assert "whab.app" not in source.read_text() and "whab_plugin_sqlite" not in source.read_text()
    if family in {"api", "ui"}:
        test = (project / "tests/test_plugin.py").read_text()
        assert "NativeRuntime" in test and ("assert_api_interface" if family == "api" else "assert_visual_interface") in test
        return  # Its installed-only native behavioral test runs in the wheel gate.
    if family == "bank":
        assert 'offline-bank-fixture = "fictional-v1"' in (project / "pyproject.toml").read_text()
        assert "exercise_bank_runtime" in (project / "tests/test_plugin.py").read_text()
        return  # The complete ordinary/native test suite runs against its installed wheel.
    if family == "remote-client":
        assert 'offline-remote-fixture = "fictional-v1"' in (project / "pyproject.toml").read_text()
        assert "assert_lost_remote_result" in (project / "tests/test_plugin.py").read_text()
        return  # Its actual two-host native test runs against installed artifacts.
    if family == "storage":
        assert 'Operating System :: POSIX' in (project / "pyproject.toml").read_text()
        assert (project / "tests/test_native_failures.py").is_file()
        assert 'storage_conformance' in (project / "tests/test_plugin.py").read_text()
        return  # Its full durable native tests execute from installed wheels.
    # Run the generated ordinary tests too, with only its module registration.
    sys.modules[module_name] = module
    try:
        tests = runpy.run_path(str(project / "tests/test_plugin.py"))
        for name, function in tests.items():
            if name.startswith("test_"):
                marks = getattr(function, "pytestmark", [])
                if marks:
                    for value in marks[0].args[1]:
                        function(value)
                else:
                    function()
    finally:
        sys.modules.pop(module_name)


@pytest.mark.parametrize(("identifier", "family"), [("../escape", "reporting"), ("Upper", "reporting"), ("has_under", "reporting"), ("valid", "unknown")])
def test_generator_rejects_unsafe_identity_or_unimplemented_family(tmp_path, identifier, family):
    with pytest.raises(ValueError):
        generate_project(identifier, family, tmp_path / "project")
    assert not tuple(tmp_path.iterdir())


def test_existing_project_files_are_never_overwritten(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    original = path / "private.txt"
    original.write_text("fictional untouched content")
    with pytest.raises(FileExistsError):
        generate_project("valid", "reporting", path)
    assert original.read_text() == "fictional untouched content"


def test_generator_output_contains_no_unexpanded_identity_tokens(tmp_path):
    project = generate_project("valid", "reporting", tmp_path / "project")
    assert all("__PLUGIN_ID__" not in path.read_text() and "__MODULE__" not in path.read_text()
               for path in project.rglob("*") if path.is_file())
    assert json.dumps({"project": project.name})


def test_generated_reusable_ci_pins_workflow_and_candidate_to_reviewed_commit(tmp_path):
    revision = "a" * 40
    project = generate_project("fictional-report", "reporting", tmp_path / "project", ci_ref=revision)
    workflow = (project / ".github/workflows/whab.yml").read_text()
    assert f"plugin-conformance.yml@{revision}" in workflow
    assert f"whab-ref: {revision}" in workflow and "plugin-id: fictional-report" in workflow
    assert "contents: read" in workflow and "secrets:" not in workflow
    assert "__CI_REF__" not in workflow and "__PLUGIN_ID__" not in workflow


@pytest.mark.parametrize("revision", ["main", "abcd", "A" * 40, 42])
def test_generator_rejects_mutable_or_malformed_ci_revision_before_creating_project(tmp_path, revision):
    with pytest.raises(ValueError, match="published full"):
        generate_project("fictional", "reporting", tmp_path / "project", ci_ref=revision)
    assert not tuple(tmp_path.iterdir())


def test_api_token_setting_matches_host_scoped_configuration_without_overwriting_selectors(tmp_path):
    project = generate_project("whab-plaid", "api", tmp_path / "project")
    source = (project / "src/whab_plugin_whab_plaid/__init__.py").read_text()
    assert 'token_setting="WHAB_PLAID_TOKEN"' in source
    assert 'minimum-host = "2.34.0"' in (project / "pyproject.toml").read_text()
