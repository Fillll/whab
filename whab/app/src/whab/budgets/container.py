"""Bounded WHAB containers with opaque, plugin-owned payload namespaces."""

import copy
import io
import json
import stat
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from whab.domain.errors import StoreError
from whab.plugins.sdk import valid_plugin_name

from .credentials import MAX_CREDENTIAL_BYTES, decrypt_credentials
from .document import MAX_BUNDLE_BYTES, MAX_MANIFEST_BYTES, _json, decode_bundle, validate_manifest

MAX_CONTAINER_FILES = 1000
_METADATA = {"settings.json": "settings", "state.json": "state"}


@dataclass(frozen=True, slots=True)
class BudgetContainer:
    manifest: dict = field(repr=False)
    files: dict[str, bytes] = field(repr=False)
    legacy_ledger: dict | None = field(default=None, repr=False)
    credentials: dict | None = field(default=None, repr=False)
    credential_blob: bytes | None = field(default=None, repr=False)


def validate_file_name(name):
    """Validate a complete archive path without extracting anything to disk."""
    if (
        type(name) is not str or not 1 <= len(name) <= 1024
        or "\\" in name or ":" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise StoreError("Plugin data has an unsafe container path.")
    parts = name.split("/")
    if (
        len(parts) < 3 or parts[0] != "plugins" or not valid_plugin_name(parts[1])
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise StoreError("Plugin data has an unsafe container path.")
    try:
        name.encode("utf-8")
    except UnicodeError:
        raise StoreError("Plugin data has an unsafe container path.") from None
    return name


def _entries(archive):
    entries = archive.infolist()
    if not 1 <= len(entries) <= MAX_CONTAINER_FILES:
        raise ValueError()
    names = set()
    expanded = 0
    for entry in entries:
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (
            entry.filename in names or entry.orig_filename != entry.filename
            or entry.is_dir() or mode not in {0, stat.S_IFREG}
            or entry.external_attr & 0x10
            or entry.flag_bits & 1
            or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        ):
            raise ValueError()
        names.add(entry.filename)
        expanded += entry.file_size
        if expanded > MAX_BUNDLE_BYTES:
            raise ValueError()
    if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
        raise ValueError()
    return entries


def _read(archive, entry):
    raw = archive.read(entry)
    if len(raw) != entry.file_size:
        raise ValueError()
    return raw


def _require_storage(manifest):
    if not any(
        plugin["id"] == manifest["storage"] and plugin["enabled"]
        for plugin in manifest["plugins"]
    ):
        raise StoreError("The container must declare its selected storage plugin as enabled.")


def decode_container(raw, password=None):
    """Read legacy ledgers, v2 namespaces, and explicitly versioned v3 state."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_BUNDLE_BYTES:
        raise StoreError("Choose a WHAB budget no larger than 50 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = _entries(archive)
            manifest = _json(_read(archive, archive.getinfo("manifest.json")))
            if type(manifest) is not dict or type(manifest.get("formatVersion")) is not int:
                raise ValueError()
            if manifest["formatVersion"] == 1 or (manifest["formatVersion"] == 3 and "ledger.json" in archive.namelist()):
                legacy_manifest, ledger = decode_bundle(raw)
                return BudgetContainer(legacy_manifest, {}, legacy_ledger=ledger)
            if manifest["formatVersion"] not in {2, 3}:
                raise ValueError()
            if type(manifest.get("plugins")) is not list:
                raise ValueError()
            normalized = copy.deepcopy(manifest)
            normalized["formatVersion"] = 1 if manifest["formatVersion"] == 2 else 3
            for plugin in normalized["plugins"]:
                keys = {"id", "enabled", "apiVersion"}
                if normalized["formatVersion"] == 3:
                    keys.add("stateVersion")
                if type(plugin) is not dict or plugin.keys() != keys:
                    raise ValueError()
                plugin.update(settings={}, state={})
            normalized = validate_manifest(normalized)
            _require_storage(normalized)
            owners = {plugin["id"]: plugin for plugin in normalized["plugins"]}
            files = {}
            credential_blob = None
            for entry in entries:
                name = entry.filename
                if name == "manifest.json":
                    continue
                if name == "credentials.enc":
                    if entry.file_size > MAX_CREDENTIAL_BYTES:
                        raise ValueError()
                    credential_blob = _read(archive, entry)
                    continue
                validate_file_name(name)
                _, owner, relative = name.split("/", 2)
                if owner not in owners:
                    raise ValueError()
                if relative in _METADATA:
                    if entry.file_size > MAX_MANIFEST_BYTES:
                        raise ValueError()
                    value = _json(_read(archive, entry))
                    if type(value) is not dict:
                        raise ValueError()
                    owners[owner][_METADATA[relative]] = value
                else:
                    files[name] = _read(archive, entry)
            normalized = validate_manifest(normalized)
            credentials = decrypt_credentials(normalized["id"], credential_blob, password) if credential_blob is not None else None
            return BudgetContainer(normalized, files, credentials=credentials, credential_blob=credential_blob)
    except StoreError:
        raise
    except (ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError, UnicodeError, OSError, zlib.error, NotImplementedError, RecursionError, EOFError):
        raise StoreError("This file is not a supported WHAB budget container.") from None


def encode_container(manifest, files, credential_blob=None):
    """Write v2 or v3 metadata and opaque files without executable serialization."""
    manifest = validate_manifest(manifest)
    _require_storage(manifest)
    if not isinstance(files, Mapping):
        raise StoreError("Plugin container files must be a filename-to-bytes mapping.")
    owners = {plugin["id"] for plugin in manifest["plugins"]}
    payloads = {}
    for name, value in files.items():
        validate_file_name(name)
        _, owner, relative = name.split("/", 2)
        if owner not in owners or relative in _METADATA or type(value) is not bytes:
            raise StoreError("Plugin container files must belong to their declared namespace.")
        payloads[name] = value
    wire = copy.deepcopy(manifest)
    wire["formatVersion"] = 2 if manifest["formatVersion"] == 1 else 3
    for plugin in wire["plugins"]:
        for filename, key in _METADATA.items():
            value = plugin.pop(key)
            payloads[f"plugins/{plugin['id']}/{filename}"] = json.dumps(value, ensure_ascii=False).encode("utf-8")
    manifest_raw = json.dumps(wire, ensure_ascii=False).encode("utf-8")
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise StoreError("The budget manifest exceeds the supported size.")
    payloads["manifest.json"] = manifest_raw
    if credential_blob is not None:
        if type(credential_blob) is not bytes or not 1 <= len(credential_blob) <= MAX_CREDENTIAL_BYTES:
            raise StoreError("The encrypted remote credential exceeds the supported size.")
        payloads["credentials.enc"] = credential_blob
    if len(payloads) > MAX_CONTAINER_FILES or sum(map(len, payloads.values())) > MAX_BUNDLE_BYTES:
        raise StoreError("This budget exceeds the container size or file count limit.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(payloads):
            archive.writestr(name, payloads[name])
    raw = output.getvalue()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise StoreError("This budget exceeds the container size limit.")
    return raw
