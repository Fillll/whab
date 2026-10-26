"""Data-only plugin identities and explicit dependencies; never import plugin code."""

import json
from importlib import metadata, util
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from .manifest_schema import CATEGORIES as CATEGORIES
from .manifest_schema import validate_manifest as _validate_manifest


def validate_manifest(value):
    return _validate_manifest(value, parse_specifier=SpecifierSet)


def installed_manifest(entry):
    """Find the top-level package resource without executing its __init__ module."""
    if type(getattr(entry, "value", None)) is not str or not entry.value:
        return None
    package = entry.value.split(":")[0].split(".")[0]
    try:
        spec = util.find_spec(package)
    except (ImportError, ValueError):
        return None
    try:
        locations = spec.submodule_search_locations if spec else None
        if not locations:
            return None  # Legacy external distributions remain compatible.
        path = Path(next(iter(locations))) / "plugin.json"
        if not path.is_file():
            return None
        if path.stat().st_size > 65536:
            raise ValueError("Oversized manifest")
        result = validate_manifest(json.loads(path.read_text(encoding="utf-8")))
        if result["id"] != entry.name:
            raise ValueError("Manifest identity mismatch")
        return result
    except (AttributeError, ImportError, OSError, ValueError, TypeError, StopIteration):
        from .runtime import PluginError

        raise PluginError("Plugin metadata is invalid. Reinstall its package.", code="invalid_manifest", plugin_id=entry.name) from None


def validate_dependencies(identifiers, entries=None):
    """Require selected providers and compatible package versions before activation."""
    from .runtime import PluginError

    identifiers = set(identifiers)
    if not identifiers:
        return
    entries = entries if entries is not None else metadata.entry_points(group="whab.plugins")
    installed = {}
    for entry in entries:
        installed.setdefault(entry.name, []).append(entry)
    edges = {}
    for identifier in sorted(identifiers):
        matches = installed.get(identifier, ())
        if len(matches) != 1:
            continue  # Discovery owns missing and duplicate registrations.
        manifest = installed_manifest(matches[0])
        dependencies = manifest["dependencies"] if manifest else ()
        edges[identifier] = [item["id"] for item in dependencies]
        for dependency in dependencies:
            providers = installed.get(dependency["id"], ())
            if (dependency["id"] not in identifiers or len(providers) != 1
                    or Version(providers[0].dist.version) not in SpecifierSet(dependency["version"])):
                raise PluginError(
                    f"Plugin '{identifier}' requires enabled plugin '{dependency['id']}' ({dependency['version'] or 'any version'}). Enable a compatible dependency before saving.",
                    code="plugin_dependency", plugin_id=identifier,
                )
    visited, visiting = set(), set()

    def visit(identifier):
        if identifier in visiting:
            raise PluginError("Plugin dependencies contain a cycle.", code="dependency_cycle", plugin_id=identifier)
        if identifier not in visited:
            visiting.add(identifier)
            for dependency in edges.get(identifier, ()):
                visit(dependency)
            visiting.remove(identifier)
            visited.add(identifier)

    for identifier in edges:
        visit(identifier)
