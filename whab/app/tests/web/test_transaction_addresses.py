"""USD register links are short; legacy/non-USD links retain their exact scope."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.web.test_budget_urls import BASE, settings
from whab.app import create_app
from whab.web.navigation import page_url


@pytest.fixture
def client(tmp_path, store):
    with TestClient(create_app(settings=settings(tmp_path), store=store), base_url=BASE) as browser:
        yield browser


def page_context(response):
    assert response.status_code == 200, response.text
    return json.loads(re.search(r'<script id="whab-context" type="application/json">(.*?)</script>', response.text, re.S)[1])


@pytest.mark.parametrize("suffix", ["", "/usd"])
def test_short_and_legacy_registers_keep_currency_account_and_filter_scope(client, store, suffix):
    identifiers = {}
    for code in ("USD", "EUR"):
        identifier = store.add_account("personal", {"name": f"Fictional {code} wallet", "type": "depository", "balance": 10000, "currency": code})
        identifiers[code] = identifier
        store.add_transaction("personal", {"accountId": identifier, "date": "2026-11-01", "merchant": f"Synthetic {code} merchant", "amount": 1234, "categoryId": None})
    before = store.export_budget("personal")
    address = f"/budgets/home/transactions/2026-11{suffix}?period=all&sort=merchant:asc&review=needs-review&q=Synthetic"
    response = client.get(address)
    data = page_context(response)
    assert data["currency"] == "USD"
    assert data["canonicalUrl"].startswith("/budgets/my-budget/transactions/2026-11?")
    assert re.findall('data-merchant="([^"]+)"', response.text) == ["Synthetic USD merchant"]
    for href in re.findall(r'<a[^>]*href="([^"]+)"', unescape(response.text)):
        if "/transactions/" in href:
            assert not urlsplit(href).path.endswith("/usd")
    account_address = page_url("my-budget", "transactions", "2026-11", account_slug=data["accountSlugs"][identifiers["USD"]], period="all")
    account = page_context(client.get(account_address))
    assert account["selectedAccountIds"] == [identifiers["USD"]]
    assert client.get(account_address.replace("?", "/usd?", 1)).status_code == 200
    eur = client.get("/budgets/my-budget/transactions/2026-11/eur?period=all")
    assert page_context(eur)["currency"] == "EUR"
    assert re.findall('data-merchant="([^"]+)"', eur.text) == ["Synthetic EUR merchant"]
    assert client.get(address + f'&accounts={identifiers["EUR"]}').status_code == 400
    assert store.export_budget("personal") == before


def test_short_register_fragments_and_pagination_preserve_selection(client, store):
    identifier = store.add_account("personal", {"name": "Fictional checking", "type": "depository", "balance": 10000})
    for index in range(101):
        store.add_transaction("personal", {"accountId": identifier, "date": "2026-11-01", "merchant": f"Synthetic item {index:03}", "amount": 100, "categoryId": None})
    address = "/budgets/home/transactions/2026-11?period=all&sort=merchant:asc&review=needs-review"
    first = client.get(address, headers={"X-WHAB-Transactions-Fragment": "1"})
    assert first.status_code == 200 and first.headers["Cache-Control"] == "no-store"
    next_url = unescape(re.search('data-stream-next="([^"]+)"', first.text)[1])
    assert urlsplit(next_url).path == "/budgets/home/transactions/2026-11"
    assert parse_qs(urlsplit(next_url).query)["sort"] == ["merchant:asc"]
    second = client.get(next_url, headers={"X-WHAB-Transactions-Fragment": "1"})
    assert re.findall('data-merchant="([^"]+)"', second.text) == ["Synthetic item 100"]


def test_short_register_addresses_are_budget_scoped_even_after_other_budget_selection(tmp_path):
    with TestClient(create_app(settings=settings(tmp_path)), base_url=BASE) as client:
        first = client.post("/api/budget/new", json={"name": "Fictional house"}).json()
        second = client.post("/api/budget/new", json={"name": "Fictional travel"}).json()
        for created in (first, second):
            route = f'/budgets/{created["id"]}/transactions/current-month'
            data = page_context(client.get(route))
            assert data["budgetId"] == created["id"] and data["currency"] == "USD"
            assert data["apiPrefix"] == f'/budgets/{created["id"]}/api'


@pytest.mark.parametrize("tail", ["transactions/2026-99", "transactions/Current-Month", "transactions/current-month/USD", "wallet/transactions/2026-99", "wallet/accounts/current-month"])
def test_short_routes_do_not_broaden_invalid_or_cross_scope_addresses(client, tail):
    assert client.get(f"/budgets/home/{tail}").status_code == 404


def test_short_routes_reject_routing_query_overrides(client):
    for query in ("currency=EUR", "page=budget", "workspace=demo", "month=2026-10"):
        assert client.get(f"/budgets/home/transactions/current-month?{query}").status_code == 400
