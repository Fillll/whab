"""Headless host transport and ledger behavior with an isolated backend."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from whab_plugin_sqlite import Store

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
        base_url="https://127.0.0.1:5173",
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


@pytest.mark.parametrize("path", ["/", "/api/workspace?workspace=personal", "/api/plaid/webhook", "/remote/v1/budgets/default/queries/accounts?workspace=personal"])
def test_http_is_rejected_even_with_forged_forwarded_headers(client, path):
    response = client.get("http://127.0.0.1:5173" + path, headers={
        "X-Forwarded-Proto": "https", "Forwarded": "proto=https;host=localhost",
    })
    assert response.status_code == 400 and response.json() == {"error": "HTTPS is required"}
    assert response.headers["cache-control"] == "no-store"


def test_local_browser_origins_require_https(client):
    account = {"workspace": "personal", "name": "Cash", "type": "depository", "balance": 100}
    assert client.post("/api/accounts", json=account, headers={"Origin": "http://127.0.0.1:5173"}).status_code == 403
    assert client.post("/api/accounts", json=account, headers={"Origin": "https://127.0.0.1:5173"}).status_code == 200


def test_account_scope_roundtrip_and_classification_history(client):
    account_id = client.post("/api/accounts", json={
        "workspace": "personal", "name": "IRA", "type": "retirement", "subtype": "roth_ira",
        "balance": 100000,
    }).json()["accountId"]
    data = client.get("/api/workspace?workspace=personal&month=2026-10").json()
    record = next(row for row in data["accounts"] if row["id"] == account_id)
    assert record["onBudget"] is False
    assert data["summary"]["readyToAssign"] == 0
    assert data["accountSummary"]["netWorth"] == 100000
    assert client.patch("/api/accounts/" + account_id, json={
        "workspace": "personal", "type": "depository", "subtype": "savings", "onBudget": True,
    }).status_code == 200
    data = client.get("/api/workspace?workspace=personal&month=2026-10").json()
    assert data["summary"]["readyToAssign"] == 100000
    assert data["accountSummary"]["netWorth"] == 100000
    for payload in (
        {}, {"onBudget": None}, {"onBudget": "false"}, {"type": "property", "onBudget": True},
        {"type": "loan", "subtype": "roth_ira"}, {"balance": 0}, {"type": None},
    ):
        assert client.patch("/api/accounts/" + account_id, json={"workspace": "personal", **payload}).status_code == 400
    assert client.patch("/api/accounts/missing", json={"workspace": "personal", "onBudget": False}).status_code == 400
    assert client.post("/api/accounts", json={
        "workspace": "personal", "name": "Bad", "type": "loan", "balance": 0, "onBudget": True,
    }).status_code == 400
    assert client.post("/api/accounts", json={
        "workspace": "personal", "name": "Bad", "type": "depository", "balance": 0, "onBudget": None,
    }).status_code == 400


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
    assert before["summariesByCurrency"]["EUR"]["readyToAssign"] == 30000
    assert before["summariesByCurrency"]["EUR"]["fundingCapacity"] == 29500
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


def test_next_month_funding_saves_without_editing_child_assignments(client):
    assert client.post('/api/accounts', json={
        'workspace': 'personal', 'name': 'Cash', 'type': 'depository', 'balance': 100000,
    }).status_code == 200
    request = {'workspace': 'personal', 'month': '2026-10', 'currency': 'USD', 'assigned': 30000}
    assert client.put('/api/budget/funding/next-month', json=request).status_code == 200
    october = client.get('/api/workspace?workspace=personal&month=2026-10').json()
    assert october['summary']['assignedNextMonth'] == 30000
    assert october['summary']['readyToAssign'] == 70000
    assert all(row['assigned'] == 0 for row in october['budget'])
    assert client.put('/api/budget/groceries', json={
        'workspace': 'personal', 'month': '2026-11', 'assigned': 20000,
    }).status_code == 200
    assert client.get('/api/workspace?workspace=personal&month=2026-10').json()['summary'] == october['summary']
    november = client.get('/api/workspace?workspace=personal&month=2026-11').json()
    assert november['summary']['readyToAssign'] == 80000
    assert client.put('/api/budget/funding/next-month', json={**request, 'assigned': 19999}).status_code == 200
    reduced = client.get('/api/workspace?workspace=personal&month=2026-11').json()
    assert reduced['summary']['carriedIn'] == 100000
    assert reduced['summary']['carriedInSetAside'] == 19999
    assert reduced['summary']['carriedInUnassigned'] == 80001
    assert reduced['summary']['assigned'] == 20000
    assert reduced['summary']['readyToAssign'] == 80000
    assert client.put('/api/budget/funding/next-month', json={**request, 'assigned': 100001}).status_code == 400
    assert client.put('/api/budget/funding/next-month', json={**request, 'assigned': 20000}).status_code == 200
    assert client.get('/api/workspace?workspace=personal&month=2026-10').json()['summary']['readyToAssign'] == 80000
    assert client.put('/api/budget/funding/next-month', json={**request, 'assigned': 0}).status_code == 200
    for month, expected_ready in (('2026-10', 100000), ('2026-11', 80000)):
        data = client.get(f'/api/workspace?workspace=personal&month={month}').json()
        summary = data['summary']
        assert data['monthlyFundingFlow'] is True
        assert summary['readyToAssign'] == expected_ready == (
            summary['income'] + summary['carriedIn']
            - summary['assignedNextMonth'] - summary['assigned']
        )
        assert summary['carriedIn'] == (
            summary['carriedInUnassigned'] + summary['carriedInSetAside']
            + summary['balanceCarryover']
        )
    assert client.get('/api/workspace?workspace=demo&month=2026-10').json()['summary']['assignedNextMonth'] == 0


@pytest.mark.parametrize('change', [
    {'assigned': True}, {'assigned': 1.5}, {'assigned': '100'}, {'assigned': -1},
    {'assigned': 1000000000001}, {'currency': 'xyz'}, {'currency': None},
    {'month': '2026-13'}, {'month': '2200-12'}, {'month': '2026-09'},
    {'workspace': 'unknown'}, {'categoryId': 'groceries'},
])
def test_next_month_funding_rejects_invalid_requests(client, change):
    request = {'workspace': 'personal', 'month': '2026-10', 'currency': 'USD', 'assigned': 0}
    before = client.get('/api/workspace?workspace=personal&month=2026-10').json()
    assert client.put('/api/budget/funding/next-month', json={**request, **change}).status_code == 400
    assert client.get('/api/workspace?workspace=personal&month=2026-10').json() == before


def test_next_month_funding_preserves_local_origin_guard(client):
    response = client.put('/api/budget/funding/next-month', json={
        'workspace': 'personal', 'month': '2026-10', 'currency': 'USD', 'assigned': 0,
    }, headers={'Origin': 'https://foreign.example'})
    assert response.status_code == 403


def test_next_month_funding_route_preserves_imported_category_named_next_month(store, tmp_path):
    records = store.export_budget()
    records['categories'][0]['id'] = 'next-month'
    store.restore_budget('personal', records)
    store.add_account('personal', {'name': 'Cash', 'type': 'depository', 'balance': 10000})
    settings = Settings(tmp_path, 5173, 'sandbox', '', '', '', False, '', '', (), ui='')
    with TestClient(create_app(store=store, settings=settings, plugins=PluginRegistry()),
                    base_url='https://127.0.0.1:5173') as browser:
        assert browser.put('/api/budget/next-month', json={
            'workspace': 'personal', 'month': '2026-10', 'assigned': 100,
        }).status_code == 200
        assert browser.put('/api/budget/funding/next-month', json={
            'workspace': 'personal', 'month': '2026-10', 'currency': 'USD', 'assigned': 3000,
        }).status_code == 200
        data = browser.get('/api/workspace?workspace=personal&month=2026-10').json()
        assert next(row for row in data['budget'] if row['categoryId'] == 'next-month')['assigned'] == 100
        assert data['summary']['assignedNextMonth'] == 3000


def test_historical_demo_months_exclude_plans_funded_by_later_income(client, monkeypatch):
    monkeypatch.setattr('whab_plugin_sqlite.store.current_month', lambda: '2026-10')
    may = client.get('/api/workspace?workspace=demo&month=2026-05').json()
    june = client.get('/api/workspace?workspace=demo&month=2026-06').json()
    assert may['summary']['readyToAssign'] == 1210000
    assert june['summary']['readyToAssign'] == 1480000
    assert client.get('/api/workspace?workspace=demo&month=2026-10').json()['summary']['readyToAssign'] == 2560000


def test_monthly_rollover_api_pools_balances_once_and_preserves_source_month(client):
    october = client.get('/api/workspace?workspace=demo&month=2026-10').json()
    assert october['rolloverMode'] == 'category'
    assert client.put('/api/budget/settings/rollover', json={
        'workspace': 'demo', 'month': '2026-10', 'currency': 'USD', 'mode': 'balance',
    }).status_code == 200
    november = client.get('/api/workspace?workspace=demo&month=2026-11').json()
    assert november['summary']['balanceCarryover'] == 899817
    assert november['summary']['readyToAssign'] == 3459817
    assert all(row['carryover'] == 0 and row['available'] == 0 for row in november['budget'])
    assert november['accounts'] == october['accounts']
    after = client.get('/api/workspace?workspace=demo&month=2026-10').json()
    assert after['rolloverMode'] == 'balance' and after['summary'] == october['summary']
    assert client.get('/api/workspace?workspace=personal&month=2026-10').json()['rolloverMode'] == 'category'
    assert client.put('/api/budget/settings/rollover', json={
        'workspace': 'demo', 'month': '2026-10', 'currency': 'USD', 'mode': 'category',
    }).status_code == 200
    restored = client.get('/api/workspace?workspace=demo&month=2026-11').json()
    assert restored['summary']['balanceCarryover'] == 0
    assert restored['summary']['available'] == 899817


@pytest.mark.parametrize('change', [
    {'mode': 'reset'}, {'mode': True}, {'mode': None}, {'mode': 1}, {'mode': []},
    {'currency': 'xyz'}, {'currency': None}, {'month': '2026-13'},
    {'month': '2200-12'}, {'month': '2026-09'}, {'workspace': 'unknown'}, {'assigned': 100},
])
def test_monthly_rollover_rejects_invalid_requests_without_mutation(client, change):
    request = {'workspace': 'personal', 'month': '2026-10', 'currency': 'USD', 'mode': 'balance'}
    before = client.get('/api/workspace?workspace=personal&month=2026-11').json()
    assert client.put('/api/budget/settings/rollover', json={**request, **change}).status_code == 400
    assert client.get('/api/workspace?workspace=personal&month=2026-11').json() == before


def test_monthly_rollover_preserves_local_origin_guard(client):
    assert client.put('/api/budget/settings/rollover', json={
        'workspace': 'personal', 'month': '2026-10', 'currency': 'USD', 'mode': 'balance',
    }, headers={'Origin': 'https://foreign.example'}).status_code == 403
