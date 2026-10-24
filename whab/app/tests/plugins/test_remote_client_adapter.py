"""Remote workers advertise immutable metadata; all authority remains host-local."""

from dataclasses import replace
from types import MappingProxyType

import pytest

from whab.plugins import Plugin, PluginContract, RemoteLedgerClient
from whab.plugins.remote_client_adapter import (
    client_record,
    configuration,
    connection_settings,
    read_client_record,
    supports_remote_client,
)
from whab.plugins.wire import Frame, WireError
from whab.plugins.worker_adapters import (
    adapter_info,
    contract_info,
    dispatch,
    read_contract,
    validate_adapter_info,
)


def client(identifier="client", **kwargs):
    prefix = identifier.upper().replace("-", "_")
    adapter = RemoteLedgerClient(url_setting=prefix + "_URL", token_setting=prefix + "_TOKEN", budget_setting=prefix + "_BUDGET_ID")
    return Plugin(identifier, "Fictional remote client", storage=adapter,
                  contract=PluginContract(authority="remote", lifecycle="live"), **kwargs)


@pytest.mark.parametrize("changes", [
    {"protocol_version": True}, {"protocol_version": 2}, {"url_setting": "https://private.invalid"},
    {"token_setting": "CLIENT_PRIVATE\nTOKEN"}, {"budget_setting": "a" * 101},
    {"url_setting": []}, {"token_setting": None}, {"budget_setting": "CLIENT_TOKEN"},
])
def test_descriptor_rejects_unversioned_nonplain_duplicate_or_configuration_values(changes):
    with pytest.raises(ValueError):
        RemoteLedgerClient(**changes)


def test_storage_union_preserves_factory_abi_and_requires_explicit_scoped_remote_declaration():
    def factory(_):
        return None
    assert Plugin("legacy", "Legacy", storage=factory).storage is factory
    for values in ({}, {"contract": PluginContract(authority="local")},
                   {"contract": PluginContract(authority="remote"), "budget_connection": lambda _: {}},
                   {"contract": PluginContract(authority="remote"), "storage_payload_paths": ("connection.json",)}):
        with pytest.raises(ValueError):
            Plugin("client", "Client", storage=RemoteLedgerClient(), **values)
    with pytest.raises(ValueError, match="declaring plugin"):
        Plugin("other", "Other", storage=RemoteLedgerClient(), contract=PluginContract(authority="remote"))
    assert not supports_remote_client(Plugin("legacy", "Legacy", storage=factory))
    assert not supports_remote_client(replace(client(), contract=PluginContract(authority="remote")))


def test_strict_codec_contains_only_configuration_key_names_and_rejects_financial_or_private_frames():
    plugin = client()
    record = client_record(plugin.storage)
    assert read_client_record(record) == plugin.storage
    info = adapter_info(plugin)
    assert validate_adapter_info(info) == info
    data, payload = dispatch(None, plugin, "client.describe", {}, b"")
    assert data == record and payload == b""
    encoded = Frame({"record": data}).encode()
    assert b"https://" not in encoded and b"Bearer" not in encoded
    for value in ({**record, "token": "fictional-private"}, {"protocol_version": 1}, MappingProxyType(record)):
        with pytest.raises(ValueError):
            read_client_record(value)
    for arguments, binary in (({"config": {}}, b""), ({}, b"private-budget-payload")):
        with pytest.raises(WireError):
            dispatch(None, plugin, "client.describe", arguments, binary)
    for change in ({"merchant_logos": True}, {"bank": {"ui": False}}):
        with pytest.raises(WireError):
            validate_adapter_info({**info, **change})
    with pytest.raises(WireError):
        validate_adapter_info({**info, "private": {}})
    capabilities = [{"name": "ledger.read", "version": 1}, {"name": "ledger.mutate", "version": 1}]
    contract = read_contract(contract_info(plugin), capabilities)
    assert {row.composition for row in contract.provides} == {"singleton"}


def test_configuration_is_resolved_only_in_the_host_and_connection_file_cannot_redirect_credentials():
    import json

    plugin = client()
    private = {"CLIENT_URL": "https://FICTIONAL.example:443/", "CLIENT_TOKEN": "fictional-" + "a" * 32,
               "CLIENT_BUDGET_ID": "fictional-budget"}
    url, token, budget = configuration(plugin.storage, private)
    assert url == "https://fictional.example" and token == private["CLIENT_TOKEN"] and budget == "fictional-budget"
    assert connection_settings(plugin.storage, {"connection.json": json.dumps({
        "formatVersion": 1, "url": url, "budgetId": budget}).encode()}) == {
        "CLIENT_URL": url, "CLIENT_BUDGET_ID": budget}
    for key in ("CLIENT_URL", "CLIENT_TOKEN", "CLIENT_BUDGET_ID"):
        with pytest.raises(ValueError):
            configuration(plugin.storage, {name: value for name, value in private.items() if name != key})
    for value in ("https://fictional.example/another-authority", "https://fictional.example/%2F", "http://fictional.example"):
        with pytest.raises(ValueError):
            configuration(plugin.storage, {**private, "CLIENT_URL": value})
