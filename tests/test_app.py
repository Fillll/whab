"""HTTP behavior and Jinja escaping, with an isolated in-memory ledger."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whab import __version__
from whab.app import create_app
from whab.config import Settings
from whab.plaid_service import PlaidService
from whab.store import Store


@pytest.fixture
def client():
    store = Store(":memory:", month="2026-10")
    settings = Settings(
        database_path=Path(":memory:"),
        port=5173,
        plaid_env="sandbox",
        plaid_client_id="",
        plaid_secret="",
        encryption_key="",
        allow_live=False,
        webhook_url="",
        redirect_uri="",
    )
    plaid = PlaidService(store, settings.plaid_environment())
    with TestClient(
        create_app(store=store, plaid=plaid, settings=settings), base_url="http://127.0.0.1:5173"
    ) as client:
        yield client
    store.close()


def test_all_pages_render_and_escape_user_content(client):
    for page in ("budget", "transactions", "insights", "accounts"):
        response = client.get("/", params={"page": page, "workspace": "demo", "month": "2026-10"})
        assert response.status_code == 200
        assert "Sample data" in response.text
        assert "/static/app.js" in response.text
        assert "WHAB · We Have A Budget" in response.text
        assert f"WHAB · Python + SQLite · {__version__}" in response.text
        assert 'id="whab-context"' in response.text
    assert client.app.version == __version__
    name = '<script>alert("x")</script>'
    assert (
        client.post(
            "/api/accounts",
            json={"workspace": "personal", "name": name, "type": "depository", "balance": 10000},
        ).status_code
        == 200
    )
    text = client.get("/?page=accounts&workspace=personal").text
    assert name not in text
    assert "&lt;script&gt;" in text
    assert "Sandbox mode" in text


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
            "/api/plaid/link-token", content="{}", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )

    def chunks():
        for _ in range(40):
            yield b"x" * 1024

    assert (
        client.post(
            "/api/plaid/link-token", content=chunks(), headers={"content-type": "application/json"}
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


def test_missing_plaid_config_is_actionable_and_bad_webhook_is_rejected(client):
    response = client.post("/api/plaid/link-token", json={})
    assert response.status_code == 400
    assert "config" in response.json()["error"].lower()
    response = client.post(
        "/api/plaid/webhook", json={"webhook_type": "TRANSACTIONS", "item_id": "fake"}
    )
    assert response.status_code == 401
