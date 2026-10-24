from whab_plugin_example_branding import plugin

from whab.plugins import PluginRegistry


def test_known_fictional_logo_and_unknown_fallback_do_not_use_a_network():
    registry = PluginRegistry((plugin,))
    assert registry.bank_logo("Fictional Bank") == (plugin.id, "fictional-bank")
    assert registry.bank_logo("Unknown institution") is None
    assert registry.logo_image(plugin.id, "fictional-bank").media_type == "image/png"
    assert registry.logo_image(plugin.id, "unknown") is None
