"""Public visual probes must reject broken advertised behavior."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import assert_visual_interface, visual_change, visual_declaration
from whab_plugin_devkit import runtime_harness as harness
from whab_plugin_devkit import visual_interface as visual

from whab import plugins as sdk


def descriptor():
    if "view_protocol" not in sdk.UserInterface.__dataclass_fields__:
        return None
    return sdk.Plugin("fictional-ui", "Fictional UI", contract=sdk.PluginContract(lifecycle="live"),
                      ui=sdk.UserInterface(None, None, lambda *args, **kw: {}, view_protocol=1,
                                          resources=sdk.PluginResources({"templates/base.html": b"<p>Fictional</p>", "static/app.js": b"fictional"})))


def test_visual_declaration_never_executes_context_and_preserves_old_host_applicability():
    plugin = descriptor()
    if plugin is None:
        with pytest.raises(ValueError, match="2.36"):
            visual_declaration(SimpleNamespace(ui=None))
        return
    assert visual_declaration(plugin)["view_protocol"] == 1
    for broken in (SimpleNamespace(ui=None), replace(plugin, contract=None), replace(plugin, insights=lambda _: ()),
                   replace(plugin, contract=sdk.PluginContract()), replace(plugin, ui=replace(plugin.ui, view_protocol=0)),
                   replace(plugin, contract=sdk.PluginContract(state_version=2))):
        with pytest.raises(ValueError):
            visual_declaration(broken)
    with pytest.raises(ValueError):
        visual_declaration(replace(plugin, ui=replace(plugin.ui, render=lambda *args: b"private")))


class FakeVisual:
    def __init__(self, fault=None):
        self.fault = fault
        self.active = {"plugin_id": "fictional-ui", "resource_generation": "a" * 64, "worker": None}
        self.target = None
        self.registered = False
        self.deleted = False
        self.commits = 0
        self.transition_timeout = 180

    def expect(self, method, path, body=None, **options):
        if options.get("status", 200) == 403:
            return {}
        if path == "/api/ui/views" and method == "POST":
            self.registered = True
            return {"view": "view"}
        if path == "/api/ui/views/view":
            if method == "DELETE":
                self.deleted = True
            return {}
        if path == "/api/ui/preview":
            self.target = body["pluginId"]
            return {"preview_id": "preview"}
        if path == "/api/ui/commit":
            self.commits += 1
            return {"id": "different" if self.fault == "duplicate" and self.commits == 2 else "operation"}
        assert path == "/api/ui/status"
        admission = "gated" if self.fault == "gate" else "open"
        active = {**self.active, "plugin_id": "alien"} if self.fault == "defer" and self.registered else self.active
        return {"active": active, "browser_preparation": {"id": "proposal"} if self.commits else None,
                "runtime": {"admission": admission}, "operations": [{"id": "operation", "state": "failed" if self.fault == "failed" else "running"}]}

    def wait(self, observe, *, timeout):
        assert timeout == self.transition_timeout
        return observe()

    def operation(self, path, identifier):
        assert path == "/api/ui/status" and identifier == "operation"
        self.active = {**self.active, "plugin_id": "other" if self.fault == "selection" else self.target}
        return {"preview_id": "other" if self.fault == "preview" else "preview"}

    def get_resource(self, path):
        asset = "/static/" in path
        headers = {"x-whab-ui-generation": "b" * 64 if self.fault == "generation" else self.active["resource_generation"],
                   "cache-control": "no-store" if self.fault == "mutable" else "public, immutable"}
        return (404 if self.fault == "missing" or asset and self.fault == "asset" else 200), headers, b"" if self.fault == "empty" else b"fictional"


@pytest.mark.parametrize("dirty", [False, True])
def test_visual_handover_correlates_exact_preview_and_cleans_opaque_view(dirty):
    runtime = FakeVisual()
    assert visual_change(runtime, None, dirty=dirty)["plugin_id"] is None
    assert runtime.commits == 2 and runtime.deleted == dirty


@pytest.mark.parametrize("fault", ["duplicate", "gate", "defer", "preview", "selection", "failed"])
def test_broken_handover_is_never_reported_as_success_and_view_is_released(fault):
    runtime = FakeVisual(fault)
    with pytest.raises(AssertionError):
        visual_change(runtime, None, dirty=True)
    assert runtime.deleted


def test_visual_probe_checks_all_pages_assets_guards_and_explicit_asset_applicability():
    plugin = descriptor()
    if plugin is None:
        assert visual.MINIMUM_UI_HOST == "2.36.0"
        return
    assert assert_visual_interface(FakeVisual(), plugin, asset_name="app.js")["asset"] == "app.js"
    assert assert_visual_interface(FakeVisual(), plugin)["asset"].startswith("not-applicable")
    for invalid in ("../private", "", "//other", "a?token=private", True):
        with pytest.raises(ValueError):
            assert_visual_interface(FakeVisual(), plugin, asset_name=invalid)
    for fault in ("generation", "missing", "empty", "mutable", "asset"):
        with pytest.raises(AssertionError):
            assert_visual_interface(FakeVisual(fault), plugin, asset_name="app.js")
    runtime = FakeVisual()
    runtime.active["plugin_id"] = "other"
    with pytest.raises(AssertionError):
        assert_visual_interface(runtime, plugin)
    runtime.active.update(plugin_id=plugin.id, resource_generation="invalid")
    with pytest.raises(AssertionError):
        assert_visual_interface(runtime, plugin)


@pytest.mark.parametrize("identifier", [True, "../escape", "", "A", "x" * 65, "optional-ui"])
def test_native_harness_rejects_ambiguous_visual_selections(identifier):
    with pytest.raises(ValueError):
        harness.NativeRuntime("sqlite", ("optional-ui",), ui=identifier)


def test_native_resource_reads_are_bounded_and_close_connections(monkeypatch):
    runtime = harness.NativeRuntime("sqlite", (), ui="fictional-ui")
    assert runtime.environment()["WHAB_UI"] == "fictional-ui" and runtime.environment()["WHAB_PLUGINS"] == ""
    closed = []
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, method, path):
            assert method == "GET" and path.startswith("/")
        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda _: b"fictional", getheaders=lambda: [("X-WHAB-UI-Generation", "a" * 64)])
        def close(self):
            closed.append(True)
    monkeypatch.setattr(harness.http.client, "HTTPSConnection", Connection)
    try:
        for invalid in (42, "https://other", "//other"):
            with pytest.raises(ValueError):
                runtime.get_resource(invalid)
        assert runtime.get_resource("/budget")[1]["x-whab-ui-generation"] == "a" * 64
        monkeypatch.setattr(Connection, "getresponse", lambda _: SimpleNamespace(status=200, read=lambda _: b"x" * (16 * 1024 * 1024 + 1)))
        with pytest.raises(ValueError, match="bound"):
            runtime.get_resource("/large")
        assert len(closed) == 2
    finally:
        runtime.__exit__(None, None, None)


def test_native_no_content_acknowledgement_does_not_require_a_json_payload(monkeypatch):
    runtime = harness.NativeRuntime("sqlite", ())
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return SimpleNamespace(status=204, read=lambda _: b"")
        def close(self):
            pass
    monkeypatch.setattr(harness.http.client, "HTTPSConnection", Connection)
    try:
        assert runtime.expect("DELETE", "/api/ui/views/fictional", status=204) is None
    finally:
        runtime.__exit__(None, None, None)


@pytest.mark.parametrize("deadline", [True, 0, -1, 301, "180"])
def test_native_handover_deadlines_are_explicit_and_bounded(deadline):
    with pytest.raises(ValueError, match="management deadline"):
        harness.NativeRuntime("sqlite", (), transition_timeout=deadline)
    runtime = harness.NativeRuntime("sqlite", ())
    try:
        with pytest.raises(ValueError, match="observable-state"):
            runtime.wait(lambda: True, timeout=deadline)
    finally:
        runtime.__exit__(None, None, None)
