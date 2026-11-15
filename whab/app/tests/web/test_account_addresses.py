"""Account management has a budget address, with no date or currency filter."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.web.test_budget_urls import BASE, add_account, create_budget, page_context, settings
from whab.app import create_app
from whab.web.navigation import page_url


def test_account_links_ignore_period_currency_and_account_scope():
    address = page_url("fictional", "accounts", "2026-10", "EUR", account_slug="wallet", marker="Mixed CASE")
    assert urlsplit(address).path == "/budgets/fictional/accounts"
    assert parse_qs(urlsplit(address).query) == {"marker": ["Mixed CASE"]}


@pytest.mark.parametrize("suffix", ["", "/current-month/usd", "/2026-10/eur"])
def test_clean_and_legacy_accounts_show_all_currencies_without_mutation(tmp_path, store, suffix):
    for currency in ("USD", "EUR"):
        store.add_account("personal", {"name": f"Fictional {currency} wallet", "type": "depository", "balance": 10000, "currency": currency})
    before = store.export_budget("personal")
    with TestClient(create_app(settings=settings(tmp_path), store=store), base_url=BASE) as client:
        assert client.put("/budgets/home/api/ui/preferences", json={"workspace": "personal", "defaultCurrency": "GBP"}).status_code == 200
        response = client.get(f"/budgets/home/accounts{suffix}")
        context = page_context(response)
        assert context["canonicalUrl"] == "/budgets/my-budget/accounts"
        assert context["monthToken"] == "current-month" and context["currency"] == "GBP"
        assert "Fictional USD wallet" in response.text and "Fictional EUR wallet" in response.text
        assert 'href="/budgets/my-budget/accounts"' in response.text
        fragment = client.get(f"/budgets/my-budget/accounts{suffix}", headers={"X-WHAB-Page-Fragment": "1"})
        assert fragment.status_code == 200 and fragment.headers["X-WHAB-Page-Fragment"] == "1"
        fragment_context = json.loads(unescape(re.search(r'data-page-context="([^"]+)"', fragment.text)[1]))
        assert fragment_context["page"] == "accounts" and fragment_context["canonicalUrl"] == "/budgets/my-budget/accounts"
        assert store.export_budget("personal") == before


def test_clean_accounts_keep_alias_id_and_demo_budget_isolation(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        first = create_budget(client, "Fictional house")
        second = create_budget(client, "Fictional travel")
        for created, name in ((first, "House wallet"), (second, "Travel wallet")):
            assert add_account(client, created["id"], name).status_code == 200
        for key in (first["id"], "fictional-house"):
            response = client.get(f"/budgets/{key}/accounts")
            context = page_context(response)
            assert context["budgetId"] == first["id"]
            assert context["canonicalUrl"] == "/budgets/fictional-house/accounts"
            assert "House wallet" in response.text and "Travel wallet" not in response.text
        demo = client.get("/budgets/demo/accounts")
        assert page_context(demo)["workspace"] == "demo"
        assert "House wallet" not in demo.text and "Travel wallet" not in demo.text
        assert client.get("/api/budget").json()["id"] == second["id"]


@pytest.mark.parametrize("tail", ["Accounts", "accounts/current-month", "accounts/usd", "accounts/2026-99/usd", "accounts/current-month/USD", "wallet/accounts", "accounts/current-month/usd/extra"])
def test_clean_account_route_does_not_accept_invalid_scope(tmp_path, tail):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        assert client.get(f"/budgets/home/{tail}").status_code == 404


def test_clean_account_route_rejects_unknown_budget_and_routing_overrides(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        assert client.get("/budgets/missing/accounts").status_code == 404
        for query in ("currency=EUR", "page=budget", "workspace=demo", "month=2026-10"):
            assert client.get(f"/budgets/home/accounts?{query}").status_code == 400
