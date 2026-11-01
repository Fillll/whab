"""Required packaged operation documentation checks; no plugin code executes."""

import json
import tomllib
from importlib import util
from pathlib import Path

from .operation_schema import validate_document


def _read(path, identifier):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
        raise ValueError("Package a bounded regular operations.json resource.")
    return validate_document(json.loads(path.read_bytes()), identifier)


def check_project_documentation(directory):
    directory = Path(directory)
    metadata = tomllib.loads((directory / "pyproject.toml").read_text())
    entries = metadata["project"]["entry-points"]["whab.plugins"]
    if len(entries) != 1:
        raise ValueError("Declare exactly one plugin entry point.")
    identifier, entry = next(iter(entries.items()))
    package = entry.split(":")[0].split(".")[0]
    if "operations.json" not in metadata["tool"]["setuptools"]["package-data"][package]:
        raise ValueError("Include operations.json in package data.")
    readme = (directory / "README.md").read_text()
    if "## Operation documentation" not in readme or f"](src/{package}/operations.json)" not in readme:
        raise ValueError("README must link the packaged operation documentation.")
    return _read(directory / "src" / package / "operations.json", identifier)


def check_entry_documentation(entry):
    package = entry.value.split(":")[0].split(".")[0]
    spec = util.find_spec(package)
    locations = spec.submodule_search_locations if spec else None
    if not locations:
        raise ValueError("Install the candidate's documented package before conformance checks.")
    return _read(Path(next(iter(locations))) / "operations.json", entry.name)
