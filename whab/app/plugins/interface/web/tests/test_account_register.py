"""Account links select a scoped, all-date register rather than account management."""

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit


def register_rows(text):
    return re.findall(r'<tr class="transaction-row"(.*?)</tr>', text, re.S)


def test_sidebar_accounts_open_all_dates_and_keep_account_management_separate(client, store):
    first = store.add_account("personal", {
        "name": "First wallet", "type": "depository", "balance": 10000,
    })
    second = store.add_account("personal", {
        "name": "Second wallet", "type": "investment", "balance": 20000, "onBudget": False,
    })
    for identifier, month, merchant in (
        (first, "2026-09", "Earlier first purchase"),
        (first, "2026-10", "Current first purchase"),
        (second, "2026-09", "Other account purchase"),
    ):
        store.add_transaction("personal", {
            "accountId": identifier, "date": month + "-05", "merchant": merchant,
            "amount": 500, "categoryId": None,
        })
    management = client.get("/budgets/home/accounts/2026-10/usd").text
    account_links = re.findall(r'<a class="sidebar-account" href="([^"]+)"', management)
    assert len(account_links) == 2
    address = urlsplit(unescape(account_links[0]))
    assert address.path == "/budgets/home/transactions/2026-10/usd"
    assert parse_qs(address.query) == {"account": [first], "all_dates": ["true"]}
    assert 'href="/budgets/home/accounts/2026-10/usd"' in management
    register = client.get(unescape(account_links[0])).text
    rows = register_rows(register)
    assert len(rows) == 2
    assert any("Earlier first purchase" in row for row in rows)
    assert any("Current first purchase" in row for row in rows)
    assert all("Other account purchase" not in row for row in rows)
    assert '<h1>First wallet</h1>' in register
    assert f'<option value="{first}" selected>First wallet</option>' in register
    assert 'id="all-dates-filter" checked' in register
    assert '2 transactions · All dates' in register
    assert f'account={first}&amp;all_dates=true" aria-current="page"' in register
    monthly = client.get(f"/budgets/home/transactions/2026-10/usd?account={first}").text
    assert len(register_rows(monthly)) == 1
    assert "Earlier first purchase" not in monthly
    assert '1 transactions · October 2026' in monthly
    off_budget = client.get(unescape(account_links[1])).text
    assert "Other account purchase" in off_budget
    assert "Off-budget · excluded from budget" in off_budget


def test_account_register_is_currency_and_budget_scoped_and_can_show_all_accounts(client, store):
    usd = store.add_account("personal", {
        "name": "Dollar wallet", "type": "depository", "balance": 10000,
    })
    eur = store.add_account("personal", {
        "name": "Euro wallet", "type": "depository", "balance": 10000, "currency": "EUR",
    })
    for identifier, merchant in ((usd, "Dollar purchase"), (eur, "Euro purchase")):
        store.add_transaction("personal", {
            "accountId": identifier, "date": "2026-09-05", "merchant": merchant,
            "amount": 100, "categoryId": None,
        })
    for query in ("all_dates=true", f"all_dates=true&account={eur}", "all_dates=true&account=unknown"):
        response = client.get("/budgets/home/transactions/2026-10/usd?" + query)
        assert response.status_code == 200
        assert "Dollar purchase" in response.text and "Euro purchase" not in response.text
        assert '<h1>Transactions</h1>' in response.text
    euro = client.get(f"/budgets/home/transactions/2026-10/eur?all_dates=true&account={eur}")
    assert "Euro purchase" in euro.text and "Dollar purchase" not in euro.text
    demo = client.get(f"/budgets/demo/transactions/2026-10/usd?all_dates=true&account={usd}")
    assert demo.status_code == 200
    assert "Dollar purchase" not in demo.text and "Euro purchase" not in demo.text
    assert '<h1>Transactions</h1>' in demo.text
    assert client.get("/budgets/home/transactions/2026-10/usd?all_dates=invalid").status_code == 400


def test_empty_account_register_explains_the_selected_account(client, store):
    identifier = store.add_account("personal", {
        "name": "Empty <wallet>", "type": "depository", "balance": 0,
    })
    text = client.get(
        f"/budgets/home/transactions/current-month/usd?account={identifier}&all_dates=true"
    ).text
    assert "<h1>Empty &lt;wallet&gt;</h1>" in text
    assert "This account has no transactions." in text
    assert not register_rows(text)
