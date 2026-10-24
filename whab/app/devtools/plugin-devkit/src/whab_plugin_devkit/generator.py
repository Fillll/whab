"""Small complete teaching projects using only the public WHAB SDK."""

import re
from importlib.resources import files
from pathlib import Path

FAMILIES = ("reporting", "branding", "file-import", "categorization")


def generate_project(plugin_id, capability, destination, *, ci_ref=None):
    """Create a new project; never merge with or overwrite an existing path."""
    if type(plugin_id) is not str or len(plugin_id) > 64 or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", plugin_id) is None:
        raise ValueError("Choose a lowercase hyphen-separated plugin ID.")
    if capability not in FAMILIES:
        raise ValueError("Choose reporting, branding, file-import, or categorization.")
    if ci_ref is not None and (type(ci_ref) is not str or re.fullmatch(r"[0-9a-f]{40}", ci_ref) is None):
        raise ValueError("Choose a published full lowercase WHAB CI source commit.")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    module = "whab_plugin_" + plugin_id.replace("-", "_")
    source = destination / "src" / module
    source.mkdir(parents=True)
    tests = destination / "tests"
    tests.mkdir()
    template = files("whab_plugin_devkit").joinpath("templates")
    replacements = {"__PLUGIN_ID__": plugin_id, "__MODULE__": module}
    for resource, path in ((capability + ".py.txt", source / "__init__.py"),
                           (capability + "_test.py.txt", tests / "test_plugin.py"),
                           ("project.toml.txt", destination / "pyproject.toml"),
                           ("README.md.txt", destination / "README.md")):
        value = template.joinpath(resource).read_text()
        for old, new in replacements.items():
            value = value.replace(old, new)
        path.write_text(value)
    (destination / "LICENSE").write_text(template.joinpath("LICENSE.txt").read_text())
    if ci_ref is not None:
        workflow = destination / ".github/workflows/whab.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(template.joinpath("workflow.yml.txt").read_text().replace("__PLUGIN_ID__", plugin_id).replace("__CI_REF__", ci_ref))
    return destination
