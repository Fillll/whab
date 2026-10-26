"""Small complete teaching projects using only the public WHAB SDK."""

import json
import re
from importlib.resources import files
from pathlib import Path

FAMILIES = ("reporting", "branding", "file-import", "categorization", "api", "ui", "bank", "remote-client", "storage")


def _static_metadata(capability):
    names = {"api": ("interface.api",), "ui": ("interface.ui",), "bank": ("ingestion.bank", "lifecycle.background"),
             "remote-client": ("ledger.mutate", "ledger.read"),
             "storage": ("ledger.read", "ledger.mutate", "provider.state", "provider.sync", "receipts", "transfer.neutral",
                         "transfer.prepare", "storage.authority", "storage.commands", "storage.worker")}.get(capability)
    if names is None:
        return ""
    if capability == "storage":
        names = tuple(sorted(names))
    capabilities = ", ".join(f'{{ name = "{name}", version = 1, composition = "{"singleton" if capability in {"ui", "remote-client"} or name.startswith(("ledger.", "provider.")) else "additive"}" }}' for name in names)
    return (f'[tool.whab.plugin]\napi-version = 1\ncapabilities = [{capabilities}]\nrequires = []\nconflicts = []\n'
            f'lifecycle = "live"\nlegacy = false\nauthority = "{"remote" if capability == "remote-client" else "local"}"\nstate-version = 1\nstate-upgrades = []\nsupport = "teaching"')


def generate_project(plugin_id, capability, destination, *, ci_ref=None):
    """Create a new project; never merge with or overwrite an existing path."""
    if type(plugin_id) is not str or len(plugin_id) > 64 or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", plugin_id) is None:
        raise ValueError("Choose a lowercase hyphen-separated plugin ID.")
    if capability not in FAMILIES:
        raise ValueError("Choose reporting, branding, file-import, categorization, api, ui, bank, remote-client, or storage.")
    if ci_ref is not None and (type(ci_ref) is not str or re.fullmatch(r"[0-9a-f]{40}", ci_ref) is None):
        raise ValueError("Choose a published full lowercase WHAB CI source commit.")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    module = "whab_plugin_" + plugin_id.replace("-", "_")
    source = destination / "src" / module
    source.mkdir(parents=True)
    category = {"reporting": "insight", "branding": "interface", "file-import": "ingestion", "categorization": "automation",
                "api": "interface", "ui": "interface", "bank": "ingestion", "remote-client": "storage", "storage": "storage"}[capability]
    (source / "plugin.json").write_text(json.dumps({"schema_version": 1, "id": plugin_id, "category": category, "dependencies": []}, indent=2) + "\n")
    tests = destination / "tests"
    tests.mkdir()
    template = files("whab_plugin_devkit").joinpath("templates")
    replacements = {"__PLUGIN_ID__": plugin_id, "__MODULE__": module,
                    "__HOST_MINIMUM__": {"api": "2.34", "ui": "2.36", "bank": "2.38", "remote-client": "2.40", "storage": "2.41"}.get(capability, "2.22"),
                    "__KIT_MINIMUM__": {"api": "0.6", "ui": "0.7", "bank": "0.8", "remote-client": "0.9", "storage": "0.10"}.get(capability, "0.2"),
                    "__REFERENCE_TEST_REQUIREMENT__": template.joinpath("remote-client_test_dependencies.txt" if capability == "remote-client" else "api_test_dependencies.txt").read_text().strip() if capability in {"api", "ui", "bank", "remote-client"} else "",
                    "__TOKEN_SETTING__": plugin_id.replace("-", "_").upper() + "_TOKEN",
                    "__URL_SETTING__": plugin_id.replace("-", "_").upper() + "_URL",
                    "__BUDGET_SETTING__": plugin_id.replace("-", "_").upper() + "_BUDGET_ID",
                    "__STATIC_METADATA__": _static_metadata(capability) }
    for resource, path in ((capability + ".py.txt", source / "__init__.py"),
                           (capability + "_test.py.txt", tests / "test_plugin.py"),
                           ("project.toml.txt", destination / "pyproject.toml"),
                           ((capability + "_README.md.txt" if capability in {"api", "ui", "bank", "remote-client", "storage"} else "README.md.txt"), destination / "README.md")):
        value = template.joinpath(resource).read_text()
        for old, new in replacements.items():
            value = value.replace(old, new)
        path.write_text(value)
    (destination / "LICENSE").write_text(template.joinpath("LICENSE.txt").read_text())
    if capability == "storage":
        for resource, path in (("storage_backend.py.txt", source / "backend.py"),
                               ("storage_native_test.py.txt", tests / "test_native_failures.py")):
            value = template.joinpath(resource).read_text()
            for old, new in replacements.items():
                value = value.replace(old, new)
            path.write_text(value)
        project = destination / "pyproject.toml"
        project.write_text(project.read_text().replace('requires-python = ">=3.14"',
            'requires-python = ">=3.14"\nclassifiers = ["Operating System :: POSIX", "Programming Language :: Python :: 3"]'))
    if capability == "bank":
        project = destination / "pyproject.toml"
        project.write_text(project.read_text().replace('minimum-host = "2.38.0"', 'minimum-host = "2.38.0"\noffline-bank-fixture = "fictional-v1"'))
    if capability == "remote-client":
        project = destination / "pyproject.toml"
        project.write_text(project.read_text().replace('minimum-host = "2.40.0"', 'minimum-host = "2.40.0"\noffline-remote-fixture = "fictional-v1"'))
    if ci_ref is not None:
        workflow = destination / ".github/workflows/whab.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(template.joinpath("workflow.yml.txt").read_text().replace("__PLUGIN_ID__", plugin_id).replace("__CI_REF__", ci_ref))
    return destination
