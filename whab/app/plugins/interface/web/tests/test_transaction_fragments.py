"""Account switches receive scoped server-rendered registers without the page shell."""

import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

FRAGMENT = {"X-WHAB-Transactions-Fragment": "1"}
OMIT_SIDEBAR = {**FRAGMENT, "X-WHAB-Transactions-Sidebar": "omit"}
SUMMARY = {"X-WHAB-Transactions-Fragment": "summary"}


class RegisterMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.context = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and ("data-transactions-fragment" in attrs or "data-transactions-summary" in attrs):
            self.context = json.loads(attrs["data-page-context"])


def test_transaction_fragment_matches_full_register_without_reloading_shell(client):
    path = "/budgets/demo/transactions/2026-08/usd?all_dates=true"
    response = client.get(path, headers=FRAGMENT)
    assert response.status_code == 200
    assert response.headers["X-WHAB-Transactions-Fragment"] == "1"
    assert response.headers["Cache-Control"] == "no-store"
    assert "X-WHAB-Transactions-Fragment" in response.headers["Vary"]
    assert "X-WHAB-Transactions-Sidebar" in response.headers["Vary"]
    register, account_payload = response.text.split('<template data-account-list>', 1)
    full = client.get(path).text
    assert register.strip() in full
    assert re.sub(r'</template>\s*$', '', account_payload).strip() in full
    assert "<!DOCTYPE" not in response.text and "<script" not in response.text
    assert 'class="sidebar"' not in response.text and 'class="topbar"' not in response.text
    context = RegisterMarkup(response.text).context
    assert context["budgetUrl"] == "/budgets/demo"
    assert context["page"] == "transactions" and context["monthToken"] == "2026-08"
    assert context["currency"] == "USD"


def test_sidebar_omission_is_opt_in_and_preserves_the_same_register(client, store):
    path = "/budgets/demo/transactions/2026-08/usd?all_dates=true&q=&tx_page=1"
    legacy = client.get(path, headers=FRAGMENT)
    lean = client.get(path, headers=OMIT_SIDEBAR)
    assert lean.status_code == 200
    assert lean.headers["X-WHAB-Transactions-Fragment"] == "1"
    assert lean.headers["Cache-Control"] == "no-store"
    assert "data-account-list" not in lean.text
    assert lean.text.strip() == legacy.text.split('<template data-account-list>', 1)[0].strip()
    assert RegisterMarkup(lean.text).context == RegisterMarkup(legacy.text).context
    assert len(lean.content) < len(legacy.content)
    explicit_include = client.get(path, headers={**FRAGMENT, "X-WHAB-Transactions-Sidebar": "include"})
    assert '<template data-account-list>' in explicit_include.text
    full = client.get(path, headers={"X-WHAB-Transactions-Sidebar": "omit"})
    assert "<!DOCTYPE html>" in full.text and 'class="sidebar"' in full.text


def test_default_fragment_refresh_includes_new_account_sidebar_markup(client, store):
    first = store.add_account("personal", {"name": "Original account", "type": "depository", "balance": 0})
    path = f"/budgets/home/transactions/2026-10/usd?account={first}"
    before = client.get(path, headers=FRAGMENT)
    assert before.status_code == 200 and '<template data-account-list>' in before.text
    second = store.add_account("personal", {"name": "New account", "type": "depository", "balance": 0})
    refreshed = client.get(path, headers=FRAGMENT)
    sidebar = refreshed.text.split('<template data-account-list>', 1)[1]
    assert f'data-account-id="{first}"' in sidebar
    assert f'data-account-id="{second}"' in sidebar and "New account" in sidebar
    assert 'data-account-list' not in client.get(path, headers=OMIT_SIDEBAR).text


@pytest.mark.parametrize("header", [{}, {"X-WHAB-Transactions-Fragment": "true"}])
def test_register_fragment_is_opt_in_and_only_for_transactions(client, header):
    full = client.get("/budgets/demo/transactions/2026-08/usd", headers=header)
    assert "<!DOCTYPE html>" in full.text
    assert "X-WHAB-Transactions-Fragment" not in full.headers
    assert "X-WHAB-Transactions-Fragment" in full.headers["Vary"]
    for page in ("budget", "accounts", "insights"):
        full = client.get(f"/budgets/demo/{page}/2026-08/usd", headers=FRAGMENT)
        assert "<!DOCTYPE html>" in full.text
        assert "X-WHAB-Transactions-Fragment" not in full.headers


@pytest.mark.parametrize("header", [FRAGMENT, OMIT_SIDEBAR])
def test_register_fragment_preserves_account_date_currency_and_escaping(client, header):
    accounts = {}
    for currency in ("USD", "JPY"):
        response = client.post("/api/accounts", json={
            "workspace": "personal", "name": f"{currency} wallet", "type": "depository",
            "balance": 0, "currency": currency,
        })
        accounts[currency] = response.json()["accountId"]
        for month in ("2026-08", "2026-09"):
            assert client.post("/api/transactions", json={
                "workspace": "personal", "accountId": accounts[currency],
                "date": month + "-05", "merchant": currency + " <script>fixture</script>",
                "amount": 123, "categoryId": None,
            }).status_code == 200
    base = "/budgets/home/transactions/2026-09/jpy?account=" + accounts["JPY"]
    response = client.get(base, headers=header)
    assert response.status_code == 200
    assert "JPY &lt;script&gt;fixture&lt;/script&gt;" in response.text
    assert "USD wallet" not in response.text and "USD &lt;script&gt;" not in response.text
    assert "2026-09-05" in response.text and "2026-08-05" not in response.text
    assert "<script>fixture</script>" not in response.text
    assert 'data-transaction-id=' in response.text
    all_dates = client.get(base + "&all_dates=true", headers=header)
    assert "2026-08-05" in all_dates.text
    assert accounts["JPY"] in unescape(response.text)


@pytest.mark.parametrize("header", [FRAGMENT, SUMMARY])
def test_fragment_navigation_badge_tracks_review_edits_and_selected_account_without_reload(client, store, header):
    first = store.add_account("personal", {"name": "First", "type": "depository", "balance": 0})
    second = store.add_account("personal", {"name": "Second", "type": "depository", "balance": 0})
    transactions = [store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-05", "merchant": f"Purchase {index}",
        "amount": 100, "categoryId": None,
    }) for index, account in enumerate((first, first, second))]

    def count_markup(account):
        response = client.get(
            f"/budgets/home/transactions/2026-10/usd?account={account}", headers=header,
        )
        assert response.status_code == 200 and "<!DOCTYPE" not in response.text
        return re.search(r'<template data-transaction-navigation-count>(.*?)</template>', response.text, re.S)[1]

    assert count_markup(first) == '<span class="nav-count">2</span>'
    for index in range(2):
        response = client.patch(f"/api/transactions/{transactions[index]}", json={
            "workspace": "personal", "reviewed": True,
        })
        assert response.status_code == 200
        assert count_markup(first) == ('<span class="nav-count">1</span>' if index == 0 else '')
    assert count_markup(second) == '<span class="nav-count">1</span>'


def test_summary_returns_only_scoped_server_badge_and_canonical_validation_context(client, store):
    first = store.add_account("personal", {"name": "First", "type": "depository", "balance": 0})
    second = store.add_account("personal", {"name": "Second", "type": "depository", "balance": 0})
    for account, date in [(first, "2026-10-05"), (second, "2026-10-05"), (first, "2026-09-05")]:
        store.add_transaction("personal", {
            "accountId": account, "date": date, "merchant": "Private description absent from summary",
            "amount": 100, "categoryId": None,
        })
    path = f"/budgets/home/transactions/2026-10/usd?accounts={first}&accounts={second}&q=&tx_page=1"
    response = client.get(path, headers=SUMMARY)
    assert response.status_code == 200
    assert response.headers["X-WHAB-Transactions-Fragment"] == "summary"
    assert response.headers["Cache-Control"] == "no-store"
    assert "X-WHAB-Transactions-Fragment" in response.headers["Vary"]
    assert "X-WHAB-Transactions-Sidebar" in response.headers["Vary"]
    assert '<span class="nav-count">2</span>' in response.text
    assert len(response.content) < 2500
    assert len(response.content) < len(client.get(path, headers=FRAGMENT).content) / 10
    assert "Private description" not in response.text
    for markup in ("transaction-row", "data-transaction-register", "data-account-list", "<script", "<!DOCTYPE"):
        assert markup not in response.text
    context = RegisterMarkup(response.text).context
    assert context["selectedAccountIds"] == [first, second]
    assert context["transactionSearch"] == "" and context["transactionRequestedPage"] == "1"
    assert context["reviewFilter"] == "all" and context["currency"] == "USD"
    assert parse_qs(urlsplit(context["canonicalUrl"]).query, keep_blank_values=True) == {
        "accounts": [first, second], "q": [""], "tx_page": ["1"],
    }
    assert "accountSlugs" not in context
    other_page = client.get("/budgets/demo/accounts/2026-10/usd", headers=SUMMARY)
    assert "<!DOCTYPE html>" in other_page.text
    assert "X-WHAB-Transactions-Fragment" not in other_page.headers


@pytest.mark.parametrize("header,status", [
    ({"Origin": "https://untrusted.invalid"}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
])
@pytest.mark.parametrize("fragment", [FRAGMENT, OMIT_SIDEBAR, SUMMARY])
def test_register_fragment_does_not_bypass_local_request_guards(client, header, status, fragment):
    response = client.get("/budgets/demo/transactions/2026-08/usd", headers={**fragment, **header})
    assert response.status_code == status
    assert "transactions-table" not in response.text
