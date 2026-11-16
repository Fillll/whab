"""Account surfaces present signed positions without changing stored conventions."""

import re

import pytest

POSITIONS = (
    ("cash", "depository", 100000, "bank", False, False, "USD", "$1,000", "Current balance"),
    ("card", "credit", 123450, "bank", False, False, "USD", "-$1,235", "Current balance"),
    ("overpaid", "credit", -12345, "manual", False, False, "USD", "$123", "Current balance"),
    ("loan", "loan", 500000, "manual", False, False, "USD", "-$5,000", "Current balance"),
    ("other-debt", "other_liability", 10000, "manual", False, False, "USD", "-$100", "Current balance"),
    ("overdraft", "depository", -9999, "bank", False, False, "USD", "-$100", "Current balance"),
    ("zero", "credit", 0, "manual", False, False, "USD", "$0", "Current balance"),
    ("old-debt", "credit", 12550, "bank", True, False, "USD", "-$126", "Last known balance"),
    ("old-asset", "investment", 9900, "bank", True, False, "USD", "$99", "Last known balance"),
    ("unknown", "credit", 12300, "bank", True, True, "USD", "Unavailable", "Balance unavailable"),
    ("import-debt", "credit", -250050, "ynab", True, True, "USD", "-$2,501", "Estimated from imported transactions · unverified"),
    ("legacy-import", "loan", -800000, "ynab", False, False, "USD", "-$8,000", "Estimated from imported transactions · unverified"),
    ("import-overpaid", "credit", 24550, "ynab", False, False, "USD", "$246", "Estimated from imported transactions · unverified"),
    ("yen-debt", "loan", 1234, "bank", False, False, "JPY", "-JPY 1,234", "Current balance"),
    ("dinar-debt", "credit", 123499, "bank", False, False, "KWD", "-KWD 123", "Current balance"),
    ("dinar-overpaid", "credit", -123500, "bank", False, False, "KWD", "KWD 124", "Current balance"),
)


@pytest.fixture
def signed_accounts(store):
    def seed(state):
        for identifier, kind, balance, source, stale, unknown, currency, *_ in POSITIONS:
            state["accounts"][identifier] = {
                "id": identifier, "name": identifier, "institution": "Fictional",
                "type": kind, "balance": balance, "source": source, "stale": stale,
                "balanceUnknown": unknown, "currency": currency,
            }
    store._mutate("personal", seed)
    return store.export_budget("personal")


@pytest.mark.parametrize("headers", [{}, {"X-WHAB-Page-Fragment": "1"}])
def test_accounts_rows_sidebar_and_totals_share_signed_positions(client, store, signed_accounts, headers):
    response = client.get("/budgets/home/accounts", headers=headers)
    assert response.status_code == 200
    text = response.text
    for identifier, *_, currency, expected, label in POSITIONS:
        row = re.search(rf'<form[^>]+data-inline-account="{identifier}"[^>]*>(.*?)</form>', text, re.S)[1]
        sidebar = re.search(rf'<a class="sidebar-account"[^>]*data-account-id="{identifier}"[^>]*>(.*?)</a>', text, re.S)[1]
        assert f'<span data-private-amount data-private-currency="{currency}">{expected}</span>' in row
        assert f'<small>{label}</small>' in row
        assert '<small>Owed</small>' not in row
        sidebar_amount = "—" if expected == "Unavailable" else expected
        assert f'<span data-private-amount>{sidebar_amount}</span>' in sidebar
        assert f'title="{label}"' in sidebar
        assert 'data-private-amount-attributes="aria-label title"' in sidebar
    for label, expected in (("Assets", "$1,123"), ("Liabilities", "-$6,434"), ("Net worth", "-$5,311")):
        assert f'<dt>{label} · USD</dt><dd><span data-private-amount>{expected}</span></dd>' in text
    assert '<dt>Liabilities · JPY</dt><dd><span data-private-amount>-JPY 1,234</span></dd>' in text
    assert '<dt>Liabilities · KWD</dt><dd><span data-private-amount>-KWD 123</span></dd>' in text
    assert "Imported estimates, stale and unavailable snapshots are excluded" in text
    # API/storage retain positive owed values and exact cents; reads create no history.
    assert store.workspace("personal", "2026-10")["accountSummary"] == {
        "assets": 112345, "liabilities": 643449, "netWorth": -531104,
        "onBudget": -21104, "offBudget": -510000,
    }
    assert store.export_budget("personal") == signed_accounts


@pytest.mark.parametrize("page", ["budget", "transactions", "insights"])
@pytest.mark.parametrize("headers", [{}, {"X-WHAB-Page-Fragment": "1"}])
def test_shared_sidebar_uses_same_sign_and_provenance_on_every_page(client, store, signed_accounts, page, headers):
    response = client.get(f"/budgets/home/{page}/2026-10/usd", headers=headers)
    assert response.status_code == 200
    for identifier, *_, expected, label in POSITIONS:
        sidebar = re.search(rf'<a class="sidebar-account"[^>]*data-account-id="{identifier}"[^>]*>(.*?)</a>', response.text, re.S)[1]
        assert f'<span data-private-amount>{"—" if expected == "Unavailable" else expected}</span>' in sidebar
        assert f'title="{label}"' in sidebar
    assert store.export_budget("personal") == signed_accounts


def test_account_position_does_not_reverse_transaction_cashflow_or_round_exact_editors(client, store):
    account = store.add_account("personal", {"name": "Fictional card", "type": "credit", "balance": 0})
    for merchant, amount in (("Fictional purchase", 123456), ("Fictional refund", -23456)):
        store.add_transaction("personal", {
            "accountId": account, "date": "2026-10-15", "merchant": merchant,
            "amount": amount, "categoryId": None,
        })
    before = store.export_budget("personal")
    response = client.get("/budgets/home/transactions/2026-10")
    assert response.status_code == 200
    assert 'value="-1234.56"' in response.text
    assert 'value="234.56"' in response.text
    assert 'data-money-rounded data-transaction-detail="amount"' in response.text
    assert '<span data-private-amount>-$1,000</span>' in response.text
    assert store.export_budget("personal") == before
