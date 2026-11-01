"""Offline operation catalogs. Read installed package data without importing hooks."""

import json
from copy import deepcopy
from functools import lru_cache
from importlib import metadata, resources, util
from pathlib import Path

from .operation_schema import validate_document


@lru_cache(maxsize=1)
def _core():
    return validate_document(json.loads(resources.files("whab").joinpath("operations.json").read_bytes()), "core")


def core_document():
    return deepcopy(_core())


def operation(document, kind, name):
    return next((row for row in document["operations"] if row["kind"] == kind and row["name"] == name), None)


def installed_document(entry):
    """Legacy distributions without documentation remain explicitly identifiable."""
    package = entry.value.split(":")[0].split(".")[0]
    try:
        spec = util.find_spec(package)
    except (ImportError, ValueError):
        return None
    locations = spec.submodule_search_locations if spec else None
    if not locations:
        return None
    path = Path(next(iter(locations))) / "operations.json"
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_size > 512 * 1024:
        raise ValueError("Invalid installed operation documentation resource.")
    return deepcopy(_read_document(path, path.stat().st_mtime_ns, path.stat().st_size, entry.name))


@lru_cache(maxsize=128)
def _read_document(path, modified, size, identifier):
    return validate_document(json.loads(path.read_bytes()), identifier)


def plugin_document(plugin_id):
    matches = [entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin_id]
    if len(matches) != 1:
        return None
    return installed_document(matches[0])


def installed_catalog(enabled=None):
    """Descriptions contain authored examples, never live settings or credentials."""
    plugins = []
    for entry in sorted(metadata.entry_points(group="whab.plugins"), key=lambda row: row.name):
        document = installed_document(entry)
        plugins.append({"id": entry.name, "version": entry.dist.version,
                        "enabled": None if enabled is None else entry.name in enabled,
                        "documented": document is not None, "documentation": document})
    return {"schema_version": 1, "source": "installed" if enabled is None else "authority",
            "core": core_document(), "plugins": plugins}
