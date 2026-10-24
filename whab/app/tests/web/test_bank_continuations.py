"""The host retains scoped continuation identity across Link and lost HTTP replies."""

from copy import deepcopy
from dataclasses import replace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.plugins import Plugin, PluginRegistry, WebhookResult
from whab.plugins.effect_store import EffectError

FLOW = "a" * 32
OTHER = "b" * 32


def status(stage="ready", **changes):
    return {
        "flow_id": FLOW, "generation": 7, "kind": "link", "stage": stage,
        "deadline_ms": 4_000_000_000_000, **changes,
    }


class ContinuedBank:
    config = {"configured": True}

    def __init__(self, flow_id=FLOW):
        self.flow_id = flow_id
        self.calls = []
        self.result = None
        self.error = None
        self.stage = "ready"
        self.kind = "link"

    async def link_token(self, item_id=None):
        pytest.fail("A continued service must never use the uncorrelated legacy ABI")

    async def exchange(self, public_token, institution_name=None):
        pytest.fail("A continued service must never use the uncorrelated legacy ABI")

    def outcome(self, **changes):
        if self.error:
            raise self.error
        return deepcopy(self.result) if self.result is not None else status(self.stage, flow_id=self.flow_id, kind=self.kind, **changes)

    async def begin_link(self, item_id=None):
        self.calls.append(("begin", item_id))
        self.kind = "update" if item_id else "link"
        return self.outcome(link_token="fictional-link-token")

    async def continuation_status(self, flow_id):
        self.calls.append(("status", flow_id))
        if flow_id != self.flow_id:
            raise EffectError("continuation_missing")
        return self.outcome()

    async def cancel_link(self, flow_id):
        self.calls.append(("cancel", flow_id))
        return self.outcome()

    async def complete_link(self, flow_id, public_token=None, institution_name=None):
        self.calls.append(("complete", flow_id, public_token, institution_name))
        return self.outcome()

    async def sync(self, item_id=None):
        return {"added": 0, "modified": 0, "removed": 0}

    async def disconnect(self, item_id):
        pass

    async def webhook(self, raw, headers):
        return WebhookResult(False, 401)

    async def close(self):
        pass


@pytest.fixture
def connection_client(store, tmp_path):
    bank = ContinuedBank()
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    registry = PluginRegistry((Plugin("fictional", "Fictional provider", bank=lambda _: bank),))
    with TestClient(create_app(store=store, settings=settings, plugins=registry), base_url="https://127.0.0.1:5173") as client:
        yield client, bank, "/api/banks/fictional"


def test_new_connection_update_status_and_cancellation_use_exact_facade(connection_client):
    client, bank, prefix = connection_client
    assert client.post(prefix + "/link-token", json={}).json() == status(link_token="fictional-link-token")
    assert client.get(prefix + f"/continuations/{FLOW}").json() == status()
    assert client.post(prefix + "/link-token", json={"itemId": "original/item"}).json() == status(kind="update", link_token="fictional-link-token")
    bank.stage = "complete"
    assert client.post(prefix + "/exchange", json={"flowId": FLOW}).json() == status("complete", kind="update")
    assert bank.calls[-1] == ("complete", FLOW, None, None)
    bank.stage = "cancelled"
    assert client.delete(prefix + f"/continuations/{FLOW}").json() == status("cancelled", kind="update")
    assert bank.calls[-1] == ("cancel", FLOW)


def test_completion_preserves_same_flow_and_request_for_native_reconciliation(connection_client):
    client, bank, prefix = connection_client
    body = {"flowId": FLOW, "publicToken": "fictional-public-token", "institutionId": "fictional-bank", "institutionName": "Fictional Bank"}
    bank.stage = "complete"
    for _ in range(2):
        assert client.post(prefix + "/exchange", json=body).json() == status("complete")
    assert bank.calls == [("complete", FLOW, "fictional-public-token", "Fictional Bank")] * 2
    # The route delegates receipt reconciliation; it never generates another operation identity.


@pytest.mark.parametrize("code", ["continuation_missing", "continuation_expired", "continuation_busy", "exchange_uncertain", "apply_uncertain", "effect_conflict"])
@pytest.mark.parametrize("operation", ["begin", "status", "cancel", "complete"])
def test_fixed_effect_errors_have_conflict_status_without_private_payload(connection_client, code, operation):
    client, bank, prefix = connection_client
    bank.error = EffectError(code)
    if operation == "begin":
        response = client.post(prefix + "/link-token", json={})
    elif operation == "status":
        response = client.get(prefix + f"/continuations/{FLOW}")
    elif operation == "cancel":
        response = client.delete(prefix + f"/continuations/{FLOW}")
    else:
        response = client.post(prefix + "/exchange", json={"flowId": FLOW, "publicToken": "private-token"})
    assert response.status_code == 409
    assert response.json() == {"code": code, "error": str(EffectError(code))}
    assert "private-token" not in response.text


def test_missing_flow_never_calls_new_or_legacy_exchange(connection_client):
    client, bank, prefix = connection_client
    for body in ({}, {"publicToken": "private-token"}):
        response = client.post(prefix + "/exchange", json=body)
        assert response.status_code == 409 and response.json()["code"] == "continuation_missing"
    assert bank.calls == []


@pytest.mark.parametrize("flow_id", [OTHER, "../another", "A" * 32, "a" * 31, "a" * 33, "a" * 32 + "\n"])
def test_unavailable_or_malformed_flow_never_crosses_scope(connection_client, flow_id):
    client, bank, prefix = connection_client
    response = client.get(prefix + "/continuations/" + quote(flow_id, safe=""))
    assert response.status_code in {400, 404, 409}
    if flow_id == OTHER:
        assert response.json()["code"] == "continuation_missing"
    else:
        assert bank.calls == []


@pytest.mark.parametrize("body", [
    {"flowId": True}, {"flowId": "A" * 32}, {"flowId": FLOW, "generation": 8},
    {"flowId": FLOW, "publicToken": ""}, {"flowId": FLOW, "publicToken": 123},
    {"flowId": FLOW, "publicToken": "x" * 501}, {"flowId": FLOW + "\n"},
])
def test_strict_request_validation_precedes_any_provider_work(connection_client, body):
    client, bank, prefix = connection_client
    assert client.post(prefix + "/exchange", json=body).status_code == 400
    assert bank.calls == []


@pytest.mark.parametrize("changes", [
    {"flow_id": OTHER}, {"flow_id": "A" * 32}, {"generation": True}, {"generation": 0},
    {"generation": 2**53}, {"kind": "unknown"}, {"kind": []}, {"stage": "unknown"},
    {"stage": []}, {"deadline_ms": True}, {"deadline_ms": 0}, {"deadline_ms": 2**53},
    {"secret": "private-response"},
])
def test_provider_status_is_exact_bounded_data_not_arbitrary_json(connection_client, changes):
    client, bank, prefix = connection_client
    bank.result = status(**changes)
    response = client.get(prefix + f"/continuations/{FLOW}")
    assert response.status_code == 400
    assert response.json() == {"error": "Bank plugin returned an invalid continuation response."}
    assert "private-response" not in response.text


@pytest.mark.parametrize("result", [None, [], {}, "private-response", status(), status(stage="complete", link_token="secret"), status(link_token=""), status(link_token="x" * 4097), status(link_token="nul\x00token"), status(kind="update", link_token="token")])
def test_begin_requires_ready_and_exact_token_response(connection_client, result):
    client, bank, prefix = connection_client
    # None means default result in this fake; use a separate callable for this one invalid case.
    async def invalid_begin(item_id=None):
        return result
    bank.begin_link = invalid_begin
    response = client.post(prefix + "/link-token", json={})
    assert response.status_code == 400
    assert "private-response" not in response.text


def test_partial_facade_never_silently_downgrades_and_unexpected_errors_are_sanitized(connection_client):
    client, bank, prefix = connection_client
    bank.cancel_link = None
    assert client.post(prefix + "/link-token", json={}).json() == {"error": "Bank plugin has an incomplete continuation interface."}
    assert bank.calls == []
    bank.cancel_link = lambda _: None
    bank.error = RuntimeError("private-provider-payload")
    response = client.get(prefix + f"/continuations/{FLOW}")
    assert response.status_code == 400
    assert "private-provider-payload" not in response.text


def test_continuation_routes_keep_https_host_origin_and_signed_webhook_boundaries(connection_client):
    client, bank, prefix = connection_client
    requests = [("post", "/link-token", {}), ("post", "/exchange", {"flowId": FLOW}), ("get", f"/continuations/{FLOW}", None), ("delete", f"/continuations/{FLOW}", None)]
    for method, suffix, body in requests:
        options = {"json": body} if body is not None else {}
        assert client.request(method, "http://127.0.0.1:5173" + prefix + suffix, **options).json() == {"error": "HTTPS is required"}
        assert client.request(method, prefix + suffix, headers={"Host": "public.example"}, **options).status_code == 403
        assert client.request(method, prefix + suffix, headers={"Origin": "https://public.example"}, **options).status_code == 403
    assert bank.calls == []
    assert client.post(prefix + "/webhook", json={}, headers={"Host": "public.example"}).status_code == 401


def test_restart_only_service_remains_explicit_legacy_and_cannot_accept_flow_ids(connection_client):
    client, bank, prefix = connection_client
    for name in ("begin_link", "continuation_status", "cancel_link", "complete_link"):
        setattr(bank, name, None)
    assert client.get(prefix + f"/continuations/{FLOW}").status_code == 404
    assert client.delete(prefix + f"/continuations/{FLOW}").status_code == 404
    assert client.post(prefix + "/exchange", json={"flowId": FLOW, "publicToken": "private-token"}).status_code == 400
    assert client.post(prefix + "/exchange", json={}).status_code == 400
    assert bank.calls == []


def test_addressed_budget_and_legacy_plaid_alias_keep_continuations_isolated(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("whab_plugin_plaid.plugin")
    services, environments = {}, []

    def factory(context):
        identifier = context.budget.budget_id if context.budget else "default"
        services[identifier] = ContinuedBank(FLOW if identifier == "default" else OTHER)
        environments.append(context.environment)
        return services[identifier]

    monkeypatch.setattr(module, "plugin", replace(module.plugin, bank=factory))
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ("plaid",), ui="")
    with TestClient(create_app(settings=settings), base_url="https://127.0.0.1:5173") as client:
        created = client.post("/budgets/home/api/budget/new", json={"name": "Fictional second budget"})
        assert created.status_code == 200, created.text
        identifier = created.json()["id"]
        assert identifier in services and set(environments) == {"sandbox"}
        # Creation selects the other budget cookie; explicit home URLs retain their original scope.
        for prefix in ("/budgets/home/api/plaid", "/budgets/home/api/banks/plaid"):
            assert client.post(prefix + "/link-token", json={}).json()["flow_id"] == FLOW
            response = client.get(prefix + f"/continuations/{OTHER}")
            assert response.status_code == 409 and response.json()["code"] == "continuation_missing"
        prefix = f"/budgets/{identifier}/api/banks/plaid"
        assert client.post(prefix + "/link-token", json={}).json()["flow_id"] == OTHER
        assert client.get(prefix + f"/continuations/{FLOW}").status_code == 409
        before = [list(service.calls) for service in services.values()]
        for method, suffix in (("get", f"/continuations/{FLOW}"), ("delete", f"/continuations/{FLOW}"), ("post", "/link-token")):
            options = {"json": {}} if method == "post" else {}
            assert client.request(method, "/budgets/demo/api/banks/plaid" + suffix, **options).status_code == 403
        assert before == [service.calls for service in services.values()]


def test_captured_bank_bundle_renders_through_visual_projection_and_versioned_dispatch(tmp_path, store):
    import re

    from whab_plugin_web.plugin import plugin as web_plugin

    from whab.plugins import BankAdapter, BankUI, Capability, PluginContract, PluginResources

    bank = ContinuedBank()
    resources = PluginResources({'templates/accounts.html': b'<section data-fictional-bank>Fictional connection panel</section>',
                                 'static/connect.js': b'/* fictional immutable provider UI */'})
    descriptor = Plugin('fictional', 'Fictional provider', bank=BankAdapter(lambda _: pytest.fail('Use injected synthetic service')),
        bank_ui=BankUI(resources=resources), contract=PluginContract(lifecycle='live', provides=(Capability('lifecycle.background'),)))
    configuration = Settings(tmp_path, 5173, 'sandbox', '', '', '', False, '', '', (), ui='web')
    app = create_app(store=store, settings=configuration, plugins=PluginRegistry((web_plugin, descriptor)),
                     bank_services={'fictional': bank})
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        page = client.get('/budgets/home/accounts/current-month/usd')
        assert page.status_code == 200 and 'data-fictional-bank' in page.text
        source = re.search(r'/budgets/home/banks/fictional/[a-f0-9]{32}/static/connect.js', page.text)[0]
        assert client.get(source).content == resources.files['static/connect.js']
        assert client.get('/plugins/fictional/static/connect.js').content == resources.files['static/connect.js']
