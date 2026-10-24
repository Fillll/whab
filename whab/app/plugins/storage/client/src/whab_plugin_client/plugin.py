"""Declarative HTTPS client; credentials and transport remain in the host."""

from whab.plugins import Plugin, PluginContract, RemoteLedgerClient
from whab.plugins.remote_client_adapter import create_client
from whab.plugins.remote_transport import RemoteStore as RemoteStore
from whab.plugins.remote_transport import connection_configuration as connection_configuration


def create_storage(context):
    """Legacy callable factory compatibility; the live descriptor is data-only."""
    return create_client(RemoteLedgerClient(), context)


plugin = Plugin(
    id="client", display_name="Remote budget client", storage=RemoteLedgerClient(),
    contract=PluginContract(authority="remote", lifecycle="live"),
)
