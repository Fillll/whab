"""Offline operation catalogs. Read installed package data without importing hooks."""

import json
import os
import tempfile
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from importlib import metadata, resources, util
from pathlib import Path


def _validation_identity():
    validator = Path(__file__).with_name("operation_schema.py").read_bytes()
    library = metadata.distribution("jsonschema")
    identity = library.read_text("METADATA") or library.read_text("PKG-INFO") or library.version
    return validator, identity


def _validated_document(raw, identifier, validation):
    document = json.loads(raw)
    # The marker attests to the exact bytes and validator, never cached document content.
    validator, identity = validation
    key = sha256(raw + identifier.encode() + validator + identity.encode()).hexdigest()
    cache = Path.home() / ".cache" / "whab" / "operation-validation"
    marker = cache / key
    try:
        valid = marker.is_file() and marker.read_bytes() == key.encode()
    except OSError:
        valid = False
    if valid:
        return document
    from .operation_schema import validate_document
    validate_document(document, identifier)
    try:
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            stream.write(key.encode())
            path = Path(stream.name)
        os.replace(path, marker)
    except OSError:
        pass  # Read-only installations retain complete validation.
    return document


@lru_cache(maxsize=1)
def _core(raw, validation):
    return _validated_document(raw, "core", validation)


def core_document(*, operations=None, _validation=None):
    raw = resources.files("whab").joinpath("operations.json").read_bytes()
    document = _core(raw, _validation or _validation_identity())
    if operations is not None:
        document = {**document, "operations": [row for row in document["operations"]
                    if (row["kind"], row["name"]) in operations]}
    return deepcopy(document)


def operation(document, kind, name):
    return next((row for row in document["operations"] if row["kind"] == kind and row["name"] == name), None)


def installed_document(entry, *, _validation=None):
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
    return deepcopy(_read_document(path, path.stat().st_mtime_ns, path.stat().st_size,
                                   entry.name, _validation or _validation_identity()))


@lru_cache(maxsize=128)
def _read_document(path, modified, size, identifier, validation):
    return _validated_document(path.read_bytes(), identifier, validation)


def plugin_document(plugin_id):
    matches = [entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin_id]
    if len(matches) != 1:
        return None
    return installed_document(matches[0])


def installed_catalog(enabled=None, *, document_plugins=None, include_versions=True, core_operations=None):
    """Discover all identities; optionally project fully validated documents."""
    from .registration import management_tool

    plugins = []
    # Share disk/metadata reads only for this catalog. A later invocation sees
    # changed validator/library identities, including cached document reloads.
    validation = _validation_identity()
    entries = sorted(metadata.entry_points(group="whab.plugins"), key=lambda row: row.name)
    for entry in entries:
        document = installed_document(entry, _validation=validation) if document_plugins is None or entry.name in document_plugins else None
        tool = management_tool(entry.name, [row for row in entries if row.name == entry.name])
        plugins.append({"id": entry.name, "version": entry.dist.version if include_versions else None,
                        "enabled": None if enabled is None or tool else entry.name in enabled,
                        **({"selection_control": "management"} if tool else {}),
                        "documented": document is not None, "documentation": document})
    return {"schema_version": 1, "source": "installed" if enabled is None else "authority",
            "core": core_document(operations=core_operations, _validation=validation), "plugins": plugins}
