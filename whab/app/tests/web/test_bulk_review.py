"""Local bulk review keeps strict input, addressed budgets and browser guards."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings

BASE = "https://127.0.0.1:5173"


def settings(home):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui="web")


@pytest.fixture
def review_client(tmp_path, store):
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 1000})
    identifiers = [store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": f"Shop {index}",
        "amount": 100, "categoryId": "groceries", "note": f"Memo {index}",
    }) for index in range(2)]
    app = create_app(store=store, settings=settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        yield client, app, store, identifiers


def payload(identifiers, **changes):
    return {"workspace": "personal", "requestId": "request", "transactionIds": identifiers,
            "reviewed": True, **changes}


def test_local_bulk_route_replays_without_overwriting_later_review(review_client):
    client, app, store, identifiers = review_client
    request = payload(identifiers)
    assert client.put("/api/transactions/review", json=request).json() == {"ok": True}
    assert client.patch("/api/transactions/" + identifiers[0], json={
        "workspace": "personal", "reviewed": False, "note": "Later memo",
    }).status_code == 200
    revision = app.state.ledger_service.revision
    replay = client.put("/api/transactions/review", json=request)
    assert replay.status_code == 200 and replay.json() == {"ok": True}
    rows = {row["id"]: row for row in store.transactions("personal")}
    assert rows[identifiers[0]]["reviewed"] is False
    assert rows[identifiers[0]]["note"] == "Later memo" and rows[identifiers[1]]["reviewed"] is True
    assert app.state.ledger_service.revision == revision
    conflict = client.put("/api/transactions/review", json=payload(identifiers, reviewed=False))
    assert conflict.status_code == 400 and "Request ID" in conflict.json()["error"]


@pytest.mark.parametrize("changes", [
    {"reviewed": 1}, {"reviewed": "true"}, {"reviewed": None},
    {"transactionIds": []}, {"transactionIds": ["one"] * 101},
    {"transactionIds": [1]}, {"transactionIds": "one"}, {"transactionIds": ["bad\x00id"]},
    {"transactionIds": ["one", "one"]}, {"transactionIds": ["x" * 201]},
    {"requestId": []}, {"requestId": ""}, {"requestId": "bad\x00id"},
    {"note": "unexpected"}, {"workspace": "other"},
])
def test_bulk_route_rejects_invalid_body_without_changes(review_client, changes):
    client, app, store, identifiers = review_client
    before, revision = store.export_budget(), app.state.ledger_service.revision
    response = client.put("/api/transactions/review", json=payload(identifiers, **changes))
    assert response.status_code == 400
    assert store.export_budget() == before and app.state.ledger_service.revision == revision


@pytest.mark.parametrize("field", ["requestId", "transactionIds", "reviewed", "workspace"])
def test_bulk_route_requires_all_fields(review_client, field):
    client, _, store, identifiers = review_client
    body = payload(identifiers)
    del body[field]
    assert client.put("/api/transactions/review", json=body).status_code == 400
    assert not any(row["reviewed"] for row in store.transactions("personal"))


def test_bulk_route_rejects_legacy_backend_and_page_advertises_actual_capability(review_client, monkeypatch):
    client, app, store, identifiers = review_client

    def context():
        response = client.get("/budgets/home/transactions/2026-10/usd")
        assert response.status_code == 200
        return json.loads(re.search(
            r'<script id="whab-context" type="application/json">(.*?)</script>', response.text,
        )[1])

    assert context()["bulkReview"] is True
    monkeypatch.setattr(store, "review_transactions", None)
    assert context()["bulkReview"] is False
    revision = app.state.ledger_service.revision
    response = client.put("/api/transactions/review", json=payload(identifiers))
    assert response.status_code == 400 and "does not support bulk review" in response.json()["error"]
    assert app.state.ledger_service.revision == revision
    assert not any(row["reviewed"] for row in store.transactions("personal"))


@pytest.mark.parametrize("url,headers,status", [
    ("http://127.0.0.1:5173", {"X-Forwarded-Proto": "https"}, 400),
    (BASE, {"Host": "attacker.invalid"}, 403),
    (BASE, {"Origin": "https://attacker.invalid"}, 403),
    (BASE, {"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_bulk_route_retains_tls_host_and_origin_guards(review_client, url, headers, status):
    client, _, store, identifiers = review_client
    response = client.put(url + "/budgets/home/api/transactions/review",
                          json=payload(identifiers), headers=headers)
    assert response.status_code == status
    assert not any(row["reviewed"] for row in store.transactions("personal"))


def test_bulk_route_retains_media_and_body_size_limits(review_client):
    client, _, store, identifiers = review_client
    address = "/api/transactions/review"
    assert client.put(address, content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.put(address, content=b"x" * (40 * 1024), headers={
        "Content-Type": "application/json",
    }).status_code == 413
    assert not any(row["reviewed"] for row in store.transactions("personal"))


def test_bulk_address_owns_budget_independently_of_cookie_and_rejects_dataset_conflict(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        first = client.post("/api/budget/new", json={"name": "Household"}).json()
        second = client.post("/api/budget/new", json={"name": "Travel"}).json()

        def seed(key):
            prefix = f"/budgets/{key}/api"
            account = client.post(prefix + "/accounts", json={
                "workspace": "personal", "name": "Cash", "type": "depository", "balance": 1000,
            }).json()["accountId"]
            assert client.post(prefix + "/transactions", json={
                "workspace": "personal", "accountId": account, "date": "2026-10-01",
                "merchant": "Shop", "amount": 100, "categoryId": "groceries",
            }).status_code == 200
            return client.get(prefix + "/workspace", params={
                "workspace": "personal", "month": "2026-10",
            }).json()["transactions"][0]["id"]

        first_id, second_id = seed(first["id"]), seed(second["id"])
        address = f'/budgets/{first["id"]}/api/transactions/review'
        body = payload([first_id])
        assert client.put(address, json=payload([first_id], workspace="demo")).status_code == 400
        assert client.put(address, json=payload([second_id])).status_code == 400
        assert client.put(address, json=body).status_code == 200
        assert client.put(f'/budgets/{second["id"]}/api/transactions/review', json=payload([second_id])).status_code == 200
        for budget in (first, second):
            rows = client.get(f'/budgets/{budget["id"]}/api/workspace', params={
                "workspace": "personal", "month": "2026-10",
            }).json()["transactions"]
            assert len(rows) == 1 and rows[0]["reviewed"] is True
