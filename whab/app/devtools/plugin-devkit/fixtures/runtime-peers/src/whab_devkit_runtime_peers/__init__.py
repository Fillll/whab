"""Only explicit fixture installation registers these fictional live peers."""

from dataclasses import replace

from whab_plugin_devkit.configurations import _PEERS, _peer

from whab.plugins import PluginContract


def live(identifier):
    plugin = _peer(identifier)
    return replace(plugin, contract=PluginContract(requires=plugin.contract.requires, lifecycle="live"))


report_a, report_b, brand_a, brand_b, import_a, import_b = map(live, _PEERS)
