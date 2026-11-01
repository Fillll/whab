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
    from .operation_schema import validate_document

    documentation = {"schema_version": 1, "plugin_id": plugin_id,
        "summary": "Generated " + capability + " plugin. Document each public operation before adding callable behavior.",
        "delegates": ["core"] if capability in {"storage", "remote-client", "api", "ui"} else [],
        "operations": [{"kind": "sdk", "name": capability,
            "summary": "Host-invoked " + capability + " capability; see the generated README and typed SDK contract.",
            "input": {"type": "object", "description": "Host-supplied typed capability inputs.", "properties": {}, "additionalProperties": True},
            "output": {"description": "Typed SDK capability result; not a remote JSON invocation."},
            "examples": [{"input": {}, "output": None}],
            "side_effects": "Only the capability's declared scoped effects; no implicit inference or financial changes.",
            "permissions": "Host-scoped SDK access. Public remote operations require separate explicit registration.",
            "scope": "Addressed budget and workspace; currencies remain separate.",
            "errors": ["Invalid input or unavailable capability is rejected with a safe authored error."],
            "retry": "Follow the owning SDK lifecycle; remote mutations retain original request IDs and revisions.",
            "availability": "SDK callback only; not directly callable through the CLI."}]}
    validate_document(documentation, plugin_id)
    (source / "operations.json").write_text(json.dumps(documentation, indent=2) + "\n")
    tests = destination / "tests"
    tests.mkdir()
    template = files("whab_plugin_devkit").joinpath("templates")
    replacements = {"__PLUGIN_ID__": plugin_id, "__MODULE__": module,
                    "__HOST_MINIMUM__": {"api": "2.34", "ui": "2.36", "bank": "2.38", "remote-client": "2.40", "storage": "2.41"}.get(capability, "2.22"),
                    "__KIT_MINIMUM__": "0.11",
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
    operation_test = tests / "test_plugin.py"
    operation_test.write_text(operation_test.read_text() + "\n\ndef test_packaged_operation_documentation():\n    import json\n    from importlib.resources import files\n    from whab_plugin_devkit.operation_schema import validate_document\n    document = json.loads(files(" + repr(module) + ").joinpath('operations.json').read_bytes())\n    assert validate_document(document, " + repr(plugin_id) + ")['plugin_id'] == " + repr(plugin_id) + "\n")
    readme = destination / "README.md"
    readme.write_text(readme.read_text() + "\n## Operation documentation\n\nThe packaged [operations.json](src/" + module + "/operations.json) is a required part of this plugin. Keep it complete for every exposed operation; input/output schemas, examples, side effects, scope, permissions, errors and retry rules are checked without executing hooks. Use whab-cli describe --plugin " + plugin_id + " --json to inspect it offline. SDK descriptions do not authorize remote calls.\n")
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
