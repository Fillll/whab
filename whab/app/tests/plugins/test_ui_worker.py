from copy import deepcopy
from dataclasses import replace

import pytest

from whab.plugins import (
    Capability,
    Plugin,
    PluginContract,
    PluginResources,
    UserInterface,
    load_plugins,
)
from whab.plugins.ui_worker import (
    adapter_info,
    dispatch,
    html_result,
    read_html,
    readiness_arguments,
    snapshot_ui,
    supports_live_visual,
    validate_adapter_info,
)
from whab.plugins.wire import WireError
from whab.plugins.worker_resources import read_resource_bundle


def visual():
    return UserInterface(None, None, lambda data, status, items, **kw: {
        "amount": lambda value: f"fictional:{value}", "data": data,
    }, resources=PluginResources({"templates/base.html": b"<p>{{ amount(data.summary.readyToAssign) }}</p>",
                                  "static/app.js": b"export const fictional = true;"}), view_protocol=1)


def test_explicit_visual_only_contract_and_projection_information():
    plugin = Plugin("fictional-ui", "Fictional UI", ui=visual(), contract=PluginContract(
        provides=(Capability("interface.ui", 1, composition="singleton"),), lifecycle="live"))
    assert supports_live_visual(plugin)
    assert not supports_live_visual(replace(plugin, contract=None))
    assert not supports_live_visual(replace(plugin, insights=lambda snapshot: ()))
    assert not supports_live_visual(replace(plugin, ui=replace(plugin.ui, view_protocol=0)))
    assert not supports_live_visual(replace(plugin, contract=replace(plugin.contract, lifecycle="restart")))
    assert not supports_live_visual(replace(plugin, contract=replace(plugin.contract, state_version=2)))
    flags = adapter_info(plugin.ui)
    assert validate_adapter_info(flags) == flags
    assert adapter_info(None) is None
    for invalid in ({}, {**flags, "extra": True}, {**flags, "budget_months": True},
                    {**flags, "transaction_page_size": 1001}, {**flags, "lightweight_accounts": 1}):
        with pytest.raises((ValueError, WireError)):
            validate_adapter_info(invalid)


def test_resource_snapshot_and_render_share_the_same_bytes_after_paths_change(tmp_path):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_bytes(b"<p>original:{{ data.summary.readyToAssign }}</p>")
    (static / "app.js").write_bytes(b"const version = 1;")
    ui = snapshot_ui(UserInterface(templates, static, lambda data, *args, **kw: {"data": data}))
    (templates / "base.html").write_bytes(b"changed")
    (static / "app.js").unlink()
    metadata, payload = dispatch(ui, "ui.resources", {}, b"")
    resources = read_resource_bundle(metadata, payload)
    assert resources.files["static/app.js"] == b"const version = 1;"
    metadata, payload = dispatch(ui, "ui.render", readiness_arguments("budget"), b"")
    assert read_html(metadata, payload) == b"<p>original:0</p>"
    assert snapshot_ui(ui).resources.files == ui.resources.files


def test_bundled_visual_readiness_uses_only_core_projections_and_all_four_pages():
    ui = snapshot_ui(load_plugins(("web",)).descriptors()[0].ui)
    for page in ("budget", "transactions", "accounts", "insights"):
        metadata, payload = dispatch(ui, "ui.render", readiness_arguments(page), b"")
        assert b"<html" in read_html(metadata, payload)
        assert b"Fictional readiness budget" in payload


@pytest.mark.parametrize("operation,arguments,payload", [
    ("ui.resources", {"path": "private"}, b""), ("ui.resources", {}, b"private"),
    ("unknown", {}, b""), ("ui.render", {}, b""), ("ui.render", [], b""),
])
def test_visual_dispatch_refuses_unsupported_or_malformed_requests(operation, arguments, payload):
    with pytest.raises((ValueError, WireError)):
        dispatch(visual(), operation, arguments, payload)


def test_result_metadata_payload_bounds_and_encoding():
    metadata, payload = html_result(b"<p>fictional</p>")
    assert read_html(metadata, payload) == payload
    for change in ({**metadata, "bytes": True}, {**metadata, "bytes": len(payload) + 1},
                   {**metadata, "sha256": "0" * 64}, {**metadata, "extra": None}):
        with pytest.raises(WireError):
            read_html(change, payload)
    for invalid in (b"", b"\xff", b"\x00", b"x" * (16 * 1024 * 1024 + 1), "text"):
        with pytest.raises((WireError, UnicodeError)):
            html_result(invalid)
    value = deepcopy(readiness_arguments("budget"))
    value["context"]["private_handle"] = object()
    with pytest.raises(WireError):
        dispatch(visual(), "ui.render", value, b"")
