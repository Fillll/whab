"""Actual guarded host routes, saved labels, demo isolation and stale inference."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from whab_plugin_ai_credentials import api as ai
from whab_plugin_ai_credentials.plugin import plugin as credentials_plugin
from whab_plugin_subscriptions.plugin import plugin
from whab_plugin_subscriptions.routes import router
from whab_plugin_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import InterfaceContext, PluginRegistry

PATH = "/api/plugin-settings/subscriptions"
CREDENTIALS = "/api/plugin-settings/ai-credentials"


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    app = create_app(
        store=store,
        settings=settings,
        plugins=PluginRegistry((web_plugin, credentials_plugin, plugin)),
    )
    with TestClient(app, base_url="https://127.0.0.1:5173") as value:
        yield value


def request(client, action, **values):
    return client.post(
        PATH,
        json={
            "workspace": "personal",
            "month": "2026-10",
            "currency": "USD",
            "action": action,
            **values,
        },
    )


def test_host_scopes_non_categorizer_settings_and_persists_review_without_ledger_edits(
    client, store, monkeypatch
):
    account = store.add_account(
        "personal", {"name": "Fictional checking", "type": "depository", "balance": 10000}
    )
    transaction = store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-15",
            "merchant": "Fictional media",
            "amount": 1200,
            "categoryId": None,
        },
    )
    assert request(client, "classify").status_code == 400
    assert request(client, "configure").status_code == 400
    assert (
        client.post(
            CREDENTIALS,
            json={"workspace": "personal", "action": "save-key", "api_key": "fictional-secret"},
        ).status_code
        == 200
    )
    providers = client.post(CREDENTIALS, json={"workspace": "personal", "action": "status"}).json()[
        "providers"
    ]
    reference = next(p["connectionId"] for p in providers if p["id"] == "openai")
    config = {
        "provider": "openai",
        "model": "fictional-model",
        "connectionId": reference,
        "effort": "default",
        "consent": True,
    }
    assert request(client, "configure", config=config).status_code == 200
    before = store.export_budget()
    monkeypatch.setattr(
        ai,
        "complete",
        lambda *a, **k: json.dumps(
            {
                "classifications": [
                    {
                        "transactionId": "0",
                        "isSubscription": True,
                        "cadence": "monthly",
                        "score": 0.95,
                    }
                ]
            }
        ),
    )
    assert request(client, "classify").json()["classified"] == 1
    result = request(client, "status").json()
    assert result["estimatedMonthly"] == 1200
    assert "fictional-secret" not in json.dumps(result)
    assert request(client, "classify").json() == {"remaining": 0, "classified": 0, "reused": 0}
    row = next(r for r in result["transactions"] if r["id"] == transaction)
    decision = {"isSubscription": False, "cadence": "unknown", "score": 1}
    assert (
        request(
            client, "override", transactionId=transaction, fingerprint="stale", decision=decision
        ).status_code
        == 400
    )
    assert (
        request(
            client,
            "override",
            transactionId=transaction,
            fingerprint=row["fingerprint"],
            decision=decision,
        ).json()["estimatedMonthly"]
        == 0
    )
    assert request(client, "status").json()["transactions"][0]["status"] == "not-subscription"
    assert store.export_budget() == before
    assert request(client, "status", workspace="demo").json()["config"]["consent"] is False
    page = client.get("/budgets/home/insights/2026-10/usd")
    assert page.status_code == 200 and 'data-dialog="subscriptions-dialog"' in page.text


def test_credential_adoption_is_bounded_preserves_new_shared_connections_and_demo(client):
    def post(**values):
        return client.post(CREDENTIALS, json={"workspace": "personal", "action": "adopt", **values})

    assert post(connections={"other": "invalid"}).status_code == 400
    assert post(connections={"openai": "invalid"}).status_code == 400
    old = "00000000-0000-0000-0000-000000000000"
    assert post(connections={"openai": old}).status_code == 200
    assert (
        client.post(
            CREDENTIALS,
            json={"workspace": "personal", "action": "save-key", "api_key": "fictional"},
        ).status_code
        == 200
    )
    assert post(connections={"openai": "00000000-0000-0000-0000-000000000001"}).status_code == 200
    status = client.post(CREDENTIALS, json={"workspace": "personal", "action": "status"}).json()
    assert next(p for p in status["providers"] if p["id"] == "openai")["connectionId"] == old
    demo = client.post(CREDENTIALS, json={"workspace": "demo", "action": "status"}).json()
    assert not any(p["connected"] for p in demo["providers"])


def test_stale_inference_leaves_cache_unchanged(client, store, monkeypatch):
    account = store.add_account(
        "personal", {"name": "Fictional", "type": "depository", "balance": 0}
    )
    store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-15",
            "merchant": "Fictional plan",
            "amount": 100,
            "categoryId": None,
        },
    )
    client.post(
        CREDENTIALS, json={"workspace": "personal", "action": "save-key", "api_key": "fictional"}
    )
    reference = client.post(CREDENTIALS, json={"workspace": "personal", "action": "status"}).json()[
        "providers"
    ][1]["connectionId"]
    request(
        client,
        "configure",
        config={
            "provider": "openai",
            "connectionId": reference,
            "model": "fictional",
            "effort": "default",
            "consent": True,
        },
    )

    def response(*args, **kwargs):
        client.app.state.ledger_service.invalidate_revision()
        return json.dumps(
            {
                "classifications": [
                    {
                        "transactionId": "0",
                        "isSubscription": True,
                        "cadence": "monthly",
                        "score": 0.9,
                    }
                ]
            }
        )

    monkeypatch.setattr(ai, "complete", response)
    assert request(client, "classify").status_code == 400
    assert request(client, "status").json()["unclassified"] == 1


def test_routes_keep_tls_origin_host_and_strict_body_validation(client):
    body = {"workspace": "personal", "month": "2026-10", "currency": "USD", "action": "status"}
    for path in (PATH, CREDENTIALS):
        payload = body if path == PATH else {"workspace": "personal", "action": "status"}
        assert client.post("http://127.0.0.1:5173" + path, json=payload).status_code == 400
        assert (
            client.post(
                path, json=payload, headers={"Origin": "https://attacker.example"}
            ).status_code
            == 403
        )
        assert (
            client.post(path, json=payload, headers={"Host": "attacker.example"}).status_code == 403
        )
    assert request(client, "status", extra=True).status_code == 400
    assert request(client, "status", month="2026-13").status_code == 400


def test_missing_local_budget_fails_safely():
    app = FastAPI()
    app.include_router(router(InterfaceContext(SimpleNamespace(), {}, "sandbox", "fictional")))
    with TestClient(app, base_url="https://127.0.0.1", raise_server_exceptions=False) as client:
        assert request(client, "status").status_code == 500


def test_portable_labels_and_shared_references_survive_reopen_and_keep_other_budgets_separate(
    tmp_path, store, row
):
    from whab_plugin_subscriptions.engine import Engine

    from whab.budgets.container import decode_container
    from whab.budgets.session import BudgetSession

    path = tmp_path / "fictional.whab"
    session = BudgetSession.create(
        tmp_path / "first", "sandbox", "Fictional", ("ai-credentials", "subscriptions"), path=path
    )
    session.bind(store)
    engine = Engine(session.context("subscriptions"))
    engine.put(row, {"isSubscription": True, "cadence": "monthly", "score": 1}, "manual")
    engine.write()
    session.context("ai-credentials").write_file(
        "connections.json", b'{"openai":"00000000-0000-0000-0000-000000000000"}'
    )
    exported = decode_container(session.bundle())
    assert "plugins/subscriptions/subscriptions.json" in exported.files
    assert "plugins/ai-credentials/connections.json" in exported.files
    reopened = BudgetSession.open(tmp_path / "reopened", "sandbox", path)
    assert Engine(reopened.context("subscriptions")).label(row)["source"] == "manual"
    assert (
        reopened.context("ai-credentials").read_file("connections.json")
        == exported.files["plugins/ai-credentials/connections.json"]
    )
    other = BudgetSession.create(
        tmp_path / "second", "sandbox", "Other", ("ai-credentials", "subscriptions")
    )
    assert Engine(other.context("subscriptions")).label(row) is None
    assert other.context("ai-credentials").read_file("connections.json") is None


def test_background_start_poll_stop_and_resume_are_request_independent(client, store, monkeypatch):
    from threading import Event
    from time import monotonic
    from uuid import uuid4

    account = store.add_account(
        "personal", {"name": "Fictional", "type": "depository", "balance": 0}
    )
    for index in range(22):
        store.add_transaction(
            "personal",
            {
                "accountId": account,
                "date": "2026-10-15",
                "merchant": f"Fictional plan {index}",
                "amount": 100,
                "categoryId": None,
            },
        )
    client.post(
        CREDENTIALS, json={"workspace": "personal", "action": "save-key", "api_key": "fictional"}
    )
    providers = client.post(CREDENTIALS, json={"workspace": "personal", "action": "status"}).json()[
        "providers"
    ]
    reference = next(r["connectionId"] for r in providers if r["id"] == "openai")
    config = {
        "provider": "openai",
        "connectionId": reference,
        "model": "fictional",
        "effort": "default",
        "consent": True,
    }
    entered, release = Event(), Event()
    calls = []

    def response(vault, provider, model, payload, *args, **kwargs):
        calls.append(len(payload["transactions"]))
        entered.set()
        assert release.wait(5)
        return json.dumps(
            {
                "classifications": [
                    {
                        "transactionId": r["transactionId"],
                        "isSubscription": True,
                        "cadence": "monthly",
                        "score": 0.95,
                    }
                    for r in payload["transactions"]
                ]
            }
        )

    monkeypatch.setattr(ai, "complete", response)
    operation = str(uuid4())
    try:
        assert (
            request(client, "start", operationId=operation, config=config).json()["id"] == operation
        )
        assert entered.wait(3)
        assert request(client, "job").json()["total"] == 22
        assert request(client, "status").json()["job"]["status"] == "running"
        assert request(client, "start", operationId=str(uuid4())).json()["id"] == operation
        for action in ("configure", "classify", "override"):
            denied = request(client, action)
            assert (
                denied.status_code == 400 and "running in the background" in denied.json()["error"]
            )
        assert request(client, "stop", jobId=str(uuid4())).status_code == 400
        assert request(client, "stop", jobId=operation).json()["status"] == "stopping"
        release.set()

        def done():
            deadline = monotonic() + 5
            while True:
                value = request(client, "job").json()
                if value["status"] not in {"preparing", "running", "stopping"}:
                    return value
                assert monotonic() < deadline

        assert done()["status"] == "stopped"
        assert request(client, "start", operationId=str(uuid4())).status_code == 200
        result = done()
        assert result["status"] == "completed" and result["total"] == 2
        assert calls == [20, 2]
    finally:
        release.set()
