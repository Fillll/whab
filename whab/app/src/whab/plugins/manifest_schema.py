"""Dependency manifest structure, shared with dependency-free source tooling."""

import re

CATEGORIES = ("interface", "ingestion", "insight", "storage", "automation", "extension")


def _identifier(value):
    return type(value) is str and len(value) <= 64 and re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", value) is not None


def validate_manifest(value, *, parse_specifier=None):
    if (type(value) is not dict or value.keys() != {"schema_version", "id", "category", "dependencies"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not _identifier(value["id"]) or value["category"] not in (*CATEGORIES, "example")
            or type(value["dependencies"]) is not list or len(value["dependencies"]) > 100):
        raise ValueError("Invalid plugin manifest.")
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
