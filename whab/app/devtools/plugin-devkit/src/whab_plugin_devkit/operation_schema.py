"""Validation for packaged, data-only operation documentation. No hooks execute.

This host-independent SDK implementation is also copied into the developer
toolkit for its genuine older-host conformance environments by check_operations.
"""

import json

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing.exceptions import Unresolvable

KINDS = ("query", "command", "feature-query", "feature-command", "local", "sdk")
FIELDS = {"name", "kind", "summary", "input", "output", "examples", "side_effects",
          "permissions", "scope", "errors", "retry", "availability"}


def _schemas(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"} and (type(item) is not str or not item.startswith("#")):
                raise ValueError("Operation schemas must use local references only.")
            _schemas(item)
    elif isinstance(value, list):
        for item in value:
            _schemas(item)


def validate_value(schema, value):
    """Validate finite JSON without resolving network schemas or showing values."""
    try:
        json.dumps(value, allow_nan=False)
        Draft202012Validator(schema).validate(value)
    except (ValueError, TypeError, ValidationError, Unresolvable):
        raise ValueError("Operation data does not match its documented schema.") from None


def validate_document(document, plugin_id=None):
    """Require complete documentation and schema-valid fictional examples."""
    if (type(document) is not dict
            or set(document) != {"schema_version", "plugin_id", "summary", "delegates", "operations"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or type(document["plugin_id"]) is not str or not document["plugin_id"]
            or plugin_id is not None and document["plugin_id"] != plugin_id
            or type(document["summary"]) is not str or not document["summary"].strip()
            or type(document["delegates"]) is not list
            or any(type(item) is not str or not item for item in document["delegates"])
            or type(document["operations"]) is not list or len(document["operations"]) > 256):
        raise ValueError("Invalid operation documentation header.")
    if len(json.dumps(document, allow_nan=False).encode()) > 512 * 1024:
        raise ValueError("Operation documentation exceeds 512 KiB.")
    seen = set()
    for operation in document["operations"]:
        if (type(operation) is not dict or set(operation) != FIELDS
                or operation["kind"] not in KINDS
                or any(type(operation[key]) is not str or not operation[key].strip()
                       for key in ("name", "summary", "side_effects", "permissions", "scope", "retry", "availability"))
                or type(operation["errors"]) is not list or not operation["errors"]
                or any(type(item) is not str or not item.strip() for item in operation["errors"])
                or type(operation["examples"]) is not list or not operation["examples"]):
            raise ValueError("Every operation requires purpose, schemas, examples and access/error/retry behavior.")
        identity = operation["kind"], operation["name"]
        if identity in seen:
            raise ValueError("Operation names must be unique within their kind.")
        seen.add(identity)
        for name in ("input", "output"):
            schema = operation[name]
            if type(schema) is not dict or not schema.get("description"):
                raise ValueError("Operation schemas require a description.")
            _schemas(schema)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError:
                raise ValueError("Invalid operation JSON Schema.") from None
            for field in schema.get("properties", {}).values():
                if type(field) is not dict or not field.get("description"):
                    raise ValueError("Operation fields require descriptions.")
                if "default" in field:
                    validate_value(field, field["default"])
        for example in operation["examples"]:
            if type(example) is not dict or set(example) != {"input", "output"}:
                raise ValueError("Examples require input and output.")
            for name in ("input", "output"):
                validate_value(operation[name], example[name])
    return document
