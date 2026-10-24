"""Data-only declaration for the host-owned HTTPS remote-ledger client.

A plugin worker advertises configuration key names, never configuration values.
The host owns TLS, bearer authorization, authority binding and command outcomes.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemoteLedgerClient:
    """Use the existing HTTPS ``/remote/v1`` protocol from the host interpreter.

    The named settings belong to this plugin's device-local configuration. A
    code replacement may select different keys only if the resolved normalized
    HTTPS endpoint, remote budget and credential are exactly the retained binding.
    New protocols require a compatible host SDK; workers never perform requests.
    """

    protocol_version: int = 1
    url_setting: str = "CLIENT_URL"
    token_setting: str = "CLIENT_TOKEN"
    budget_setting: str = "CLIENT_BUDGET_ID"

    def __post_init__(self):
        if type(self.protocol_version) is not int or self.protocol_version != 1:
            raise ValueError("Unsupported remote ledger client protocol.")
        names = (self.url_setting, self.token_setting, self.budget_setting)
        if any(type(name) is not str for name in names) or len(set(names)) != 3:
            raise ValueError("Remote client settings must use distinct host configuration keys.")
        for name in names:
            if (type(name) is not str or len(name) > 100
                    or re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", name) is None):
                raise ValueError("Remote client settings must name bounded host configuration keys.")


def client_record(adapter):
    if type(adapter) is not RemoteLedgerClient:
        raise ValueError("Use a supported data-only remote client adapter.")
    # Revalidate a potentially altered frozen record before crossing a boundary.
    RemoteLedgerClient(**{name: getattr(adapter, name) for name in RemoteLedgerClient.__slots__})
    return {name: getattr(adapter, name) for name in RemoteLedgerClient.__slots__}


def read_client_record(value):
    if type(value) is not dict or value.keys() != set(RemoteLedgerClient.__slots__):
        raise ValueError("Invalid remote client adapter metadata.")
    return RemoteLedgerClient(**value)


def supports_remote_client(plugin):
    """Only a pure, explicitly live remote-client declaration can be replaced."""
    from .contracts import contract_for

    if type(plugin.storage) is not RemoteLedgerClient:
        return False
    contract = contract_for(plugin)
    return (
        plugin.contract is not None and contract.lifecycle == "live"
        and contract.authority == "remote" and contract.state_version == 1
        and not contract.state_upgrades
        and {(row.name, row.version, row.composition) for row in contract.provides} == {
            ("ledger.read", 1, "singleton"), ("ledger.mutate", 1, "singleton")}
        and all(getattr(plugin, name) is None for name in (
            "bind", "migrate_state", "budget_connection", "categorization", "settings",
            "bank", "bank_ui", "ui", "interface", "bank_logos", "merchant_logos",
            "branding_factory", "file_import", "insights", "export",
        ))
        and not plugin.storage_payload_paths
    )


def configuration(adapter, config):
    """Resolve one private host profile without constructing a transport."""
    from whab.domain.errors import StoreError
    from whab.plugins.remote_transport import _connection

    client_record(adapter)
    url, budget = _connection(config.get(adapter.url_setting, ""), config.get(adapter.budget_setting, ""))
    token = config.get(adapter.token_setting, "")
    if type(token) is not str or not 32 <= len(token) <= 4096 or any(not 33 <= ord(char) <= 126 for char in token):
        raise StoreError("Remote client requires a private bearer token with 32 to 4096 printable ASCII characters.")
    return url, token, budget


def connection_settings(adapter, files):
    """Imported public connection metadata may only select its exact local profile."""
    from whab.plugins.remote_transport import connection_configuration

    values = connection_configuration(files)
    return {adapter.url_setting: values["CLIENT_URL"], adapter.budget_setting: values["CLIENT_BUDGET_ID"]}


def create_client(adapter, context):
    """Construct the retained host transport; no context is sent to a worker."""
    from whab.plugins.remote_transport import RemoteStore

    return RemoteStore(*configuration(adapter, context.config))
