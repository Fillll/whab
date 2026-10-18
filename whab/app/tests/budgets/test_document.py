"""Budget documents are bounded data, with isolated plugin settings and atomic saves."""

import io
import json
import os
import struct
import zipfile
import zlib
from copy import deepcopy
from pathlib import Path

import pytest

from whab.budgets import document
from whab.domain.errors import StoreError
from whab.domain.money import MAX_SAFE_INTEGER
from whab.plugins import API_VERSION


def ledger():
    return {
        "formatVersion": 1, "budgetStartMonth": "2026-10", "accounts": [],
        "categories": [], "transactions": [], "assignments": [],
    }


def manifest():
    return document.new_manifest("Family budget", ("sqlite", "plaid", "reports", "plaid"))


def archive(entries, *, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as bundle:
        for name, data in entries:
            bundle.writestr(name, data if isinstance(data, (str, bytes)) else json.dumps(data))
    return output.getvalue()


def test_archive_roundtrip_contains_exactly_two_data_files_and_preserves_unicode():
    settings = manifest()
    settings["name"] = "家計 budget"
    settings["plugins"][0].update(
        settings={"institution": "Example bank", "features": [True, None, "日本語", 123]},
        state={"review": {"lastMonth": "2026-09"}},
    )
    raw = document.encode_bundle(settings, ledger())
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        assert set(bundle.namelist()) == {"manifest.json", "ledger.json"}
        assert all(not entry.is_dir() for entry in bundle.infolist())
        assert json.loads(bundle.read("manifest.json")) == settings
        assert json.loads(bundle.read("ledger.json")) == {**ledger(), "futureFunding": [], "rolloverModes": []}
    assert document.decode_bundle(raw) == (settings, {**ledger(), "futureFunding": [], "rolloverModes": []})
    assert document.digest(raw) == document.digest(bytes(raw))
    assert len(document.digest(raw)) == 64
    assert document.digest(raw) != document.digest(raw + b"changed")


def test_new_budget_identifiers_are_unique_and_storage_is_recorded_once():
    first = document.new_manifest("  Household  ", ("sqlite", "plaid", "plaid", "reports"))
    second = document.new_manifest("Household")
    assert first["id"] != second["id"]
    assert first["name"] == "Household"
    assert first["storage"] == "sqlite"
    assert [plugin["id"] for plugin in first["plugins"]] == ["sqlite", "plaid", "reports"]
    assert all(plugin["apiVersion"] == API_VERSION for plugin in first["plugins"])


@pytest.mark.parametrize("name", [None, 1, "", "   ", "x\x00", "x" * 101])
def test_invalid_new_budget_names_raise_authored_errors(name):
    with pytest.raises(StoreError):
        document.new_manifest(name)


def test_manifest_validation_and_opened_settings_are_independent_between_budgets():
    original = manifest()
    original["plugins"][0].update(settings={"nested": {"list": [1]}}, state={"seen": ["old"]})
    validated = document.validate_manifest(original)
    raw = document.encode_bundle(original, ledger())
    first, _ = document.decode_bundle(raw)
    second, _ = document.decode_bundle(raw)
    original["plugins"][0]["settings"]["nested"]["list"].append(2)
    validated["plugins"][0]["state"]["seen"].append("validated only")
    first["plugins"][0]["settings"]["nested"]["list"].append(3)
    assert second["plugins"][0]["settings"] == {"nested": {"list": [1]}}
    assert second["plugins"][0]["state"] == {"seen": ["old"]}


@pytest.mark.parametrize("bad", [None, [], {}, {"unexpected": 1}])
def test_manifest_requires_exact_fields(bad):
    with pytest.raises(StoreError, match="manifest"):
        document.validate_manifest(bad)


@pytest.mark.parametrize("field,value", [
    ("formatVersion", True), ("formatVersion", "1"), ("formatVersion", 2),
    ("id", "not-a-uuid"), ("id", 3), ("id", "6A6646B6-1306-4D75-956F-33DA5DAFEFA8"),
    ("name", []), ("name", ""), ("name", "x" * 101), ("name", "private\x00name"),
    ("storage", "../sqlite"), ("storage", "SQLite"), ("storage", ""),
    ("plugins", ()), ("plugins", [{}] * 101),
])
def test_manifest_fields_are_validated_without_coercion(field, value):
    data = manifest()
    data[field] = value
    with pytest.raises(StoreError, match="manifest"):
        document.validate_manifest(data)


@pytest.mark.parametrize("field,value", [
    ("id", "Bad Plugin"), ("id", "../plugin"), ("id", "x" * 65),
    ("enabled", 1), ("enabled", "true"), ("apiVersion", True),
    ("apiVersion", "1"), ("apiVersion", API_VERSION + 1),
    ("settings", []), ("state", None),
])
def test_plugin_ids_versions_and_settings_shapes_are_exact(field, value):
    data = manifest()
    data["plugins"][0][field] = value
    with pytest.raises(StoreError, match="manifest"):
        document.validate_manifest(data)


@pytest.mark.parametrize("plugin", [None, {}, {"id": "plaid", "code": "print('must not run')"}])
def test_plugins_cannot_embed_code_fields_or_omit_required_metadata(plugin):
    data = manifest()
    data["plugins"] = [plugin]
    with pytest.raises(StoreError, match="manifest"):
        document.validate_manifest(data)


def test_duplicate_plugin_ids_are_rejected():
    data = manifest()
    data["plugins"].append(deepcopy(data["plugins"][0]))
    with pytest.raises(StoreError, match="manifest"):
        document.validate_manifest(data)


@pytest.mark.parametrize("section", ["settings", "state"])
@pytest.mark.parametrize("key", [
    "password", "PLAID_SECRET", "access_token", "refresh-token", "encryption_key", "api_key",
    "apiKey", "accessToken", "refreshToken", "encryptionKey", "privateKey", "credentials", "token",
    "SERVER_TOKEN", "CLIENT_TOKEN", "authorization", "bearer",
])
def test_credentials_are_rejected_recursively_in_plugin_settings_and_state(section, key):
    data = manifest()
    data["plugins"][0][section] = {"nested": [{key: "synthetic-secret"}]}
    with pytest.raises(StoreError, match="credentials"):
        document.portable_values(data["plugins"][0][section])
    with pytest.raises(StoreError, match="manifest"):
        document.validate_manifest(data)


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"), float(MAX_SAFE_INTEGER) * 2, b"binary", (1, 2), {1},
    "x" * 100001, "text\x00tail", MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1,
    {1: "non-string key"}, {"": "empty key"}, {"x\x00": "bad key"}, {"x" * 201: 1},
    [None] * 10001, {str(index): None for index in range(10001)},
])
def test_plugin_values_are_bounded_portable_json(value):
    with pytest.raises(StoreError):
        document.portable_values(value)


def test_plugin_json_boundaries_are_inclusive_and_copied():
    data = {
        "flags": [True, False, None], "text": "x" * 100000, "threshold": 0.9,
        "integerBounds": [-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER],
        "fields": {str(index): index for index in range(10000)},
    }
    copied = document.portable_values(data)
    assert copied == data
    assert copied is not data and copied["flags"] is not data["flags"]


def test_deep_and_cyclic_plugin_state_is_rejected():
    nested = {}
    for _ in range(17):
        nested = {"child": nested}
    with pytest.raises(StoreError, match="deeply"):
        document.portable_values(nested)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(StoreError, match="deeply"):
        document.portable_values(cycle)


def test_manifest_total_size_is_bounded(monkeypatch):
    monkeypatch.setattr(document, "MAX_MANIFEST_BYTES", 100)
    with pytest.raises(StoreError, match="manifest"):
        document.validate_manifest(manifest())


@pytest.mark.parametrize("raw", [b"", b"not a zip", b"PK\x03\x04truncated", None, 42, "text", []])
def test_invalid_and_nonbinary_bundles_raise_authored_errors(raw):
    with pytest.raises(StoreError):
        document.decode_bundle(raw)


@pytest.mark.parametrize("names", [
    ["manifest.json"], ["manifest.json", "ledger.json", "plugin.py"],
    ["manifest.json", "../ledger.json"], ["manifest.json", "nested/ledger.json"],
    ["ledger.json", "plugins/"],
])
def test_archive_has_exact_names_and_file_count(names):
    raw = archive([(name, {}) for name in names])
    with pytest.raises(StoreError, match="bundle"):
        document.decode_bundle(raw)


def test_duplicate_archive_members_are_rejected():
    with pytest.warns(UserWarning, match="Duplicate name"):
        raw = archive([("manifest.json", manifest()), ("manifest.json", ledger())])
    with pytest.raises(StoreError):
        document.decode_bundle(raw)


@pytest.mark.parametrize("invalid_json", [
    b'{"formatVersion":1,"formatVersion":1}',
    b'{"nested":{"duplicate":1,"duplicate":2}}',
    b'{"number":NaN}', b'{"number":Infinity}', b'{"number":-Infinity}',
    b'not json', b'\xff\xfeinvalid utf-8',
])
@pytest.mark.parametrize("member", ["manifest.json", "ledger.json"])
def test_duplicate_json_keys_nonfinite_constants_and_invalid_encodings_are_rejected(member, invalid_json):
    payloads = {"manifest.json": manifest(), "ledger.json": ledger()}
    payloads[member] = invalid_json
    with pytest.raises(StoreError):
        document.decode_bundle(archive(payloads.items()))


def test_bundle_revalidates_ledger_fields_and_manifest_versions():
    bad_ledger = {**ledger(), "items": [{"token": "never accepted"}]}
    with pytest.raises(StoreError):
        document.decode_bundle(archive([("manifest.json", manifest()), ("ledger.json", bad_ledger)]))
    bad_manifest = manifest()
    bad_manifest["plugins"][0]["apiVersion"] += 1
    with pytest.raises(StoreError):
        document.decode_bundle(archive([("manifest.json", bad_manifest), ("ledger.json", ledger())]))


def test_compressed_bomb_is_rejected_before_json_parsing(monkeypatch):
    raw = archive([("manifest.json", manifest()), ("ledger.json", " " * 10000)])
    monkeypatch.setattr(document, "MAX_BUNDLE_BYTES", 2048)
    assert len(raw) < document.MAX_BUNDLE_BYTES
    with pytest.raises(StoreError, match="bundle"):
        document.decode_bundle(raw)


def test_manifest_expansion_limit_is_checked_separately(monkeypatch):
    raw = archive([("manifest.json", " " * 10000), ("ledger.json", ledger())])
    monkeypatch.setattr(document, "MAX_MANIFEST_BYTES", 1000)
    with pytest.raises(StoreError, match="bundle"):
        document.decode_bundle(raw)


def test_raw_input_size_and_output_expansion_are_bounded(monkeypatch):
    settings = manifest()
    monkeypatch.setattr(document, "MAX_BUNDLE_BYTES", 100)
    with pytest.raises(StoreError, match="50 MiB"):
        document.decode_bundle(b"x" * 101)
    with pytest.raises(StoreError, match="size limit"):
        document.encode_bundle(settings, ledger())


def test_zip_overhead_cannot_exceed_output_size_limit(monkeypatch):
    settings = document.new_manifest("A")
    size = len(json.dumps(settings, ensure_ascii=False).encode()) + len(json.dumps(ledger()).encode())
    monkeypatch.setattr(document, "MAX_BUNDLE_BYTES", size)
    with pytest.raises(StoreError, match="size limit"):
        document.encode_bundle(settings, ledger())


def test_encrypted_zip_members_are_rejected():
    raw = bytearray(archive([("manifest.json", manifest()), ("ledger.json", ledger())]))
    # The stdlib cannot create encrypted ZIPs; set the standard flags in a fixture archive.
    local = raw.index(b"PK\x03\x04")
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, local + 6, struct.unpack_from("<H", raw, local + 6)[0] | 1)
    struct.pack_into("<H", raw, central + 8, struct.unpack_from("<H", raw, central + 8)[0] | 1)
    with pytest.raises(StoreError):
        document.decode_bundle(bytes(raw))


def test_corrupt_member_checksum_raises_supported_bundle_error():
    settings = manifest()
    raw = bytearray(archive(
        [("manifest.json", settings), ("ledger.json", ledger())], compression=zipfile.ZIP_STORED,
    ))
    position = raw.index(b'"budgetStartMonth"')
    raw[position] = ord("X")
    with pytest.raises(StoreError):
        document.decode_bundle(bytes(raw))


def test_corrupt_deflate_stream_raises_supported_bundle_error():
    raw = bytearray(archive([("manifest.json", manifest()), ("ledger.json", ledger())]))
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        info = bundle.getinfo("ledger.json")
    name_size, extra_size = struct.unpack_from("<HH", raw, info.header_offset + 26)
    start = info.header_offset + 30 + name_size + extra_size
    # DEFLATE block type 3 is reserved and must fail before JSON decoding.
    raw[start] = (raw[start] & ~7) | 7
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        with pytest.raises(zlib.error):
            bundle.read("ledger.json")
    with pytest.raises(StoreError, match="supported WHAB budget bundle"):
        document.decode_bundle(bytes(raw))


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
@pytest.mark.parametrize("location", ["name", "state-key", "state-value", "settings-value"])
def test_escaped_lone_surrogates_in_manifests_are_rejected_before_encoding_or_open(surrogate, location):
    data = manifest()
    if location == "name":
        data["name"] = "Budget " + surrogate
    elif location == "state-key":
        data["plugins"][0]["state"] = {surrogate: "invalid key"}
    elif location == "state-value":
        data["plugins"][0]["state"] = {"text": surrogate}
    else:
        data["plugins"][0]["settings"] = {"text": surrogate}
    with pytest.raises(StoreError):
        document.encode_bundle(data, ledger())
    # JSON can represent escaped surrogates even though they are not valid UTF-8 text.
    raw = archive([("manifest.json", json.dumps(data)), ("ledger.json", ledger())])
    with pytest.raises(StoreError):
        document.decode_bundle(raw)


@pytest.mark.parametrize("value", ["\ud800", {"\udfff": "field"}, {"nested": ["\ud800"]}])
def test_plugin_strings_and_keys_report_clear_unicode_validation_error(value):
    with pytest.raises(StoreError, match="valid Unicode"):
        document.portable_values(value)


def test_atomic_save_creates_private_directories_replaces_file_and_removes_temporary(tmp_path):
    path = tmp_path / "new" / "budget.whab"
    document.atomic_write(path, b"first")
    document.atomic_write(path, b"second")
    assert path.read_bytes() == b"second"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("failure", ["mkstemp", "fsync", "replace"])
def test_failed_atomic_save_preserves_previous_budget_and_cleans_temporary_files(tmp_path, monkeypatch, failure):
    path = tmp_path / "budget.whab"
    path.write_bytes(b"original budget")

    def fail(*_args, **_kwargs):
        raise OSError("Simulated disk failure")

    if failure == "mkstemp":
        monkeypatch.setattr(document.tempfile, "mkstemp", fail)
    else:
        monkeypatch.setattr(document.os, failure, fail)
    with pytest.raises(OSError, match="disk failure"):
        document.atomic_write(path, b"replacement budget")
    assert path.read_bytes() == b"original budget"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_write_to_new_path_leaves_no_incomplete_budget(tmp_path, monkeypatch):
    path = Path(tmp_path) / "budget.whab"

    def fail(*_args):
        raise OSError("Simulated flush failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="flush failure"):
        document.atomic_write(path, b"incomplete")
    assert not path.exists()
    assert not list(tmp_path.iterdir())
