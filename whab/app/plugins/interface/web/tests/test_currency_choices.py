"""Closed account dialogs defer their shared public currency choices."""

import json
from html.parser import HTMLParser

import pytest

from whab.domain.money import SUPPORTED_CURRENCIES, currency_minor_units


class CurrencyMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.selects = []
        self.current = None
        self.metadata = []
        self.in_metadata = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and "data-currency-choices-lazy" in attrs:
            self.current = {"attrs": attrs, "options": []}
            self.selects.append(self.current)
        elif tag == "option" and self.current is not None:
            self.current["options"].append(attrs)
        elif tag == "script" and attrs.get("id") == "account-currency-choices":
            assert attrs["type"] == "application/json"
            self.in_metadata = True

    def handle_data(self, data):
        if self.in_metadata:
            self.metadata.append(data)

    def handle_endtag(self, tag):
        if tag == "select":
            self.current = None
        elif tag == "script":
            self.in_metadata = False


@pytest.mark.parametrize("currency", ["USD", "JPY", "KWD", "CLF"])
def test_closed_account_dialogs_keep_only_selected_currency_and_one_shared_catalog(client, store, currency):
    store.add_account("personal", {
        "name": f"{currency} cash", "type": "depository", "balance": 0, "currency": currency,
    })
    response = client.get(f"/budgets/home/accounts/2026-10/{currency.lower()}")
    assert response.status_code == 200
    markup = CurrencyMarkup(response.text)
    assert len(markup.selects) == 3
    assert markup.selects[2]["attrs"]["name"] == "defaultCurrency"
    assert markup.selects[2]["options"] == [{"value": "USD", "selected": None}]
    for select in markup.selects[:2]:
        assert select["attrs"]["name"] == "currency"
        assert select["options"] == [{
            "value": currency, "data-minor-units": str(currency_minor_units(currency)), "selected": None,
        }]
    assert "disabled" not in markup.selects[0]["attrs"]
    assert "disabled" in markup.selects[1]["attrs"]
    assert response.text.count('id="account-currency-choices"') == 1
    assert json.loads("".join(markup.metadata)) == {
        code: currency_minor_units(code) for code in SUPPORTED_CURRENCIES
    }
    fragment = client.get(
        f"/budgets/home/transactions/2026-10/{currency.lower()}",
        headers={"X-WHAB-Transactions-Fragment": "1"},
    )
    assert fragment.status_code == 200
    assert "account-currency-choices" not in fragment.text
