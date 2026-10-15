"""Provider normalization preserves safe account and category fallbacks."""

import pytest
from whab_plaid.mapping import normalize_account, suggest_category, transfer_category

from whab.domain.errors import StoreError


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ({}, None),
        ({"primary": "UNRECOGNIZED"}, None),
        ({"primary": "INCOME"}, None),
        ({"primary": "TRANSFER_IN"}, None),
        ({"primary": "FOOD_AND_DRINK"}, "dining"),
        ({"detailed": "RENT"}, "rent"),
        ({"primary": "UTILITIES"}, "utilities"),
        ({"primary": "TRANSPORTATION"}, "transport"),
        ({"primary": "MEDICAL"}, "health"),
        ({"primary": "ENTERTAINMENT"}, "fun"),
        ({"primary": "TRAVEL"}, "travel"),
        ({"primary": "GENERAL_MERCHANDISE"}, "shopping"),
    ],
)
def test_category_suggestions_leave_unknown_income_and_transfers_uncategorized(category, expected):
    assert suggest_category({"personal_finance_category": category}) == expected


def test_credit_card_payments_are_inferred_transfers():
    transaction = {"personal_finance_category": {"detailed": "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"}}
    assert transfer_category(transaction) is True
    assert suggest_category(transaction) is None


def test_depository_accounts_use_available_when_current_balance_is_missing():
    account = normalize_account(
        {"account_id": "bank", "type": "depository", "balances": {"available": 12.34}},
        "item",
        "Fixture Bank",
    )
    assert account["balance"] == 1234
    assert account["balanceUnknown"] is False
    assert account["stale"] is False
    assert account["name"] == "Bank account"
    assert account["mask"] == ""


@pytest.mark.parametrize(
    "balances", [{"iso_currency_code": "EUR"}, {"unofficial_currency_code": "BTC"}]
)
def test_non_usd_accounts_are_rejected_before_import(balances):
    with pytest.raises(StoreError, match="Only USD bank accounts are supported"):
        normalize_account({"account_id": "bank", "balances": balances}, "item", "Fixture Bank")
