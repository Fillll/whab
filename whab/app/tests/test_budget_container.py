"""V2 containers preserve plugin data while enforcing bounded, safe namespaces."""

import io
import json
import stat
import warnings
import zipfile

import pytest

from whab.budgets import container
from whab.budgets.credentials import encrypt_credentials
from whab.budgets.document import encode_bundle, new_manifest
from whab.domain.errors import StoreError


def manifest():
    return new_manifest("Household", ("future-plugin",))


def ledger():
    return {"formatVersion": 1, "budgetStartMonth": "2026-10", "accounts": [],
            "categories": [], "transactions": [], "assignments": []}


def wire_manifest():
    value = manifest()
    value["formatVersion"] = 2
    for plugin in value["plugins"]:
        plugin.pop("settings")
        plugin.pop("state")
    return value


def archive(entries):
    output = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        warnings.simplefilter("ignore", UserWarning)
        for name, value in entries:
            bundle.writestr(name, value if isinstance(value, bytes) else json.dumps(value).encode())
    return output.getvalue()


def test_v2_roundtrip_preserves_binary_and_disabled_unknown_plugin_files():
    settings = manifest()
    settings["plugins"][1].update(enabled=False, settings={"theme": "日本語"}, state={"seen": [1, 2]})
    files = {
        "plugins/sqlite/budget.sqlite": b"opaque\x00database\xff",
        "plugins/future-plugin/nested/model.bin": b"unknown-version-data",
    }
    raw = container.encode_container(settings, files)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        wire = json.loads(bundle.read("manifest.json"))
        assert wire["formatVersion"] == 2
        assert all(set(plugin) == {"id", "enabled", "apiVersion"} for plugin in wire["plugins"])
        assert "ledger.json" not in bundle.namelist()
        assert json.loads(bundle.read("plugins/future-plugin/settings.json")) == {"theme": "日本語"}
    restored = container.decode_container(raw)
    assert restored.manifest == settings and restored.files == files
    assert restored.legacy_ledger is None
    assert restored.credentials is None and restored.credential_blob is None
    assert container.decode_container(container.encode_container(restored.manifest, restored.files)).files == files


def test_container_does_not_require_or_fetch_a_financial_ledger():
    settings = new_manifest("Remote connection", storage="client")
    files = {"plugins/client/connection.json": b'{"origin":"https://budget.example"}'}
    restored = container.decode_container(container.encode_container(settings, files))
    assert restored.files == files and restored.legacy_ledger is None


def test_v1_manifest_and_ledger_remain_readable():
    settings, records = manifest(), ledger()
    restored = container.decode_container(encode_bundle(settings, records))
    assert restored.manifest == settings and restored.legacy_ledger == records
    assert restored.files == {} and restored.credentials is None


def test_credentials_are_optional_preserved_and_hidden_from_repr():
    settings = new_manifest("Remote connection", storage="client")
    profile = {"client": {"CLIENT_URL": "https://budget.example", "CLIENT_BUDGET_ID": "default",
                          "CLIENT_TOKEN": "fictional-client-token-for-unit-tests-only"}}
    password = "fictional strong password"
    vault = encrypt_credentials(settings["id"], profile, password)
    raw = container.encode_container(settings, {}, vault)
    with pytest.raises(StoreError, match="password"):
        container.decode_container(raw)
    restored = container.decode_container(raw, password)
    assert restored.credentials == profile and restored.credential_blob == vault
    assert "fictional" not in repr(restored) and "budget.example" not in repr(restored)
    assert container.decode_container(container.encode_container(restored.manifest, restored.files)).credentials is None
    assert container.decode_container(container.encode_container(restored.manifest, restored.files, vault), password).credential_blob == vault


@pytest.mark.parametrize("name", [
    "", "/plugins/sqlite/x", "plugins/sqlite/../x", "plugins/sqlite/./x",
    "plugins/sqlite//x", "plugins/sqlite/", "plugins/SQLite/x", "plugins/x",
    "other/sqlite/x", "plugins/sqlite/C:/x", "plugins/sqlite/x\\y",
    "plugins/sqlite/a\x00b", "plugins/sqlite/a\nb", "plugins/sqlite/\ud800",
])
def test_plugin_paths_cannot_escape_their_namespace(name):
    with pytest.raises(StoreError, match="unsafe"):
        container.validate_file_name(name)


@pytest.mark.parametrize("files", [
    [], {"plugins/undeclared/x": b"data"},
    {"plugins/sqlite/x": "not-bytes"},
    {"plugins/sqlite/settings.json": b"{}"},
    {"plugins/sqlite/state.json": b"{}"},
])
def test_encoder_only_accepts_declared_opaque_files(files):
    with pytest.raises(StoreError):
        container.encode_container(manifest(), files)


@pytest.mark.parametrize("extra", [
    ("ledger.json", b"{}"), ("unknown.txt", b"data"),
    ("plugins/undeclared/x", b"data"), ("plugins/sqlite/../x", b"data"),
])
def test_decoder_rejects_unknown_roots_or_undeclared_payload_owners(extra):
    with pytest.raises(StoreError):
        container.decode_container(archive([("manifest.json", wire_manifest()), extra]))


def test_archive_rejects_duplicate_files_directories_and_symlinks():
    for name in ("manifest.json", "plugins/sqlite/x"):
        values = [("manifest.json", wire_manifest()), ("plugins/sqlite/x", b"data"), (name, b"{}")]
        with pytest.raises(StoreError):
            container.decode_container(archive(values))
    for kind, name in ((stat.S_IFDIR, "plugins/sqlite/directory/"), (stat.S_IFLNK, "plugins/sqlite/link")):
        entry = zipfile.ZipInfo(name)
        entry.create_system = 3
        entry.external_attr = (kind | 0o700) << 16
        with pytest.raises(StoreError):
            container.decode_container(archive([("manifest.json", wire_manifest()), (entry, b"target")]))
    dos_directory = zipfile.ZipInfo("plugins/sqlite/directory")
    dos_directory.create_system = 0
    dos_directory.external_attr = 0x10
    with pytest.raises(StoreError):
        container.decode_container(archive([("manifest.json", wire_manifest()), (dos_directory, b"")]))


@pytest.mark.parametrize("disabled", [False, True])
def test_v2_selected_storage_must_be_declared_and_enabled(disabled):
    settings = manifest()
    if disabled:
        settings["plugins"][0]["enabled"] = False
    else:
        settings["plugins"].pop(0)
    with pytest.raises(StoreError, match="storage plugin as enabled"):
        container.encode_container(settings, {})
    wire = json.loads(json.dumps(settings))
    wire["formatVersion"] = 2
    for plugin in wire["plugins"]:
        plugin.pop("settings")
        plugin.pop("state")
    with pytest.raises(StoreError, match="storage plugin as enabled"):
        container.decode_container(archive([("manifest.json", wire)]))


@pytest.mark.parametrize("payload", [b"[]", b'{"x":1,"x":2}', b'{"CLIENT_TOKEN":"fictional"}'])
def test_settings_and_state_require_safe_unambiguous_json_objects(payload):
    with pytest.raises(StoreError):
        container.decode_container(archive([
            ("manifest.json", wire_manifest()), ("plugins/sqlite/settings.json", payload),
        ]))


def test_manifest_is_required_and_unknown_versions_fail():
    with pytest.raises(StoreError):
        container.decode_container(archive([("plugins/sqlite/x", b"data")]))
    for version in (True, 3, "2"):
        value = wire_manifest()
        value["formatVersion"] = version
        with pytest.raises(StoreError):
            container.decode_container(archive([("manifest.json", value)]))


def test_expanded_size_is_checked_before_decompression(monkeypatch):
    raw = archive([("manifest.json", wire_manifest()), ("plugins/sqlite/x", b"x" * 100000)])
    monkeypatch.setattr(container, "MAX_BUNDLE_BYTES", 10000)
    assert len(raw) < 10000
    with pytest.raises(StoreError):
        container.decode_container(raw)
    with pytest.raises(StoreError):
        container.encode_container(manifest(), {"plugins/sqlite/x": b"x" * 100000})


def test_archive_count_and_manifest_sizes_are_bounded(monkeypatch):
    monkeypatch.setattr(container, "MAX_CONTAINER_FILES", 2)
    with pytest.raises(StoreError):
        container.encode_container(manifest(), {})
    with pytest.raises(StoreError):
        container.decode_container(archive([
            ("manifest.json", wire_manifest()), ("plugins/sqlite/a", b""), ("plugins/sqlite/b", b""),
        ]))
    monkeypatch.setattr(container, "MAX_CONTAINER_FILES", 1000)
    monkeypatch.setattr(container, "MAX_MANIFEST_BYTES", 10)
    with pytest.raises(StoreError):
        container.encode_container(manifest(), {})
    with pytest.raises(StoreError):
        container.decode_container(archive([("manifest.json", wire_manifest())]))


@pytest.mark.parametrize("raw", [None, "text", b"", b"not a zip"])
def test_malformed_archives_return_authored_errors(raw):
    with pytest.raises(StoreError):
        container.decode_container(raw)
