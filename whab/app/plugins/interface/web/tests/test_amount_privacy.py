"""Amount privacy annotates public Web views without rewriting ledger values."""

import re
from html.parser import HTMLParser

import pytest

from whab.plugins import Insight, Plugin


class MonetaryMarkup(HTMLParser):
    """Locate actual display values, independently of template implementation."""

    money = re.compile(r"(?:\$|\b(?:USD|JPY|KWD|EUR)\s)[−-]?\d|(?<![\w.])[−-]?\d[\d,]*\.\d{2,3}(?![\w.])")
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, text):
        super().__init__()
        self.stack = []
        self.unprotected = []
        self.money_inputs = []
        self.protected_text = []
        self.feed(text)

    def handle_starttag(self, tag, pairs):
        attributes = dict(pairs)
        protected = any(item[1] for item in self.stack) or any(
            marker in attributes for marker in ("data-private-amount", "data-private-content")
        )
        if tag == "input" and attributes.get("type") != "hidden" and (
            attributes.get("name") in {"amount", "balance", "target", "assigned"}
            or attributes.get("data-transaction-detail") == "amount"
        ):
            self.money_inputs.append(attributes)
            if not protected:
                self.unprotected.append(("input", attributes.get("aria-label", attributes.get("name"))))
        for key in ("title", "aria-label", "aria-valuetext"):
            value = attributes.get(key, "")
            if self.money.search(value) and not protected and key not in attributes.get("data-private-amount-attributes", "").split():
                self.unprotected.append((key, value))
        if tag not in self.void:
            self.stack.append((tag, protected))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(tag in {"script", "style"} for tag, _ in self.stack) or not self.money.search(data):
            return
        if any(protected for _, protected in self.stack):
            self.protected_text.append(data)
        else:
            self.unprotected.append(("text", data.strip()))


@pytest.mark.parametrize("page", ["budget", "transactions", "accounts", "insights"])
@pytest.mark.parametrize("fragment", [False, True])
def test_rendered_money_is_protected_without_changing_financial_records(client, store, page, fragment):
    before = store.accounts("demo"), store.transactions("demo")
    response = client.get(
        f"/budgets/demo/{page}/2026-10/usd?all_dates=true",
        headers={"X-WHAB-Page-Fragment": "1"} if fragment else {},
    )
    assert response.status_code == 200
    markup = MonetaryMarkup(response.text)
    assert markup.protected_text, "The fictional sample must exercise monetary display values."
    assert markup.unprotected == []
    if fragment:
        assert 'data-private-currency="USD"' in response.text
    if page in {"budget", "transactions"}:
        assert markup.money_inputs, "Editable amounts must be concealed without replacing their values."
    assert (store.accounts("demo"), store.transactions("demo")) == before


def test_privacy_bootstrap_precedes_style_and_transaction_fragments_keep_exact_amounts(client):
    full = client.get("/budgets/demo/transactions/2026-10?all_dates=true").text
    assert full.index("/static/amount-privacy.js") < full.index("/static/styles.css")
    assert "data-amount-privacy-toggle" in full
    fragment = client.get(
        "/budgets/demo/transactions/2026-10?all_dates=true",
        headers={"X-WHAB-Transactions-Fragment": "1"},
    )
    assert fragment.status_code == 200
    markup = MonetaryMarkup(fragment.text)
    assert markup.unprotected == []
    inputs = {field.get("value") for field in markup.money_inputs}
    assert {"-64.25", "8200.00"} <= inputs
    assert "XX" not in inputs
    assert 'value="2026-10-' in fragment.text


def test_untyped_extension_report_values_and_details_are_shielded(store, tmp_path, client_for):
    def insights(_snapshot):
        return (Insight("Fictional report", "$918.27", "Projected reserve EUR 3,456.78"),)

    with client_for(store, tmp_path, Plugin("privacy-fixture", "Fictional report", insights=insights)) as client:
        response = client.get("/budgets/demo/insights/2026-10/usd")
    assert response.status_code == 200
    markup = MonetaryMarkup(response.text)
    assert markup.unprotected == []
    assert any("$918.27" in text for text in markup.protected_text)
    assert any("EUR 3,456.78" in text for text in markup.protected_text)


@pytest.mark.parametrize("currency,balance,expected", [("USD", 123456, "$1,235"), ("JPY", 123456, "JPY 123,456"), ("KWD", 123456, "KWD 123")])
def test_account_privacy_covers_currency_precision_and_keeps_stored_balance(client, store, currency, balance, expected):
    account = store.add_account("personal", {"name": "Fictional 東京 🌱", "type": "depository", "balance": balance, "currency": currency})
    response = client.get("/budgets/home/accounts/2026-10/usd")
    assert response.status_code == 200
    assert "Fictional 東京 🌱" in response.text
    # Accounts can mix currencies: totals, row and sidebar carry their own scope.
    assert response.text.count(f'data-private-currency="{currency}"') >= 3
    markup = MonetaryMarkup(response.text)
    assert markup.unprotected == []
    assert any(expected in value for value in markup.protected_text)
    assert next(row for row in store.accounts("personal") if row["id"] == account)["balance"] == balance
