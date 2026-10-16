"""Data-only budget bundles. Opening a bundle never installs or imports code."""

import copy
import hashlib
import io
import json
import math
import os
import tempfile
import zipfile
import zlib
from pathlib import Path
from uuid import UUID, uuid4

from whab.domain.errors import StoreError
from whab.domain.money import MAX_SAFE_INTEGER
from whab.domain.portable import validate_portable_budget
from whab.plugins import API_VERSION
from whab.plugins.sdk import valid_plugin_name

MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _utf8(value):
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise StoreError("Budget text must contain valid Unicode characters.") from None


def portable_values(value, depth=0):
    """Copy bounded JSON settings/state; connection secrets have a separate home."""
    if depth > 16:
        raise StoreError("Plugin data is nested too deeply.")
    if type(value) is dict:
        if len(value) > 10000:
            raise StoreError("Plugin data contains too many fields.")
        result = {}
        for key, item in value.items():
            if type(key) is not str or not key.strip() or len(key) > 200 or "\x00" in key:
                raise StoreError("Plugin data has an invalid field name.")
            _utf8(key)
            normalized = "".join(character for character in key.lower() if character.isalnum())
            if normalized.endswith("token") or any(word in normalized for word in (
                "password", "secret", "accesstoken", "refreshtoken", "encryptionkey", "apikey",
                "privatekey", "credentials", "authorization", "bearer",
            )):
                raise StoreError("Store plugin credentials on this machine, outside the budget bundle.")
            result[key] = portable_values(item, depth + 1)
        return result
    if type(value) is list:
        if len(value) > 10000:
            raise StoreError("Plugin data contains too many entries.")
        return [portable_values(item, depth + 1) for item in value]
    if value is None or type(value) in {str, int, float, bool}:
        if type(value) is str and (len(value) > 100000 or "\x00" in value):
            raise StoreError("Plugin text exceeds the supported limit.")
        if type(value) is str:
            _utf8(value)
        if type(value) is int and abs(value) > MAX_SAFE_INTEGER:
            raise StoreError("Plugin integers exceed the supported range.")
        if type(value) is float and (not math.isfinite(value) or abs(value) > MAX_SAFE_INTEGER):
            raise StoreError("Plugin numbers must be finite and within the supported range.")
        return value
    raise StoreError("Plugin settings and state must contain portable JSON values.")


def validate_manifest(value):
    try:
        if type(value) is not dict or set(value) != {"formatVersion", "id", "name", "storage", "plugins"}:
            raise ValueError()
        if type(value["formatVersion"]) is not int or value["formatVersion"] != 1:
            raise ValueError()
        if str(UUID(value["id"])) != value["id"]:
            raise ValueError()
        if type(value["name"]) is not str or not value["name"].strip() or len(value["name"]) > 100 or "\x00" in value["name"]:
            raise ValueError()
        _utf8(value["name"])
        if not valid_plugin_name(value["storage"]):
            raise ValueError()
        if type(value["plugins"]) is not list or len(value["plugins"]) > 100:
            raise ValueError()
        ids = set()
        for plugin in value["plugins"]:
            if type(plugin) is not dict or set(plugin) != {"id", "enabled", "apiVersion", "settings", "state"}:
                raise ValueError()
            if not valid_plugin_name(plugin["id"]) or plugin["id"] in ids:
                raise ValueError()
            ids.add(plugin["id"])
            if type(plugin["enabled"]) is not bool or type(plugin["apiVersion"]) is not int or plugin["apiVersion"] != API_VERSION:
                raise ValueError()
            if type(plugin["settings"]) is not dict or type(plugin["state"]) is not dict:
                raise ValueError()
            portable_values(plugin["settings"])
            portable_values(plugin["state"])
        if len(json.dumps(value).encode()) > MAX_MANIFEST_BYTES:
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise StoreError("This budget has an invalid or incompatible plugin manifest.") from None
    return copy.deepcopy(value)


def new_manifest(name, plugins=(), storage="sqlite"):
    if type(name) is not str:
        raise StoreError("Choose a name for this budget.")
    return validate_manifest({
        "formatVersion": 1, "id": str(uuid4()), "name": name.strip(), "storage": storage,
        "plugins": [{"id": name, "enabled": True, "apiVersion": API_VERSION, "settings": {}, "state": {}} for name in dict.fromkeys((storage, *plugins))],
    })


def decode_bundle(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_BUNDLE_BYTES:
        raise StoreError("Choose a WHAB budget no larger than 50 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) != 2 or {info.filename for info in entries} != {"manifest.json", "ledger.json"}:
                raise ValueError()
            if any(info.flag_bits & 1 for info in entries) or sum(info.file_size for info in entries) > MAX_BUNDLE_BYTES:
                raise ValueError()
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                raise ValueError()
            manifest = validate_manifest(_json(archive.read("manifest.json")))
            ledger = validate_portable_budget(_json(archive.read("ledger.json")))
            return manifest, ledger
    except (ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError, UnicodeError, OSError, zlib.error):
        raise StoreError("This file is not a supported WHAB budget bundle.") from None


def encode_bundle(manifest, ledger):
    manifest = validate_manifest(manifest)
    ledger = validate_portable_budget(ledger)
    manifest_raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    ledger_raw = json.dumps(ledger, ensure_ascii=False).encode("utf-8")
    if len(manifest_raw) + len(ledger_raw) > MAX_BUNDLE_BYTES:
        raise StoreError("This budget exceeds the portable bundle size limit.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_raw)
        archive.writestr("ledger.json", ledger_raw)
    raw = output.getvalue()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise StoreError("This budget exceeds the portable bundle size limit.")
    return raw


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".whab-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
