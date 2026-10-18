"""Register filter URLs select inclusive dates/accounts without changing ledger rows."""

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.web.test_budget_urls import BASE, settings
from whab.app import create_app
from whab.web.navigation import page_url
from whab.web.transaction_filters import transaction_period


def context(response):
    assert response.status_code == 200, response.text
    return json.loads(re.search(r'<script id="whab-context" type="application/json">(.*?)</script>', response.text, re.S)[1])


def test_periods_resolve_presets_and_legacy_months_with_validated_custom_bounds(monkeypatch):
    monkeypatch.setattr("whab.web.transaction_filters.current_month", lambda: "2026-01")
    previous = transaction_period("2026-12", "2026-12", "previous-month", None, None, False)
    assert (previous.start, previous.end, previous.month, previous.query_month) == (
        "2025-12-01", "2025-12-31", "2025-12", "2025-12",
    )
    leap = transaction_period("2024-02", "2024-02", None, None, None, False)
    assert (leap.selection, leap.start, leap.end, leap.label) == (
        "custom", "2024-02-01", "2024-02-29", "February 2024",
    )
    current = transaction_period("2026-12", "current-month", None, None, None, False)
    assert (current.selection, current.month) == ("current-month", "2026-01")
    for start, end, expected in (("2026-02-03", None, "From 2026-02-03"),
                                 (None, "2026-02-03", "Through 2026-02-03"),
                                 (None, None, "Custom dates")):
        result = transaction_period("2026-12", "2026-12", "custom", start, end, False)
        assert result.label == expected and result.query_month is None
    legacy = transaction_period("2026-12", "2026-12", None, None, None, True)
    assert legacy.selection == "all" and legacy.count_label == "All dates"
    with pytest.raises(ValueError, match="Invalid transaction period"):
        transaction_period("2026-12", "2026-12", "year", None, None, False)


def test_register_custom_range_multi_account_urls_preserve_scope_and_canonical_identity(tmp_path, store):
    identifiers = [store.add_account("personal", {"name": name, "type": "depository", "balance": 0})
                   for name in ("One", "Two", "Three")]
    euro = store.add_account("personal", {"name": "Euro", "type": "depository", "balance": 0, "currency": "EUR"})
    for account_index, account in enumerate([*identifiers, euro]):
        for date in ("2026-09-30", "2026-10-01", "2026-10-03", "2026-10-04"):
            store.add_transaction("personal", {"accountId": account, "date": date,
                "merchant": f"Purchase {account_index} {date}", "amount": 100, "categoryId": None})
    before = store.transactions("personal")
    with TestClient(create_app(store=store, settings=settings(tmp_path)), base_url=BASE) as client:
        params = [("accounts", identifier) for identifier in identifiers[:2]] + [
            ("period", "custom"), ("start_date", "2026-10-01"), ("end_date", "2026-10-03"), ("review", "reviewed"),
        ]
        response = client.get("/budgets/home/transactions/current-month/usd", params=params)
        data = context(response)
        assert data["selectedAccountIds"] == identifiers[:2] and data["selectedAccountId"] is None
        assert (data["startDate"], data["endDate"], data["transactionPeriod"], data["reviewFilter"]) == (
            "2026-10-01", "2026-10-03", "custom", "reviewed",
        )
        assert data["monthToken"] == "current-month"
        assert parse_qs(urlsplit(data["canonicalUrl"]).query)["accounts"] == identifiers[:2]
        for index in range(4):
            for date in ("2026-09-30", "2026-10-01", "2026-10-03", "2026-10-04"):
                assert (f"Purchase {index} {date}" in response.text) is (index < 2 and date in {"2026-10-01", "2026-10-03"})
        single = context(client.get("/budgets/home/transactions/current-month/usd", params={"accounts": identifiers[0]}))
        assert single["selectedAccountId"] == identifiers[0]
        assert "/one/transactions/" in single["canonicalUrl"] and "accounts=" not in single["canonicalUrl"]
        for invalid in ("unknown", euro):
            assert client.get("/budgets/home/transactions/current-month/usd", params={"accounts": invalid}).status_code == 400
        assert client.get("/budgets/my-budget/one/transactions/current-month/usd", params={"accounts": identifiers[1]}).status_code == 400
        for values in ({"start_date": "2026-02-30"}, {"start_date": "2026-10-04", "end_date": "2026-10-01"},
                       {"period": "year"}, {"review": "invalid"}):
            assert client.get("/budgets/home/transactions/current-month/usd", params={"period": "custom", **values}).status_code == 400
    assert store.transactions("personal") == before


def test_page_url_preserves_repeated_account_filters_without_coalescing_identifiers():
    query = parse_qs(urlsplit(page_url("family", "transactions", accounts=["one,two", "three"])).query)
    assert query == {"accounts": ["one,two", "three"]}
