"""Readable name routes are aliases over isolated, stable ledger identities."""

import json
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.web.test_budget_urls import BASE, settings
from whab.app import create_app
from whab.budgets.host import BudgetHost


def context(response):
    assert response.status_code == 200, response.text
    return json.loads(re.search(r'<script id="whab-context" type="application/json">(.*?)</script>', response.text, re.S)[1])


def test_budget_and_account_names_make_readable_urls_while_ids_own_api_scope(tmp_path, store):
    app = create_app(store=store, settings=settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        account = store.add_account("personal", {"name": "💎 Счёт / Café", "type": "depository", "balance": 10000})
        other = store.add_account("personal", {"name": "Second wallet", "type": "depository", "balance": 10000})
        for identifier, merchant in ((account, "Selected fixture"), (other, "Other fixture")):
            store.add_transaction("personal", {"accountId": identifier, "date": "2026-09-05", "merchant": merchant, "amount": 100, "categoryId": None})
        old = f"/budgets/home/transactions/current-month/usd?account={account}&all_dates=true"
        data = context(client.get(old))
        canonical = "/budgets/my-budget/schyot-cafe/transactions/current-month/usd?all_dates=true"
        assert data["canonicalUrl"] == canonical
        assert data["budgetUrl"] == "/budgets/my-budget" and data["apiPrefix"] == "/budgets/home/api"
        assert data["budgetId"] == "default" and data["selectedAccountId"] == account
        assert data["accountSlugs"][account] == "schyot-cafe"
        assert set(data["budgetAliases"]) == {"home", "my-budget"}
        response = client.get(canonical)
        assert "Selected fixture" in response.text and "Other fixture" not in response.text
        assert context(response)["selectedAccountId"] == account
        assert 'href="/budgets/my-budget/accounts/current-month/usd"' in response.text
        assert "set-cookie" not in response.headers
        assert client.get("/budgets/my-budget/api/workspace?workspace=personal").status_code == 200
        assert client.get("/budgets/my-budget/api/workspace?workspace=demo").status_code == 400
        assert client.get("/budgets/demo/schyot-cafe/transactions/current-month/usd").status_code == 404
        assert client.get("/budgets/my-budget/schyot-cafe/transactions/current-month/eur").status_code == 404
        assert client.get(canonical + f"&account={other}").status_code == 400


def test_budget_collisions_and_old_name_bookmarks_survive_restart_and_rename(tmp_path):
    configuration = settings(tmp_path)
    host = BudgetHost(configuration)
    with TestClient(create_app(settings=configuration, budget_host=host), base_url=BASE) as client:
        first = client.post("/api/budget/new", json={"name": "Семейный бюджет"}).json()
        second = client.post("/api/budget/new", json={"name": "Семейный бюджет"}).json()
        assert first["url"] == "/budgets/semeynyy-byudzhet/budget/current-month/usd"
        assert second["url"] == "/budgets/semeynyy-byudzhet-2/budget/current-month/usd"
        session = host.applications[first["id"]][1]
        session.manifest["name"] = "Renamed family budget"
        session.checkpoint()
        renamed = context(client.get(first["url"]))
        assert renamed["budgetUrl"] == "/budgets/renamed-family-budget"
        assert renamed["apiPrefix"] == f'/budgets/{first["id"]}/api'
        assert renamed["budgetId"] == first["id"]
        assert context(client.get(f'/budgets/{second["id"]}/budget/current-month/usd'))["budgetId"] == second["id"]
        assert context(client.get(f'/budgets/{first["id"]}/budget/current-month/usd'))["budgetId"] == first["id"]
    with TestClient(create_app(settings=configuration), base_url=BASE) as client:
        data = context(client.get(first["url"]))
        assert data["budgetId"] == first["id"] and data["budgetUrl"] == "/budgets/renamed-family-budget"
        assert context(client.get(second["url"]))["budgetId"] == second["id"]


def test_account_alias_renames_collisions_and_registry_stay_device_local(tmp_path, store):
    app = create_app(store=store, settings=settings(tmp_path))
    first = store.add_account("personal", {"name": "Shared wallet", "type": "depository", "balance": 10000})
    second = store.add_account("personal", {"name": "Shared wallet", "type": "depository", "balance": 10000})
    with TestClient(app, base_url=BASE) as client:
        data = context(client.get("/budgets/home/transactions/2026-10/usd"))
        assert set(data["accountSlugs"].values()) == {"shared-wallet", "shared-wallet-2"}
        old_alias = data["accountSlugs"][first]
        store.db.execute("UPDATE accounts SET name='Daily cash' WHERE workspace='personal' AND id=?", (first,))
        renamed = context(client.get(f"/budgets/my-budget/{old_alias}/transactions/2026-10/usd"))
        assert renamed["selectedAccountId"] == first
        assert renamed["canonicalUrl"] == "/budgets/my-budget/daily-cash/transactions/2026-10/usd"
        fragment = client.get(f"/budgets/my-budget/{old_alias}/transactions/2026-10/usd", headers={"X-WHAB-Transactions-Fragment": "1"})
        template = re.search(r'<template data-account-list>(.*?)</template>', fragment.text, re.S)[1]
        assert "Daily cash" in template and f'data-account-id="{first}"' in template
        assert '/daily-cash/transactions/2026-10/usd?all_dates=true' in template
        assert 'title="Daily cash"' in template
        assert context(client.get(f'/budgets/my-budget/{data["accountSlugs"][second]}/transactions/2026-10/usd'))["selectedAccountId"] == second
        raw = client.get("/budgets/home/api/budget/export").content
        assert b"routes-sandbox.json" not in raw
    assert (tmp_path / "budgets" / "routes-sandbox.json").exists()


@pytest.mark.parametrize("path", [
    "/budgets/my-budget/../accounts/current-month/usd",
    "/budgets/my-budget/%2fetc/transactions/current-month/usd",
    "/budgets/my-budget/wallet/transactions/current-month/USD",
    "/budgets/my-budget/wallet/accounts/current-month/usd",
    "/budgets/my-budget/wallet/transactions/2026-99/usd",
    "/budgets/my-budget/wallet/transactions/current-month/usd/extra",
    "/budgets/my-budget/unknown/transactions/current-month/usd",
])
def test_invalid_account_paths_never_become_api_or_cross_scope_routes(tmp_path, path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        assert client.get(path).status_code == 404


def test_unnamed_budget_uses_its_opaque_id_only_as_a_fallback(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        created = client.post("/api/budget/new", json={"name": "💰"}).json()
        assert str(uuid4()) != created["id"]
        assert created["url"] == f'/budgets/{created["id"]}/budget/current-month/usd'
        assert context(client.get(created["url"]))["budgetId"] == created["id"]
