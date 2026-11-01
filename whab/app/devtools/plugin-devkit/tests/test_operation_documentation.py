"""Documentation conformance requires complete contracts without executing hooks."""

import copy
import json
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import cli, documentation
from whab_plugin_devkit import operation_schema as schema
from whab_plugin_devkit.generator import generate_project


def document():
    return {
        "schema_version": 1,
        "plugin_id": "fictional",
        "summary": "Fictional documented feature.",
        "delegates": ["core"],
        "operations": [
            {
                "name": "read",
                "kind": "feature-query",
                "summary": "Read fictional state.",
                "input": {
                    "type": "object",
                    "description": "Input.",
                    "properties": {
                        "choice": {
                            "type": "string",
                            "description": "Choice.",
                            "enum": ["one", "two"],
                            "default": "one",
                        }
                    },
                    "additionalProperties": False,
                },
                "output": {"type": "object", "description": "Output."},
                "examples": [{"input": {"choice": "one"}, "output": {}}],
                "side_effects": "Read-only.",
                "permissions": "Budget access.",
                "scope": "Budget/workspace.",
                "errors": ["Invalid choice."],
                "retry": "Read again.",
                "availability": "Enabled feature.",
            }
        ],
    }


def test_complete_contract_defaults_examples_and_finite_json():
    value = document()
    value["operations"][0]["input"]["properties"]["flag"] = {
        "type": "boolean",
        "description": "Optional explicit flag.",
    }
    assert schema.validate_document(value) is value
    assert schema.validate_document(value, "fictional") is value
    schema.validate_value({"type": "integer"}, 1)
    for bad in ({"type": "integer"}, {"type": "object"}):
        with pytest.raises(ValueError):
            schema.validate_value(bad, float("nan"))
    with pytest.raises(ValueError):
        schema.validate_value({"type": "string"}, {})
    with pytest.raises(ValueError):
        schema.validate_value({}, {"not-json": object()})
    with pytest.raises(ValueError):
        schema.validate_value({"$ref": "#/$defs/absent"}, {})


@pytest.mark.parametrize("invalid_default", [False, True])
def test_nested_fields_require_descriptions_and_valid_defaults(invalid_default):
    value = document()
    nested = {"type": "integer", "default": "invalid"} if invalid_default else {"type": "integer"}
    if invalid_default:
        nested["description"] = "Nested integer."
    value["operations"][0]["input"]["properties"]["nested"] = {
        "type": "object", "description": "Nested controls.", "properties": {"count": nested}
    }
    with pytest.raises(ValueError):
        schema.validate_document(value)


def test_schema_walk_checks_subschemas_and_leaves_literal_values_alone():
    value = document()
    row = value["operations"][0]
    row["input"]["properties"]["properties"] = {"description": "A literal JSON object.", "const": {"$ref": "https://example.invalid/data", "properties": None}}
    row["input"]["allOf"] = [True, {"additionalProperties": True}]
    assert schema.validate_document(value)
    row["input"]["allOf"].append({"$ref": "https://example.invalid/no-network"})
    with pytest.raises(ValueError):
        schema.validate_document(value)


@pytest.mark.parametrize(
    "case",
    [
        "header",
        "identity",
        "oversize",
        "empty-summary",
        "missing-field",
        "duplicate",
        "no-description",
        "remote-ref",
        "bad-schema",
        "field-description",
        "bad-default",
        "bad-example",
        "example-fields",
        "empty-examples",
        "empty-errors",
    ],
)
def test_incomplete_or_unsafe_operation_metadata_is_rejected(case):
    value = document()
    row = value["operations"][0]
    if case == "header":
        value["unexpected"] = True
    elif case == "identity":
        value["plugin_id"] = "other"
    elif case == "oversize":
        value["summary"] = "x" * (512 * 1024)
    elif case == "empty-summary":
        value["summary"] = ""
    elif case == "missing-field":
        row.pop("retry")
    elif case == "duplicate":
        value["operations"].append(copy.deepcopy(row))
    elif case == "no-description":
        row["input"].pop("description")
    elif case == "remote-ref":
        row["input"]["$ref"] = "https://example.invalid/no-network.json"
    elif case == "bad-schema":
        row["input"]["type"] = "invalid-type"
    elif case == "field-description":
        row["input"]["properties"]["choice"].pop("description")
    elif case == "bad-default":
        row["input"]["properties"]["choice"]["default"] = "three"
    elif case == "bad-example":
        row["examples"][0]["input"]["choice"] = "three"
    elif case == "example-fields":
        row["examples"] = [{}]
    elif case == "empty-examples":
        row["examples"] = []
    else:
        row["errors"] = []
    with pytest.raises(ValueError):
        schema.validate_document(value, "fictional")


def test_generated_project_and_installed_candidate_require_packaged_docs(
    tmp_path, monkeypatch, capsys
):
    project = generate_project("fictional", "reporting", tmp_path / "project")
    assert documentation.check_project_documentation(project)["plugin_id"] == "fictional"
    assert cli.main(["docs", "--directory", str(project)]) == 0
    assert "Validated" in capsys.readouterr().out
    package = project / "src/whab_plugin_fictional"
    entry = SimpleNamespace(value="whab_plugin_fictional:plugin", name="fictional")
    monkeypatch.setattr(
        documentation.util,
        "find_spec",
        lambda _: SimpleNamespace(submodule_search_locations=[str(package)]),
    )
    assert documentation.check_entry_documentation(entry)["plugin_id"] == "fictional"
    metadata = project / "pyproject.toml"
    text = metadata.read_text()
    metadata.write_text(text.replace(', "operations.json"', ""))
    with pytest.raises(ValueError):
        documentation.check_project_documentation(project)
    metadata.write_text(
        text.replace(
            'fictional = "whab_plugin_fictional:plugin"',
            'fictional = "whab_plugin_fictional:plugin"\nother = "whab_plugin_fictional:plugin"',
        )
    )
    with pytest.raises(ValueError):
        documentation.check_project_documentation(project)
    metadata.write_text(text)
    readme = project / "README.md"
    original_readme = readme.read_text()
    readme.write_text("No operation documentation section.")
    with pytest.raises(ValueError):
        documentation.check_project_documentation(project)
    readme.write_text(original_readme)
    raw = (package / "operations.json").read_bytes()
    (package / "operations.json").unlink()
    with pytest.raises(ValueError):
        documentation.check_entry_documentation(entry)
    assert cli.main(["docs", "--directory", str(project)]) == 1
    assert "failed" in capsys.readouterr().err
    (package / "operations.json").write_bytes(raw)
    monkeypatch.setattr(documentation.util, "find_spec", lambda _: None)
    with pytest.raises(ValueError):
        documentation.check_entry_documentation(entry)
    (package / "operations.json").write_text(json.dumps(document()))
