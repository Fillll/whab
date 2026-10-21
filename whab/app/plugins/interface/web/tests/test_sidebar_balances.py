"""Sidebar balances retain snapshot provenance and signed imported estimates."""

import re

import pytest


def sidebar_rows(text):
    return dict(re.findall(
        r'<a class="sidebar-account"[^>]*data-account-id="([^"]+)"[^>]*>(.*?)</a>',
        text, re.S,
    ))


@pytest.mark.parametrize('headers', ({}, {"X-WHAB-Page-Fragment": "1"}, {
    "X-WHAB-Transactions-Fragment": "1",
}))
def test_sidebar_balances_include_import_estimates_without_inventing_unknown_snapshots(
    client, store, headers,
):
    accounts = (
        ("cash", "depository", 123456, "manual", False, False),
        ("debt", "credit", 56789, "manual", False, False),
        ("zero", "depository", 0, "manual", False, False),
        ("old", "investment", 90000, "bank", True, False),
        ("unknown", "depository", 0, "bank", True, True),
        ("import-cash", "other", 78901, "ynab", True, True),
        ("import-debt", "credit", -23456, "ynab", True, True),
        ("verified-import", "credit", 40000, "ynab", False, False),
    )
    def seed(state):
        for identifier, kind, balance, source, stale, unknown in accounts:
            state["accounts"][identifier] = {
                "id": identifier, "name": identifier, "institution": "Synthetic",
                "type": kind, "balance": balance, "currency": "USD", "source": source,
                "stale": stale, "balanceUnknown": unknown,
            }
    store._mutate("personal", seed)
    for month in ("2026-09", "2026-10"):
        response = client.get(f"/budgets/home/transactions/{month}/usd", headers=headers)
        assert response.status_code == 200
        rows = sidebar_rows(response.text)
        for identifier, amount, label in (
            ("cash", "$1,234.56", "Current balance"),
            ("debt", "-$567.89", "Current balance"),
            ("zero", "$0.00", "Current balance"),
            ("old", "≈ $900.00", "Last known balance"),
            ("unknown", "—", "Balance unavailable"),
            ("import-cash", "≈ $789.01", "Estimated from imported transactions"),
            ("import-debt", "≈ -$234.56", "Estimated from imported transactions"),
            ("verified-import", "-$400.00", "Current balance"),
        ):
            assert f'>{amount}</small>' in rows[identifier]
            assert f'title="{label}"' in rows[identifier]
            assert f'aria-label="{label}' in rows[identifier]
    data = store.workspace("personal", "2026-10")
    assert data["accountSummary"]["netWorth"] == 26667


@pytest.mark.parametrize('currency,balance,formatted', (
    ("JPY", 123456, "JPY 123,456"), ("KWD", 123456, "KWD 123.456"),
))
def test_sidebar_balances_use_selected_currency_precision(client, store, currency, balance, formatted):
    account = store.add_account("personal", {
        "name": "Currency wallet", "type": "depository", "balance": balance,
        "currency": currency,
    })
    rows = sidebar_rows(client.get(f"/budgets/home/budget/2026-10/{currency.lower()}").text)
    assert f'>{formatted}</small>' in rows[account]
