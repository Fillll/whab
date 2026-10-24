"""Declare a remote authority; HTTPS credentials and transport stay in the host."""

from whab.plugins import Plugin, PluginContract, RemoteLedgerClient

plugin = Plugin(
    "example-remote-client", "Fictional remote ledger client",
    storage=RemoteLedgerClient(url_setting="EXAMPLE_REMOTE_CLIENT_URL", token_setting="EXAMPLE_REMOTE_CLIENT_TOKEN",
                               budget_setting="EXAMPLE_REMOTE_CLIENT_BUDGET_ID"),
    contract=PluginContract(authority="remote", lifecycle="live"),
)
