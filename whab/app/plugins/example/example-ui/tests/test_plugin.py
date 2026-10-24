from whab_plugin_devkit import (
    NativeRuntime,
    assert_visual_interface,
    visual_change,
    visual_declaration,
)
from whab_plugin_example_ui import build_context, plugin


def test_public_context_and_participating_descriptor():
    data = {"accounts": [], "transactions": [], "currency": "USD"}
    assert build_context(data, {}, [], page="budget") == {"data": data}
    assert visual_declaration(plugin)["view_protocol"] == 1


def test_installed_native_https_pages_assets_handover_and_reopen():
    with NativeRuntime("sqlite", (), ui=plugin.id) as runtime:
        evidence = assert_visual_interface(runtime, plugin, asset_name="app.js")
        old = runtime.get_resource(f'/budgets/home/ui/{evidence["generation"]}/static/app.js')[2]
        visual_change(runtime, None, dirty=True)
        assert runtime.get_resource("/budgets/home/budget/current-month/usd")[0] == 404
        visual_change(runtime, plugin.id)
        runtime.stop()
        runtime.start()
        assert assert_visual_interface(runtime, plugin, asset_name="app.js")["generation"] == evidence["generation"]
        assert runtime.get_resource(f'/budgets/home/ui/{evidence["generation"]}/static/app.js')[2] == old
