"""Guarded settings, budget/demo isolation and real suggestion review persistence."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from whab_plugin_llm_categorization import oauth, providers, routes
from whab_plugin_llm_categorization.plugin import plugin
from whab_plugin_llm_categorization.private import Vault
from whab_plugin_server.plugin import plugin as server_plugin
from whab_plugin_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import InterfaceContext, PluginRegistry, StoreError

PATH = "/api/plugin-settings/llm-categorization"


@pytest.fixture
def client(store, context, tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_TOKEN", "fictional-server-token-" * 3)
    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    app = create_app(store=store, settings=configuration, plugins=PluginRegistry((web_plugin, plugin, server_plugin)))
    with TestClient(app, base_url="https://127.0.0.1:5173") as value:
        yield value


def local(client, action, **values):
    return client.post(PATH, json={"workspace": "personal", "action": action, **values})


def category(client, action, **values):
    return client.post("/api/categorization", json={"workspace": "personal", "payload": {
        "pluginId": plugin.id, "action": action, **values}})


def test_settings_require_tls_local_origin_and_separate_remote_owner(client):
    assert client.post(PATH, json={"workspace": "personal", "action": "status"}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("http://127.0.0.1:5173" + PATH, json={"workspace": "personal", "action": "status"}).status_code == 400
    assert client.post(PATH, json={"workspace": "personal", "action": "status"}, headers={"Host": "attacker.example"}).status_code == 403
    # The independent bearer-authenticated API remains selected alongside local settings.
    assert client.get("/remote/v1/budgets/default/queries/categories?workspace=personal").status_code == 401
    headers = {"Authorization": "Bearer " + "fictional-server-token-" * 3}
    assert client.get("/remote/v1/budgets/default/queries/categories?workspace=personal", headers=headers).status_code == 200
    assert local(client, "status").status_code == 200
    assert local(client, "status", api_key="", extra="reject").status_code == 400


def test_keys_never_return_or_enter_portable_files_and_demo_is_separate(client, store):
    assert local(client, "save-key", api_key="fictional-secret").status_code == 200
    status = local(client, "status")
    assert "fictional-secret" not in status.text
    assert next(row for row in status.json()["providers"] if row["id"] == "openai")["connected"]
    demo = client.post(PATH, json={"workspace": "demo", "action": "status"}).json()
    assert not any(row["connected"] for row in demo["providers"])
    categorization = client.app.state.ledger_service.categorization
    context = categorization.contexts[plugin.id]
    assert b"fictional-secret" not in context.read_file("connections.json")
    assert local(client, "disconnect").json()["message"].startswith("API key removed")
    assert not any(row["connected"] for row in local(client, "status").json()["providers"])
    for values in ({"provider": "chatgpt", "api_key": "x"}, {"api_key": " "}, {"api_key": "has space"}):
        assert local(client, "save-key", **values).status_code == 400
    assert local(client, "connect").status_code == 400


def test_local_settings_load_models_and_oauth_status_without_leaking_credentials(client, monkeypatch, fake_attempt):
    monkeypatch.setattr(providers, "models", lambda vault, provider: [{"id": "text-model", "name": "Text"}])
    assert local(client, "models").json()["models"][0]["id"] == "text-model"
    assert local(client, "auth-status").json()["status"] == "idle"
    monkeypatch.setattr(oauth, "SignIn", lambda _: fake_attempt)
    assert local(client, "connect", provider="chatgpt").json()["url"].startswith("https://auth.openai.com")
    assert local(client, "auth-status").json()["status"] == "connected"
    # A second attempt cancels the first and closing the settings view cancels it.
    assert local(client, "connect", provider="chatgpt").status_code == 200
    assert local(client, "cancel-sign-in").json()["status"] == "canceled"
    assert local(client, "cancel-sign-in").json()["status"] == "idle"
    assert local(client, "connect", provider="chatgpt").status_code == 200
    monkeypatch.setattr(oauth, "disconnect", lambda _: False)
    assert "not confirmed" in local(client, "disconnect", provider="chatgpt").json()["message"]
    monkeypatch.setattr(oauth, "disconnect", lambda _: True)
    assert local(client, "disconnect", provider="chatgpt").json()["message"] == "Disconnected."
    local(client, "connect", provider="chatgpt")  # Lifespan closes the outstanding attempt.


def test_actual_profile_suggestions_review_and_reopen(client, store, monkeypatch):
    one = store.add_category("personal", {"name": "Fictional food", "target": 0, "group": "Spending"})
    account = store.add_account("personal", {"name": "Fictional account", "type": "depository", "balance": 10000})
    tx = store.add_transaction("personal", {"accountId": account, "date": "2026-10-01", "amount": 1234,
                                           "merchant": "Fictional cafe", "categoryId": None})
    local(client, "save-key", api_key="fictional-key")
    config = {"provider": "openai", "model": "text-model", "consent": True,
              "categories": local(client, "status").json()["categories"], "examples": 0}
    result = category(client, "train", name="Zero-shot profile", parameters=config).json()
    token = result["session"]
    assert category(client, "step", session=token).status_code == 200
    result = category(client, "step", session=token).json()
    profile = result["model"]["id"]
    assert category(client, "select", modelId=profile).status_code == 200
    monkeypatch.setattr(providers, "complete", lambda *args: json.dumps({"suggestions": [{"transactionId": "0", "categoryId": one, "score": .95}]}))
    assert category(client, "suggest").json()["suggested"] == 0  # Automatic sending stays off.
    assert category(client, "preview").json()["suggested"] == 1
    suggestion = client.get("/api/categorization/suggestions?workspace=personal").json()[tx]
    transaction = next(row for row in store.transactions("personal") if row["id"] == tx)
    assert transaction["categoryId"] is None and transaction["reviewed"] is False
    html = client.get("/budgets/home/transactions/2026-10/usd").text
    assert "LLM suggested" in html
    assert client.patch("/api/transactions/" + tx, json={"workspace": "personal", "reviewed": True,
                                                     "suggestionId": suggestion["id"]}).status_code == 200
    transaction = next(row for row in store.transactions("personal") if row["id"] == tx)
    assert transaction["categoryId"] == one and transaction["reviewed"] is True
    namespace = client.app.state.ledger_service.categorization.contexts[plugin.id]
    from whab_plugin_llm_categorization.engine import Engine
    reopened = Engine(namespace)
    assert reopened.index()["active"] == profile
    assert reopened.index()["models"][0]["name"] == "Zero-shot profile"
    assert "fictional-key" not in json.dumps(reopened.index())


def test_provider_failure_leaves_import_and_categories_saved(client, store, monkeypatch):
    service = client.app.state.ledger_service.categorization
    account = store.add_account("personal", {"name": "Fictional account", "type": "depository", "balance": 0})
    store.add_transaction("personal", {"accountId": account, "date": "2026-10-01", "amount": 100,
                                      "merchant": "Fictional cafe", "categoryId": None})
    before = store.export_budget("personal")
    def fail(*args):
        raise StoreError("Fictional provider failure")
    monkeypatch.setattr(next(iter(service.engines.values())), "predict", fail)
    service.after_import()
    assert service.error.startswith("Import completed")
    assert store.export_budget("personal") == before


def test_settings_without_local_namespace_fail_safely():
    app = FastAPI()
    app.include_router(routes.router(InterfaceContext(SimpleNamespace(), {}, "sandbox", "fictional")))
    with TestClient(app, base_url="https://127.0.0.1", raise_server_exceptions=False) as client:
        assert local(client, "status").status_code == 500


def test_distinct_budget_namespaces_do_not_share_keys(context, tmp_path):
    first = Vault(context)
    first.write("openai", {"api_key": "first-budget-key"})
    files = {}
    other = replace(context, budget_id="second-budget", read_file=files.get,
                    write_file=lambda name, raw: files.__setitem__(name, raw))
    assert Vault(other).read("openai") == {}
    Vault(other).write("openai", {"api_key": "second-budget-key"})
    assert first.read("openai")["api_key"] == "first-budget-key"
    assert Vault(other).read("openai")["api_key"] == "second-budget-key"
    assert first.references() != Vault(other).references()


def test_local_settings_cannot_claim_other_route_namespaces(store, tmp_path):
    from whab.plugins import Interface, Plugin
    from whab.plugins.runtime import PluginError

    def factory(_):
        result = APIRouter()
        result.add_api_route("/remote/v1/unowned", lambda: {})
        return result
    bad = Plugin("fictional-settings", "Fictional settings", interface=Interface(factory, local_settings=True))
    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    with pytest.raises(PluginError, match="Interface plugin could not start"):
        create_app(store=store, settings=configuration, plugins=PluginRegistry((bad,)))
