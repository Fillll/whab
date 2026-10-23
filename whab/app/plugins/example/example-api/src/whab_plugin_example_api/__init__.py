"""Declare an API interface; the host owns HTTPS, credentials and ledger commands."""

from whab.plugins import Interface, Plugin, PluginContract, RemoteLedgerInterface

plugin = Plugin(
    "example-api", "Fictional API interface",
    interface=Interface(adapter=RemoteLedgerInterface(token_setting="EXAMPLE_API_TOKEN")),
    contract=PluginContract(lifecycle="live"),
)
