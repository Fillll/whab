"""Provider normalization preserves safe account and category fallbacks."""

import pytest
from whab_plugin_plaid.mapping import normalize_account, suggest_category, transfer_category

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
    assert account["onBudget"] is True
    assert account["subtype"] is None


@pytest.mark.parametrize("kind,provider_subtype,expected_type,expected_subtype,on_budget", [
    ("depository", "checking", "depository", "checking", True),
    ("depository", "savings", "depository", "savings", True),
    ("depository", "money market", "depository", "money_market", True),
    ("depository", "cd", "deposit", "cd", False),
    ("credit", "credit card", "credit", "credit_card", True),
    ("credit", "line of credit", "credit", "line_of_credit", True),
    ("investment", "brokerage", "investment", "brokerage", False),
    ("investment", "ira", "retirement", "ira", False),
    ("investment", "roth", "retirement", "roth_ira", False),
    ("investment", "roth ira", "retirement", "roth_ira", False),
    ("investment", "401k", "retirement", "401k", False),
    ("investment", "roth 401k", "retirement", "401k", False),
    ("investment", "403B", "retirement", "403b", False),
    ("investment", "457b", "retirement", "457b", False),
    ("investment", "pension", "retirement", "pension", False),
    ("loan", "mortgage", "loan", "mortgage", False),
    ("loan", "auto", "loan", "auto_loan", False),
    ("loan", "student", "loan", "student_loan", False),
    ("investment", "provider_new_subtype", "investment", None, False),
    ("loan", None, "loan", None, False),
    ("other", None, "other", None, False),
    ("provider_new_type", "provider_new_subtype", "other", None, False),
])
def test_provider_accounts_normalize_budget_scope_and_asset_types(
    kind, provider_subtype, expected_type, expected_subtype, on_budget,
):
    account = normalize_account({
        "account_id": "bank", "type": kind, "subtype": provider_subtype,
        "balances": {"current": 12.34},
    }, "item", "Fixture Bank")
    assert account["type"] == expected_type
    assert account["subtype"] == expected_subtype
    assert account["onBudget"] is on_budget
    assert account["balance"] == 1234


def test_provider_missing_coarse_type_preserves_legacy_checking_default():
    account = normalize_account({"account_id": "bank"}, "item", "Fixture Bank")
    assert account["type"] == "depository"
    assert account["onBudget"] is True
    assert account["subtype"] is None


@pytest.mark.parametrize(
    "balances", [{"iso_currency_code": "EUR"}, {"unofficial_currency_code": "BTC"}]
)
def test_non_usd_accounts_are_rejected_before_import(balances):
    with pytest.raises(StoreError, match="Only USD bank accounts are supported"):
        normalize_account({"account_id": "bank", "balances": balances}, "item", "Fixture Bank")
