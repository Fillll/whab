"""Dependency manifest structure, shared with dependency-free source tooling."""

import re

CATEGORIES = ("interface", "ingestion", "insight", "storage", "automation", "extension")


def _identifier(value):
    return type(value) is str and len(value) <= 64 and re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", value) is not None


def validate_manifest(value, *, parse_specifier=None):
    required = {"schema_version", "id", "category", "dependencies"}
    if (type(value) is not dict or not required <= value.keys() or value.keys() - required - {"process_config"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not _identifier(value["id"]) or value["category"] not in (*CATEGORIES, "example")
            or type(value["dependencies"]) is not list or len(value["dependencies"]) > 100):
        raise ValueError("Invalid plugin manifest.")
    configuration = value.get("process_config", [])
    if (type(configuration) is not list or len(configuration) > 32
            or any(type(name) is not str or len(name) > 100 or re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None
                   or name.startswith(("WHAB_", "PYTHON", "PIP_")) or name == "TOKEN_ENCRYPTION_KEY"
                   for name in configuration) or len(set(configuration)) != len(configuration)):
        raise ValueError("Invalid plugin process configuration.")
    seen = {value["id"]}
    for dependency in value["dependencies"]:
        if (type(dependency) is not dict or dependency.keys() != {"id", "version"}
                or not _identifier(dependency["id"]) or dependency["id"] in seen
                or type(dependency["version"]) is not str or len(dependency["version"]) > 200):
            raise ValueError("Invalid plugin dependency.")
        if parse_specifier is not None:
            parse_specifier(dependency["version"])
        seen.add(dependency["id"])
    return value
