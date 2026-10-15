"""Headless host transport and ledger behavior with an isolated backend."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from whab_sqlite import Store

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


@pytest.fixture
def client():
    store = Store(":memory:", month="2026-10")
    settings = Settings(
        home=Path(":memory:"),
        port=5173,
        plaid_env="sandbox",
        plaid_client_id="",
        plaid_secret="",
        encryption_key="",
        allow_live=False,
        webhook_url="",
        redirect_uri="",
        ui="",
    )
    with TestClient(
        create_app(
            store=store,
            settings=settings,
            plugins=PluginRegistry(),
        ),
        base_url="http://127.0.0.1:5173",
    ) as client:
        yield client
    store.close()


def test_strict_money_and_calendar_validation(client):
    for invalid in (True, 1.5, "100"):
        response = client.post(
            "/api/accounts",
            json={
                "workspace": "personal",
                "name": "Wallet",
                "type": "depository",
                "balance": invalid,
            },
        )
        assert response.status_code == 400
    assert (
        client.put(
            "/api/budget/groceries", json={"workspace": "demo", "month": "2026-13", "assigned": 100}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/transactions",
            json={
                "workspace": "demo",
                "accountId": "checking",
                "date": "2026-02-30",
                "merchant": "Store",
                "amount": 100,
                "categoryId": "groceries",
            },
        ).status_code
        == 400
    )


def test_origin_host_and_chunked_body_limit(client):
    assert client.get("/api/status", headers={"host": "attacker.example"}).status_code == 403
    assert (
        client.post(
            "/api/accounts", headers={"origin": "https://attacker.example"}, json={}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/accounts", content="{}", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )

    def chunks():
        for _ in range(40):
            yield b"x" * 1024

    assert (
        client.post(
            "/api/accounts", content=chunks(), headers={"content-type": "application/json"}
        ).status_code
        == 413
    )
    assert client.get("/api/status").headers["cache-control"] == "no-store"


def test_review_and_manual_money_flow(client):
    account_id = client.post(
        "/api/accounts",
        json={"workspace": "personal", "name": "Wallet", "type": "depository", "balance": 10000},
    ).json()["accountId"]
    month = client.get("/api/workspace?workspace=personal").json()["budgetStartMonth"]
    assert (
        client.put(
            "/api/budget/groceries",
            json={"workspace": "personal", "month": month, "assigned": 10000},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/transactions",
            json={
                "workspace": "personal",
                "accountId": account_id,
                "date": month + "-01",
                "merchant": "Grocer",
                "amount": 2000,
                "categoryId": "groceries",
            },
        ).status_code
        == 200
    )
    data = client.get("/api/workspace", params={"workspace": "personal", "month": month}).json()
    assert data["summary"]["readyToAssign"] == 0
    assert data["summary"]["available"] == 8000
    transaction = data["transactions"][0]
    assert (
        client.patch(
            "/api/transactions/" + transaction["id"],
            json={"workspace": "personal", "reviewed": True},
        ).status_code
        == 200
    )
    saved = client.get("/api/workspace", params={"workspace": "personal", "month": month}).json()[
        "transactions"
    ][0]
    assert saved["reviewed"] is True
    assert saved["categoryId"] == "groceries"
    assert (
        client.post(
            "/api/transactions",
            json={
                "workspace": "personal",
                "accountId": account_id,
                "date": month + "-02",
                "merchant": "Grocer refund",
                "amount": -1000,
                "categoryId": "groceries",
            },
        ).status_code
        == 200
    )
    summary = client.get("/api/workspace", params={"workspace": "personal", "month": month}).json()[
        "summary"
    ]
    assert summary["available"] == 9000
    assert summary["readyToAssign"] == 0


def test_csv_formula_safety_and_no_credentials(client):
    name = ' \t=HYPERLINK("https://example.com")'
    account_id = client.post(
        "/api/accounts",
        json={"workspace": "personal", "name": "Wallet", "type": "depository", "balance": 10000},
    ).json()["accountId"]
    month = client.get("/api/workspace?workspace=personal").json()["budgetStartMonth"]
    client.post(
        "/api/transactions",
        json={
            "workspace": "personal",
            "accountId": account_id,
            "date": month + "-01",
            "merchant": name,
            "amount": 100,
            "categoryId": None,
            "note": "\t@SUM(A1)",
        },
    )
    text = client.get("/api/export?workspace=personal").text
    assert "'=HYPERLINK" in text
    assert "'\t@SUM" in text
    assert "PLAID_SECRET" not in text
    assert "access_token" not in client.get("/api/status").text



def test_multiple_currency_http_views_reject_mixing_and_identify_exports(client):
    import csv
    import io

    ids = {}
    for code, balance in (("USD", 50000), ("EUR", 30000)):
        response = client.post("/api/accounts", json={
            "workspace": "personal", "name": f"{code} cash", "type": "depository",
            "balance": balance, "currency": code,
        })
        assert response.status_code == 200
        ids[code] = response.json()["accountId"]
    assert client.post("/api/categories", json={
        "workspace": "personal", "name": "European food", "group": "Everyday",
        "target": 1000, "currency": "EUR",
    }).status_code == 200
    all_data = client.get("/api/workspace?workspace=personal&month=2026-10").json()
    food = next(c for c in all_data["categories"] if c["currency"] == "EUR")
    tx = {"workspace": "personal", "accountId": ids["EUR"], "date": "2026-10-03",
          "merchant": "Fixture market", "amount": 500, "categoryId": food["id"]}
    assert client.post("/api/transactions", json=tx).status_code == 200
    before = client.get("/api/workspace?workspace=personal&month=2026-10").json()
    for category in ("groceries", food["parentId"]):
        assert client.post("/api/transactions", json={**tx, "categoryId": category}).status_code == 400
    assert client.get("/api/workspace?workspace=personal&month=2026-10").json() == before
    eur = client.get("/api/workspace?workspace=personal&month=2026-10&currency=EUR").json()
    assert eur["currency"] == "EUR" and eur["summary"]["spent"] == 500
    assert {a["currency"] for a in eur["accounts"]} == {"EUR"}
    assert len(eur["transactions"]) == 1 and eur["categoryGroups"][0]["children"] == [food]
    assert before["summary"]["spent"] == 0
    assert before["summariesByCurrency"]["EUR"]["readyToAssign"] == 29500
    assert before["summariesByCurrency"]["USD"]["readyToAssign"] == 50000
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export?workspace=personal").text)))
    assert rows[0]["currency"] == "EUR" and rows[0]["amount_cents"] == "500"
    assert list(csv.DictReader(io.StringIO(client.get(
        "/api/export?workspace=personal&currency=USD").text))) == []
    for url in ("/api/workspace?workspace=personal&currency=XXX", "/api/export?workspace=personal&currency=XXX"):
        assert client.get(url).status_code == 400


def test_category_parent_and_fixed_date_http_contract(client):
    data = client.get("/api/workspace?workspace=demo&month=2026-10").json()
    parent = data["categories"][0]["parentId"]
    body = {"workspace": "demo", "name": "New child", "parentId": parent, "target": 0}
    assert client.post("/api/categories", json=body).status_code == 200
    assert client.post("/api/categories", json={**body, "parentId": "groceries"}).status_code == 400
    assert client.post("/api/categories", json={**body, "children": []}).status_code == 400
    assert client.put(f"/api/budget/{parent}", json={
        "workspace": "demo", "month": "2026-10", "assigned": 100,
    }).status_code == 400
    tx = {"workspace": "demo", "accountId": "checking", "date": "2026-10-03",
          "merchant": "Fixture market", "amount": 500, "categoryId": "groceries"}
    for date_value in ("10/03/2026", "20261003", "2026-10-3", "2026-02-30"):
        assert client.post("/api/transactions", json={**tx, "date": date_value}).status_code == 400
    assert client.post("/api/transactions", json=tx).status_code == 200
