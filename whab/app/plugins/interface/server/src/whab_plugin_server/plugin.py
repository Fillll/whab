"""Declare the public API; authorization and financial commands stay in WHAB."""

from whab.plugins import Interface, Plugin, PluginContract, RemoteLedgerInterface

plugin = Plugin(
    id="server", display_name="Remote budget server",
    interface=Interface(adapter=RemoteLedgerInterface()),
    contract=PluginContract(lifecycle="live"),
)
