"""Imported and connected accounts are selectable on full and retained pages."""

import re

import pytest


@pytest.mark.parametrize("source", ["ynab", "plaid", "custom-feed"])
def test_add_transaction_enabled_for_imported_accounts_and_fragments(client, store, source):
    key = store.add_account("personal", {"name": "Fictional imported account", "type": "depository", "balance": 10000})
    second = store.add_account("personal", {"name": "Second imported account", "type": "depository", "balance": 10000})
    foreign = store.add_account("personal", {"name": "Fictional EUR account", "type": "depository", "balance": 10000, "currency": "EUR"})
    store.db.execute("UPDATE accounts SET source=? WHERE id IN (?,?,?)", (source, key, second, foreign))
    for review in ("all", "needs-review"):
        text = client.get(f"/budgets/home/transactions/2026-10/usd?review={review}").text
        button = re.search(r'<button[^>]*data-add-transaction[^>]*>', text)[0]
        assert "disabled" not in button
        form = re.search(r'data-new-transaction(.*?)</tr>', text, re.S)[1]
        assert f'<option value="{key}">Fictional imported account</option>' in form
        assert foreign not in form
        assert 'form="transaction-create-form"' in form
        assert 'id="transaction-dialog"' not in text
    selected = client.get(f"/budgets/home/transactions/2026-10/usd?account={second}").text
    selected_form = re.search(r'data-new-transaction(.*?)</tr>', selected, re.S)[1]
    assert f'name="accountId" value="{second}"' in selected_form
    for page in ("transactions", "accounts", "budget", "insights"):
        fragment = client.get(f"/budgets/home/{page}/2026-10/usd", headers={"X-WHAB-Page-Fragment": "1"}).text
        options = re.search(r'<select data-page-account-options>(.*?)</select>', fragment, re.S)[1]
        assert f'value="{key}"' in options and foreign not in options


def test_empty_currency_view_keeps_a_clear_account_precondition(client):
    text = client.get("/budgets/home/transactions/2026-10/usd").text
    button = re.search(r'<button[^>]*data-add-transaction[^>]*>', text)[0]
    assert "disabled" in button and 'title="Add an account in Accounts first"' in button
