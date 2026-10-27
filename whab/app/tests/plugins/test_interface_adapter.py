"""API workers declare bounded data; host adapters retain auth and ledger rules."""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from whab.plugins import Interface, InterfaceContext, LedgerService, RemoteLedgerInterface
from whab.plugins.interface_adapter import interface_record, read_interface_record
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router


def test_local_settings_are_distinct_from_the_remote_ledger_owner():
    from fastapi import APIRouter

    from whab.plugins import Plugin, PluginRegistry
    from whab.plugins.interface_adapter import validate_interface_owners
    from whab.web.interface_adapter import prepare_interfaces

    local = Plugin("fictional-settings", "Fictional settings", interface=Interface(lambda _: APIRouter(), local_settings=True))
    remote = Plugin("fictional-server", "Fictional server", interface=Interface(adapter=RemoteLedgerInterface()))
    validate_interface_owners((local, remote))
    generation = prepare_interfaces(PluginRegistry((local,)), lambda _: None)
    assert not generation.enabled() and not generation.legacy_routes()
    for values in ({"local_settings": "true", "factory": lambda _: None},
                   {"local_settings": True, "adapter": RemoteLedgerInterface()}):
        with pytest.raises(ValueError, match="Local settings"):
            Interface(**values)


@pytest.mark.parametrize("changes", [
    {"protocol_version": True}, {"protocol_version": 2}, {"token_setting": ""},
    {"token_setting": "SERVER_TOKEN\n"}, {"token_setting": "private.token"},
    {"token_setting": "TOKEN" * 100}, {"queries": 1}, {"commands": None},
    {"queries": False, "commands": False},
])
def test_adapter_metadata_rejects_unsupported_or_ambiguous_transport(changes):
    with pytest.raises(ValueError):
        RemoteLedgerInterface(**changes)


def test_adapter_roundtrip_has_only_protocol_data_and_retains_legacy_factory_contract():
    adapter = RemoteLedgerInterface(token_setting="CUSTOM_TOKEN", commands=False)
    assert read_interface_record(interface_record(adapter)) == adapter
    assert Interface(adapter=adapter).factory is None
    assert Interface(lambda _: None).adapter is None
    with pytest.raises(ValueError):
        Interface(lambda _: None, adapter)
    with pytest.raises(ValueError):
        Interface(adapter={})
    for record in ({}, {**interface_record(adapter), "private": "reject"}, None):
        with pytest.raises(ValueError):
            read_interface_record(record)
    with pytest.raises(ValueError):
        interface_record({})


def test_host_adapter_keeps_credentials_financial_validation_retry_and_workspace_rules(store):
    demo = store.accounts("demo")
    service = LedgerService(store, budget_id="fictional")
    context = InterfaceContext(service, {"SERVER_TOKEN": "synthetic-test-token-" * 3}, "sandbox", "fictional")
    assert "synthetic-test-token" not in repr(context)
    active = [BoundRemoteInterface(RemoteLedgerInterface(), context)]
    app = FastAPI()
    app.include_router(create_remote_router(lambda: active[0]))
    headers = {"Authorization": "Bearer " + context.config["SERVER_TOKEN"]}
    with TestClient(app, base_url="https://127.0.0.1") as client:
        path = "/remote/v1/budgets/fictional/queries/accounts"
        assert client.get(path, params={"workspace": "personal"}).status_code == 401
        assert client.get("http://127.0.0.1" + path, params={"workspace": "personal"}, headers=headers).status_code == 400
        assert client.get(path.replace("fictional", "different"), params={"workspace": "personal"}, headers=headers).status_code == 404
        initial = client.get(path, params={"workspace": "personal"}, headers=headers)
        assert initial.status_code == 200 and initial.json()["budgetId"] == "fictional"
        body = {"operation": "add_account", "workspace": "personal", "payload": {"name": "Fictional", "type": "depository", "balance": 12500},
                "request_id": "fictional-retry", "expected_revision": service.revision}
        command = "/remote/v1/budgets/fictional/commands"
        first = client.post(command, json=body, headers=headers)
        assert first.status_code == 200, first.text
        assert client.post(command, json=body, headers=headers).json() == first.json()
        assert len(store.accounts("personal")) == 1
        assert store.accounts("demo") == demo
        invalid = {**body, "request_id": "bad-money", "expected_revision": service.revision,
                   "payload": {**body["payload"], "balance": True}}
        assert client.post(command, json=invalid, headers=headers).status_code == 400
        assert len(store.accounts("personal")) == 1
        assert client.post(command, json={**body, "extra": 1}, headers=headers).status_code == 422
        active[0] = BoundRemoteInterface(RemoteLedgerInterface(commands=False), context)
        assert client.post(command, json=body, headers=headers).status_code == 405
        assert client.get(path, params={"workspace": "personal"}, headers=headers).status_code == 200
        active[0] = BoundRemoteInterface(RemoteLedgerInterface(queries=False), context)
        assert client.get(path, params={"workspace": "personal"}, headers=headers).status_code == 405
        active[0] = None
        assert client.get(path, params={"workspace": "personal"}, headers=headers).status_code == 404


def test_candidate_interface_requires_the_host_secret_before_activation(store):
    context = InterfaceContext(LedgerService(store), {}, "sandbox", "fictional")
    with pytest.raises(ValueError):
        BoundRemoteInterface({}, context)
    for token in ("", "too-short", "x" * 4097, 123):
        from whab.plugins import StoreError

        with pytest.raises(StoreError, match="bearer token"):
            BoundRemoteInterface(RemoteLedgerInterface(), replace(context, config={"SERVER_TOKEN": token}))
