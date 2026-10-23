"""Optional worker adapters exchange bounded data and preserve public validation."""

import copy
import io
from dataclasses import replace
from types import SimpleNamespace

import pytest

from whab.plugins import (
    BankLogos,
    FileImport,
    Insight,
    LogoImage,
    Plugin,
    PluginAction,
    PluginContract,
    PluginRegistry,
    PluginResources,
    PluginSettings,
    WorkspaceImport,
    report_worker,
)
from whab.plugins.wire import HEADER, Frame, WireError, frame_lengths
from whab.plugins.worker_adapters import dispatch, import_bytes, read_import, supports_live_plugin
from whab.plugins.worker_resources import capture_resources, read_resource_bundle, resource_bundle


def imported(label="Fictional import"):
    return WorkspaceImport(({
        "id": "wallet", "name": label, "institution": "Fictional", "type": "other",
        "balance": 0, "source": "fictional", "stale": True, "balanceUnknown": True,
        "onBudget": False, "subtype": None,
    },), (), (), (), "2026-10")


def resources(label="version one"):
    return PluginResources({"templates/accounts.html": f"<p>{label}</p>".encode(),
                            "static/import.js": f"/* {label} */".encode()})


def test_resource_dto_detaches_files_and_supports_shared_ui_bundles_without_parser_requirements():
    files = {"templates/base.html": b"<main>Fictional</main>", "static/app.js": b"/* fictional */"}
    value = PluginResources(files)
    files.clear()
    assert len(value.files) == 2 and "Fictional" not in repr(value)
    with pytest.raises(TypeError):
        value.files["static/app.js"] = b"changed"
    with pytest.raises(ValueError, match="accounts.html"):
        FileImport(lambda _: imported(), resources=value)
    with pytest.raises(ValueError):
        FileImport(lambda _: imported(), templates=__import__("pathlib").Path("."), resources=resources())


@pytest.mark.parametrize("files", [
    {}, {"../private": b"x"}, {"static/../private": b"x"}, {"/static/file": b"x"},
    {"static/\\file": b"x"}, {"static/drive:file": b"x"}, {"static/\x00file": b"x"},
    {"static/file": bytearray(b"x")}, {"templates/base.html": b"\xff"},
    {"static/large": b"x" * (2 * 1024 * 1024 + 1)},
    {f"static/file-{index}": b"" for index in range(257)},
    {f"static/file-{index}": b"x" * (2 * 1024 * 1024) for index in range(3)},
])
def test_resource_paths_types_unicode_and_all_allocation_bounds_fail_closed(files):
    with pytest.raises((ValueError, UnicodeError)):
        PluginResources(files)


def test_resource_capture_rejects_links_and_keeps_old_generation_bytes_after_source_edits(tmp_path):
    template, static = tmp_path / "templates", tmp_path / "static"
    template.mkdir()
    static.mkdir()
    (template / "accounts.html").write_text("version one")
    (static / "import.js").write_text("/* version one */")
    parser = FileImport(lambda _: imported(), template, static)
    captured = capture_resources(parser)
    (static / "import.js").write_text("/* version two */")
    assert captured.files["static/import.js"] == b"/* version one */"
    assert capture_resources(parser).files["static/import.js"] == b"/* version two */"
    (static / "private").symlink_to(tmp_path / "unread-private-target")
    with pytest.raises(WireError):
        capture_resources(parser)


def test_resource_bundle_hashes_exact_lengths_and_paths_without_child_filesystem_authority():
    original = resources()
    metadata, payload = resource_bundle(original)
    assert read_resource_bundle(metadata, payload).files == original.files
    assert read_resource_bundle(*resource_bundle(None)) is None
    for mutation in ("hash", "size", "duplicate", "path", "trailing"):
        value = copy.deepcopy(metadata)
        binary = payload
        if mutation == "hash":
            value["files"][0]["sha256"] = "0" * 64
        elif mutation == "size":
            value["files"][0]["size"] = True
        elif mutation == "duplicate":
            value["files"].append(value["files"][0])
        elif mutation == "path":
            value["files"][0]["name"] = "static/../../private"
        else:
            binary += b"trailing"
        with pytest.raises((ValueError, UnicodeError)):
            read_resource_bundle(value, binary)


def test_live_predicate_accepts_explicit_branding_and_pure_parsers_without_invoking_hooks():
    def forbidden(*_):
        pytest.fail("Lifecycle metadata must not invoke hooks")
    branding = Plugin("artwork", "Fictional artwork", bank_logos=BankLogos(forbidden, forbidden),
                      settings=PluginSettings(forbidden), contract=PluginContract(lifecycle="live"))
    parser = Plugin("parser", "Fictional parser", file_import=FileImport(forbidden), contract=PluginContract(lifecycle="live"))
    assert supports_live_plugin(branding) and supports_live_plugin(parser)
    assert not supports_live_plugin(replace(parser, settings=PluginSettings(forbidden)))
    assert not supports_live_plugin(replace(parser, contract=None))
    assert not supports_live_plugin(replace(parser, bind=forbidden))
    assert not supports_live_plugin(replace(parser, contract=PluginContract(lifecycle="live", state_version=2)))


def test_binary_import_validation_rejects_duplicate_fields_floats_raw_objects_and_bad_references():
    data = imported()
    assert read_import(import_bytes(data)) == data.validated_copy()
    with pytest.raises(WireError):
        import_bytes(object())
    with pytest.raises(WireError):
        read_import(b'{"accounts":[],"accounts":[]}')
    invalid = replace(data, accounts=({**data.accounts[0], "balance": 1.25},))
    with pytest.raises(ValueError):
        import_bytes(invalid)


def test_all_new_worker_operations_use_typed_arguments_and_only_public_data(tmp_path):
    observed = []
    image = LogoImage(b"\x89PNG\r\n\x1a\nfictional", "image/png")
    branding = Plugin("artwork", "Fictional artwork", bank_logos=BankLogos(
        lambda text: "fictional" if text == "Fictional" else None, lambda _: image,
        configure_cache=lambda path: observed.append(path), resolve_domain=lambda _: "fictional",
    ), settings=PluginSettings(lambda: (Insight("Cache", "0"),), (PluginAction("flush", "Flush"),), lambda _: "Cleared"),
                      contract=PluginContract(lifecycle="live"))
    registry = PluginRegistry((branding,))
    assert dispatch(registry, branding, "bank.resolve", {"text": "Fictional"}, b"") == ("fictional", b"")
    assert dispatch(registry, branding, "bank.domain", {"text": "bank.example.org"}, b"") == ("fictional", b"")
    assert dispatch(registry, branding, "bank.fetch", {"key": "fictional"}, b"") == ({"media_type": "image/png"}, image.data)
    assert dispatch(registry, branding, "cache.configure", {"directory": str(tmp_path)}, b"") == (None, b"")
    assert observed == [tmp_path]
    assert dispatch(registry, branding, "settings.act", {"action": "flush"}, b"") == ("Cleared", b"")
    assert dispatch(registry, branding, "settings.read", {}, b"")[0]["insights"][0]["value"] == "0"
    parser = Plugin("parser", "Parser", file_import=FileImport(lambda _: imported(), resources=resources()), contract=PluginContract(lifecycle="live"))
    registry = PluginRegistry((parser,))
    value, payload = dispatch(registry, parser, "file.parse", {}, b"fictional archive")
    assert value == {"format": "workspace-import-v1"} and read_import(payload) == imported().validated_copy()
    assert read_resource_bundle(*dispatch(registry, parser, "file.resources", {}, b"")).files == resources().files
    with pytest.raises(WireError):
        dispatch(registry, parser, "file.parse", {"backend": "forbidden"}, b"fictional archive")


def test_child_loop_sends_resources_and_import_data_as_binary_without_raw_objects(monkeypatch):
    plugin = Plugin("parser", "Parser", file_import=FileImport(lambda _: imported(), resources=resources()), contract=PluginContract(lifecycle="live"))
    monkeypatch.setattr(report_worker, "load_plugins", lambda _: PluginRegistry((plugin,)))
    monkeypatch.setattr(report_worker.metadata, "entry_points", lambda **_: (
        SimpleNamespace(name="parser", dist=SimpleNamespace(metadata={"Name": "fictional-parser"}, version="1.0.0")),
    ))
    requests = b"".join(Frame({"id": operation, "operation": operation, "arguments": {}},
                              b"fictional archive" if operation == "file.parse" else b"").encode()
                        for operation in ("file.parse", "file.resources"))
    target = io.BytesIO()
    report_worker.serve("parser", io.BytesIO(requests), target)
    raw, frames = target.getvalue(), []
    while raw:
        metadata, payload = frame_lengths(raw[:HEADER.size])
        length = HEADER.size + metadata + payload
        frames.append(Frame.decode(raw[:length]))
        raw = raw[length:]
    assert frames[0].metadata["live"] is True
    assert frames[0].metadata["adapters"]["file_import"] == {"ui": True}
    assert read_import(frames[1].payload) == imported().validated_copy()
    assert read_resource_bundle(frames[2].metadata["data"], frames[2].payload).files == resources().files


def test_child_rejects_oversized_upload_headers_before_reading_or_allocating_the_payload():
    metadata = Frame({"id": "request", "operation": "file.parse", "arguments": {}}).encode()[HEADER.size:]
    magic, version, _, _ = HEADER.unpack(Frame({}).encode()[:HEADER.size])
    source = io.BytesIO(HEADER.pack(magic, version, len(metadata), 10 * 1024 * 1024 + 1) + metadata)
    with pytest.raises(WireError):
        report_worker._read_request(source)
    assert source.tell() == HEADER.size + len(metadata)


def test_branding_factory_is_zero_authority_and_cannot_change_identity_contract_or_add_privileged_hooks():
    template = Plugin("artwork", "Artwork", bank_logos=BankLogos(lambda _: None, lambda _: None), contract=PluginContract(lifecycle="live"))
    for changed in (replace(template, id="other"), replace(template, storage=lambda _: None),
                    replace(template, branding_factory=lambda: template), replace(template, contract=None)):
        registry = PluginRegistry((replace(template, branding_factory=lambda changed=changed: changed),))
        with pytest.raises(ValueError) as raised:
            registry.bind_branding()
        assert raised.value.code == "branding_startup_failed" and raised.value.plugin_id == "artwork"
