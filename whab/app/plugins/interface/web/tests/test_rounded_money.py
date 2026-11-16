"""Web rounds presentation only, preserving exact currency inputs and public data."""

import re

import pytest
from whab_plugin_web.views import display_money, money_input, money_number

from whab.plugins import format_money


@pytest.mark.parametrize("value,currency,number,label,editor", [
    (49, "USD", "0", "$0", "0.49"),
    (-49, "USD", "0", "$0", "-0.49"),
    (50, "USD", "1", "$1", "0.50"),
    (-50, "USD", "-1", "-$1", "-0.50"),
    (123450, "USD", "1,235", "$1,235", "1234.50"),
    (-123450, "USD", "-1,235", "-$1,235", "-1234.50"),
    (1234, "JPY", "1,234", "JPY 1,234", "1234"),
    (123499, "KWD", "123", "KWD 123", "123.499"),
    (-123500, "KWD", "-124", "-KWD 124", "-123.500"),
    (12345000, "CLF", "1,235", "CLF 1,235", "1234.5000"),
    (9007199254740991, "USD", "90,071,992,547,410", "$90,071,992,547,410", "90071992547409.91"),
])
def test_whole_unit_display_rounding_retains_exact_editor(value, currency, number, label, editor):
    assert money_number(value, currency) == number
    assert display_money(value, currency) == label
    assert money_input(value, currency) == editor


def test_core_money_formatting_keeps_public_precision():
    assert format_money(123456, "USD") == "$1,234.56"
    assert format_money(123456, "KWD") == "KWD 123.456"


@pytest.mark.parametrize("source", ["bank", "ynab"])
def test_unknown_account_balances_are_privacy_targets_without_inventing_values(client, store, source):
    account = store.add_account("personal", {
        "name": "Fictional unavailable balance", "type": "credit", "balance": 12345,
        "currency": "USD",
    })
    def mark_unknown(state):
        state["accounts"][account].update(source=source, balanceUnknown=True, stale=True)
    store._mutate("personal", mark_unknown)
    before = store.export_budget("personal")
    text = client.get("/budgets/home/accounts").text
    row = re.search(rf'<form[^>]+data-inline-account="{account}"[^>]*>(.*?)</form>', text, re.S)[1]
    expected = "$123" if source == "ynab" else "Unavailable"
    assert f'<span data-private-amount data-private-currency="USD">{expected}</span>' in row
    if source == "ynab":
        assert 'Estimated from imported transactions · unverified' in row
    sidebar = re.search(rf'<a class="sidebar-account"[^>]*data-account-id="{account}"[^>]*>(.*?)</a>', text, re.S)[1]
    assert 'data-private-amount-attributes="aria-label title"' in sidebar
    assert '<span data-private-amount>' in sidebar
    assert ('>—</span>' in sidebar) is (source == "bank")
    assert store.export_budget("personal") == before
