"""Data-only host adapters for supported API interface protocols.

Workers can declare the protocol and its enabled operations. Authorization,
request validation and financial execution stay in the host interpreter.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemoteLedgerInterface:
    """The existing HTTPS ``/remote/v1`` ledger protocol, served by the host.

    ``token_setting`` names a device-private host configuration key. Its value
    never enters a worker descriptor or protocol message. The host enforces a
    bearer token of at least 32 characters and the ordinary financial contracts.
    New transport protocols require a compatible host adapter/SDK upgrade.
    """

    protocol_version: int = 1
    token_setting: str = "SERVER_TOKEN"
    queries: bool = True
    commands: bool = True

    def __post_init__(self):
        if type(self.protocol_version) is not int or self.protocol_version != 1:
            raise ValueError("Unsupported remote ledger interface protocol.")
        if (type(self.token_setting) is not str or len(self.token_setting) > 100
                or re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", self.token_setting) is None):
            raise ValueError("Remote interface token settings must name a bounded host configuration key.")
        if type(self.queries) is not bool or type(self.commands) is not bool or not (self.queries or self.commands):
            raise ValueError("Remote interfaces must explicitly enable queries, commands or both.")


def interface_record(adapter):
    if type(adapter) is not RemoteLedgerInterface:
        raise ValueError("Use a supported data-only interface adapter.")
    return {name: getattr(adapter, name) for name in RemoteLedgerInterface.__slots__}


def read_interface_record(value):
    if type(value) is not dict or value.keys() != set(RemoteLedgerInterface.__slots__):
        raise ValueError("Invalid remote interface adapter metadata.")
    return RemoteLedgerInterface(**value)


def validate_interface_owners(plugins):
    """Reject ambiguous v1 ownership before binding any plugin configuration."""
    from .runtime import PluginError

    interfaces = sorted((plugin for plugin in plugins if plugin.interface is not None), key=lambda row: row.id)
    declarative = [plugin for plugin in interfaces if plugin.interface.adapter is not None]
    if declarative and len(interfaces) != 1:
        raise PluginError("Select exactly one declarative API interface and remove other API interfaces before retrying.",
                          code="interface_conflict", plugin_id=declarative[0].id)
