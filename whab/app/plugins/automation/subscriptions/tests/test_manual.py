"""Model-free subscription workflows, persistence and public mutation guarantees."""

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from whab_plugin_subscriptions.api import Subscriptions, install_ai, scope
from whab_plugin_subscriptions.engine import Engine, configuration
from whab_plugin_subscriptions.plugin import plugin
from whab_plugin_web.plugin import plugin as web

from whab.app import create_app
from whab.config import Settings
from whab.plugins import InterfaceContext, PluginRegistry, StoreError


@pytest.fixture
def app(store, tmp_path):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    return create_app(store=store, settings=settings, plugins=PluginRegistry((web, plugin)))


def payment(store, **values):
    account = store.add_account("personal", {"name": "Fictional account", "type": "depository", "balance": 10000})
    return store.add_transaction("personal", {"accountId": account, "date": "2026-10-15",
        "merchant": "Fictional plan", "amount": 1200, "categoryId": None, **values})


def test_manual_http_and_feature_commands_preserve_ledger_and_reuse_new_payments(app, store):
    identifier = payment(store)
    client = TestClient(app, base_url="https://127.0.0.1:5173")
    body = {"workspace": "personal", "month": "2026-10", "currency": "USD", "action": "status"}
    path = "/api/plugin-settings/subscriptions"
    with client:
        status = client.post(path, json=body).json()
        assert status["classificationMode"] == "manual" and not status["aiAvailable"]
        assert client.post(path, json={**body, "action": "job"}).json() == {"status": "idle"}
        for action in ("configure", "classify", "start", "stop"):
            assert client.post(path, json={**body, "action": action}).status_code == 400
        assert client.post(path, json={**body, "action": "set-mode", "mode": "ai"}).status_code == 400
        assert client.post(path, json={**body, "action": "set-mode", "mode": "manual"}).status_code == 200
        row = status["transactions"][0]
        assert client.post(path, json={**body, "action": "override", "transactionId": identifier,
            "fingerprint": row["fingerprint"], "decision": {}}).status_code == 400
        service = app.state.ledger_service
        feature = {"pluginId": "subscriptions", "action": "report", "parameters": {"month": "2026-10", "currency": "USD"}}
        read = service.query("feature", "personal", parameters=feature)
        assert "config" not in read["data"]
        assert service.query("features", "personal")["data"]["subscriptions"]["commands"] == ["set_label"]
        before = store.export_budget()
        payload = {"pluginId": "subscriptions", "action": "set_label", "parameters": {
            "transactionId": identifier, "fingerprint": row["fingerprint"], "isSubscription": True, "cadence": "monthly"}}
        result = service.command("feature", "personal", payload, request_id="label-one", expected_revision=read["revision"])
        assert result["data"]["saved"]
        assert service.command("feature", "personal", payload, request_id="label-one", expected_revision=read["revision"]) == result
        assert store.export_budget() == before
        assert client.post(path, json=body).json()["estimatedMonthly"] == 1200
        old = store.transactions("personal")[0]
        store.add_transaction("personal", {"accountId": old["accountId"], "date": "2026-11-15",
            "merchant": old["merchant"], "amount": 1200, "categoryId": None})
        report = service.query("feature", "personal", parameters={**feature, "parameters": {"month": "2026-11", "currency": "USD"}})["data"]
        assert all(row["status"] == "subscription" for row in report["transactions"])
        assert all(row["source"] == "manual" for row in report["transactions"])
        assert service.query("feature", "demo", parameters=feature)["data"]["estimatedMonthly"] == 0
        # The compatibility UI route uses the same explicit mutation semantics.
        assert client.post(path, json={**body, "action": "override", "transactionId": identifier,
            "fingerprint": row["fingerprint"], "decision": {"isSubscription": False, "cadence": "unknown", "score": 1}}).status_code == 200


@pytest.mark.parametrize("value", [{}, {"month": "bad", "currency": "USD"}, {"month": "2026-13", "currency": "USD"}, {"month": "2026-10", "currency": "usd"}])
def test_report_scope_validation(value):
    with pytest.raises(StoreError):
        scope(value)


def test_public_validation_absent_context_and_preserved_legacy_configuration(app):
    service = app.state.ledger_service
    feature = service.feature_adapter("subscriptions")
    for params in ({}, {"month": "2026-10", "currency": "USD", "extra": True}):
        with pytest.raises(StoreError):
            feature.query("personal", "report", params)
    with pytest.raises(StoreError):
        feature.command("personal", "set_label", {})
    with pytest.raises(StoreError):
        configuration({})
    detached = Subscriptions(InterfaceContext(type(service)(object()), {}, "sandbox", "other"))
    with pytest.raises(StoreError):
        detached.budget("personal")
    install_ai(service, lambda context: object())
    with pytest.raises(StoreError):
        install_ai(service, lambda context: object())
    # The public collaboration context retains the base namespace, never secrets.
    assert feature.context.budget is not None


def test_ai_packages_cannot_be_imported_by_model_free_plugins():
    code = '''
import importlib.abc, sys
class BlockAI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("whab_plugin_ai", "whab_plugin_llm", "whab_plugin_subscriptions_ai", "sklearn")):
            raise RuntimeError("AI import forbidden")
sys.meta_path.insert(0, BlockAI())
from whab.plugins import load_plugins
registry = load_plugins(("sqlite", "web", "subscriptions", "cli", "server"))
assert {p.id for p in registry.descriptors()} == {"sqlite", "web", "subscriptions", "cli", "server"}
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_mode_and_saved_file_errors_are_authored(app):
    feature = app.state.ledger_service.feature_adapter("subscriptions")
    feature.budget("personal").write_file("classification-mode.json", b"invalid")
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        response = client.post("/api/plugin-settings/subscriptions", json={"workspace": "personal", "month": "2026-10", "currency": "USD", "action": "status"})
        assert response.status_code == 400
    feature.budget("personal").write_file("subscriptions.json", json.dumps({"version": 1, "config": {}, "labels": {}}).encode())
    with pytest.raises(StoreError):
        Engine(feature.budget("personal"))
