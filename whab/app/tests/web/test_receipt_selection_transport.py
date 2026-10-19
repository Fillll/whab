"""Bounded receipt markers remain private, compatible and entirely read-only."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from tests.receipt_images import png
from whab.app import create_app
from whab.budgets.host import BudgetHost
from whab.config import Settings

BASE = "https://127.0.0.1:5173"


def settings(home):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui="web")


@pytest.fixture
def selection_client(tmp_path, store):
    account = store.add_account("personal", {"name": "Synthetic cash", "type": "depository", "balance": 1000})
    for identifier in ("active", "pending", "deleted", "off-page", "é", "e\u0301"):
        store.add_transaction("personal", {
            "id": identifier, "accountId": account, "date": "2026-10-01",
            "merchant": "Synthetic shop", "amount": 10, "categoryId": "groceries",
        })
        store.save_receipt("personal", identifier, png())
    store.db.execute("UPDATE transactions SET pending=1 WHERE workspace='personal' AND id='pending'")
    store.db.execute("UPDATE transactions SET deleted=1 WHERE workspace='personal' AND id='deleted'")
    store.db.execute("UPDATE accounts SET active=0 WHERE workspace='personal'")
    demo_account = store.accounts("demo")[0]["id"]
    store.add_transaction("demo", {
        "id": "foreign", "accountId": demo_account, "date": "2026-10-01",
        "merchant": "Synthetic sample", "amount": 10, "categoryId": None,
    })
    store.save_receipt("demo", "foreign", png())
    app = create_app(store=store, settings=settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        yield client, app, store


def body(identifiers, **changes):
    return {"workspace": "personal", "transactionIds": identifiers, **changes}


def test_scoped_marker_read_is_bounded_and_changes_no_state_revision_or_receipt_bytes(selection_client, monkeypatch):
    client, app, store = selection_client
    before = store.db.serialize()
    revision = app.state.ledger_service.revision
    for name in ("transactions", "receipt_ids", "get_receipt"):
        monkeypatch.setattr(store, name, lambda *_: pytest.fail("Native markers must not read full indexes, history or images."))
    app.state.tracked_store.on_mutation = lambda: pytest.fail("Receipt markers must not mutate the budget.")
    selected = ["pending", "active", "missing", "deleted", "foreign"]
    response = client.post("/api/receipts/index", json=body(selected))
    assert response.status_code == 200 and response.json() == {
        "supported": True, "transactionIds": ["active", "pending"],
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert store.db.serialize() == before and app.state.ledger_service.revision == revision


def test_legacy_fallback_returns_only_requested_ids_and_old_get_stays_complete(selection_client, monkeypatch):
    client, _, store = selection_client
    original = store.receipt_ids("personal")
    monkeypatch.setattr(store, "receipt_ids_for", None)
    response = client.post("/api/receipts/index", json=body(["pending", "active", "foreign"]))
    assert response.status_code == 200 and response.json() == {
        "supported": True, "transactionIds": ["active", "pending"],
    }
    assert client.get("/api/receipts?workspace=personal").json() == {
        "supported": True, "transactionIds": original,
    }


def test_marker_route_preserves_unicode_identity_and_accepts_the_full_selection_bound(selection_client):
    client, _, _ = selection_client
    response = client.post("/api/receipts/index", json=body(["é", "e\u0301"]))
    assert response.status_code == 200 and response.json()["transactionIds"] == ["e\u0301", "é"]
    response = client.post("/api/receipts/index", json=body(["active", *(
        f"missing-{index}" for index in range(99)
    )]))
    assert response.status_code == 200 and response.json()["transactionIds"] == ["active"]


@pytest.mark.parametrize("changes", [
    {"transactionIds": []}, {"transactionIds": ["active"] * 101},
    {"transactionIds": "active"}, {"transactionIds": [1]}, {"transactionIds": [True]},
    {"transactionIds": [None]}, {"transactionIds": [{}]}, {"transactionIds": ["active", "active"]},
    {"transactionIds": ["bad\x00id"]}, {"transactionIds": ["x" * 201]},
    {"workspace": "other"}, {"reviewed": True}, {"requestId": "unexpected"},
])
def test_scoped_marker_route_rejects_invalid_fields_before_backend_reads(selection_client, monkeypatch, changes):
    client, app, store = selection_client
    monkeypatch.setattr(store, "receipt_ids_for", lambda *_: pytest.fail("Invalid selection must not reach storage."))
    before, revision = store.db.serialize(), app.state.ledger_service.revision
    response = client.post("/api/receipts/index", json=body(["active"], **changes))
    assert response.status_code == 400
    assert store.db.serialize() == before and app.state.ledger_service.revision == revision


@pytest.mark.parametrize("field", ["workspace", "transactionIds"])
def test_scoped_marker_route_requires_both_fields(selection_client, field):
    client, _, _ = selection_client
    payload = body(["active"])
    del payload[field]
    assert client.post("/api/receipts/index", json=payload).status_code == 400


@pytest.mark.parametrize("identifier", ["\ud800", "bad\udfff"])
def test_escaped_lone_surrogates_are_clean_transport_validation_errors(selection_client, monkeypatch, identifier):
    client, _, store = selection_client
    monkeypatch.setattr(store, "receipt_ids_for", lambda *_: pytest.fail("Unencodable IDs must not reach SQLite."))
    # Escaped ASCII JSON reaches the server rather than failing in httpx's UTF-8 encoder.
    response = client.post("/api/receipts/index", content=json.dumps(body([identifier])),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and response.json() == {"error": "Invalid request"}


def test_malformed_native_index_cannot_return_unrequested_private_identifiers(selection_client, monkeypatch):
    client, _, store = selection_client
    monkeypatch.setattr(store, "receipt_ids_for", lambda *_: ["synthetic-private-unrequested-id"])
    response = client.post("/api/receipts/index", json=body(["active"]))
    assert response.status_code == 400 and response.json() == {
        "error": "Storage returned an invalid receipt selection index.",
    }
    assert "synthetic-private-unrequested-id" not in response.text


def test_context_advertises_route_support_with_legacy_or_unsupported_receipt_storage(selection_client, tmp_path, monkeypatch):
    client, _, store = selection_client

    def context(response):
        assert response.status_code == 200
        return json.loads(re.search(
            r'<script id="whab-context" type="application/json">(.*?)</script>', response.text,
        )[1])

    monkeypatch.setattr(store, "receipt_ids_for", None)
    assert context(client.get("/budgets/home/transactions/2026-10/usd"))["receiptIndexScoped"] is True
    monkeypatch.setattr(store, "save_receipt", None)
    with TestClient(create_app(store=store, settings=settings(tmp_path)), base_url=BASE) as unsupported:
        assert context(unsupported.get("/budgets/home/transactions/2026-10/usd"))["receiptIndexScoped"] is True
        assert unsupported.post("/api/receipts/index", json=body(["active"])).json() == {
            "supported": False, "transactionIds": [],
        }
        assert unsupported.get("/api/receipts?workspace=personal").json() == {
            "supported": False, "transactionIds": [],
        }


@pytest.mark.parametrize("url,headers,status", [
    ("http://127.0.0.1:5173", {"X-Forwarded-Proto": "https"}, 400),
    (BASE, {"Host": "untrusted.invalid"}, 403),
    (BASE, {"Origin": "https://untrusted.invalid"}, 403),
    (BASE, {"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_scoped_marker_route_retains_tls_host_and_origin_guards(selection_client, monkeypatch, url, headers, status):
    client, _, store = selection_client
    monkeypatch.setattr(store, "receipt_ids_for", lambda *_: pytest.fail("Rejected transport must not read storage."))
    response = client.post(url + "/budgets/home/api/receipts/index", json=body(["active"]), headers=headers)
    assert response.status_code == status


def test_marker_route_retains_body_media_and_addressed_workspace_guards(selection_client, monkeypatch):
    client, _, store = selection_client
    monkeypatch.setattr(store, "receipt_ids_for", lambda *_: pytest.fail("Rejected requests must not read storage."))
    assert client.post("/api/receipts/index", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/receipts/index", content=b"x" * (40 * 1024), headers={
        "Content-Type": "application/json",
    }).status_code == 413
    assert client.post("/budgets/demo/api/receipts/index", json=body(["active"])).status_code == 400
    assert client.post("/budgets/home/api/receipts/index", json=body(["active"], workspace="demo")).status_code == 400


def test_scoped_marker_reads_address_their_budget_and_never_checkpoint(tmp_path, monkeypatch):
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        identifiers = []
        for name in ("Household", "Travel"):
            key = client.post("/api/budget/new", json={"name": name}).json()["id"]
            prefix = f"/budgets/{key}/api"
            account = client.post(prefix + "/accounts", json={
                "workspace": "personal", "name": "Cash", "type": "depository", "balance": 100,
            }).json()["accountId"]
            assert client.post(prefix + "/transactions", json={
                "workspace": "personal", "accountId": account, "date": "2026-10-01",
                "merchant": "Synthetic shop", "amount": 10, "categoryId": "groceries",
            }).status_code == 200
            identifier = client.get(prefix + "/workspace", params={"workspace": "personal", "month": "2026-10"}).json()["transactions"][0]["id"]
            assert client.put(prefix + f"/transactions/{identifier}/receipt?workspace=personal", content=png(), headers={
                "Content-Type": "image/png",
            }).status_code == 200
            application = host.applications[key][0]
            identifiers.append((key, identifier, application.state.ledger_service.revision))
        requested = [identifier for _, identifier, _ in identifiers]
        for key, _, _ in identifiers:
            monkeypatch.setattr(host.applications[key][1], "checkpoint", lambda: pytest.fail("Marker reads must not checkpoint."))
        for key, identifier, revision in identifiers:
            response = client.post(f"/budgets/{key}/api/receipts/index", json=body(requested))
            assert response.status_code == 200 and response.json() == {
                "supported": True, "transactionIds": [identifier],
            }
            assert host.applications[key][0].state.ledger_service.revision == revision
