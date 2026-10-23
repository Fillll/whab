from whab_plugin_devkit import NativeRuntime, assert_api_interface
from whab_plugin_example_api import plugin


def test_host_owns_authenticated_api_commands_and_the_fictional_runtime_is_cleaned():
    assert plugin.interface.factory is None
    assert plugin.contract.lifecycle == "live"
    assert plugin.interface.adapter.token_setting == "EXAMPLE_API_TOKEN"
    with NativeRuntime("sqlite", (plugin.id,), api_token_setting=plugin.interface.adapter.token_setting) as runtime:
        result = assert_api_interface(runtime, plugin)
        assert result["query"] == "passed" and result["command"] == "passed"
        assert result["retry"] == "passed" and result["guards"] == "passed"
    assert runtime.process is None and not runtime.home.exists()
