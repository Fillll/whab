import importlib.util
import json
import runpy
import sys

import pytest
from whab_plugin_devkit.configurations import exercise_candidate
from whab_plugin_devkit.generator import FAMILIES, generate_project


@pytest.mark.parametrize("family", FAMILIES)
def test_complete_generated_project_uses_public_sdk_and_has_working_fictional_hooks(tmp_path, family):
    identifier = "fictional-" + family
    project = generate_project(identifier, family, tmp_path / identifier)
    module_name = "whab_plugin_" + identifier.replace("-", "_")
    source = project / "src" / module_name / "__init__.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.plugin.id == identifier
    assert exercise_candidate(module.plugin)
    assert (project / "LICENSE").is_file() and (project / "README.md").is_file()
    assert "whab>=2.22,<3" in (project / "pyproject.toml").read_text()
    assert "whab.app" not in source.read_text() and "whab_plugin_sqlite" not in source.read_text()
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


@pytest.mark.parametrize(("identifier", "family"), [("../escape", "reporting"), ("Upper", "reporting"), ("has_under", "reporting"), ("valid", "storage")])
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
