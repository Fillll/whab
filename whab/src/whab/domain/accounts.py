"""Account classification and budget scope, independent of providers and storage.

Assets carry their signed value; liabilities carry a positive amount owed. Only
on-budget cash and credit accounts participate in envelope funding and activity.
"""

from .errors import StoreError
from .money import MAX_SAFE_INTEGER

ACCOUNT_TYPES = {
    "depository": "Cash / checking / savings",
    "credit": "Credit card / line of credit",
    "investment": "Investment account",
    "retirement": "Retirement account",
    "property": "Property",
    "loan": "Loan",
    "deposit": "Fixed-term deposit",
    "other_asset": "Other asset",
    "other_liability": "Other liability",
    "other": "Unclassified account",
}
ACCOUNT_SUBTYPES = {
    "depository": {
        "checking": "Checking", "savings": "Savings", "cash": "Cash",
        "money_market": "Money market",
    },
    "credit": {"credit_card": "Credit card", "line_of_credit": "Line of credit"},
    "investment": {"brokerage": "Brokerage"},
    "retirement": {
        "ira": "Traditional IRA", "roth_ira": "Roth IRA", "401k": "401(k)",
        "403b": "403(b)", "457b": "457(b)", "pension": "Pension",
        "other_retirement": "Other retirement account",
    },
    "property": {"home": "Home", "vehicle": "Vehicle", "other_property": "Other property"},
    "loan": {
        "mortgage": "Mortgage", "auto_loan": "Car loan", "student_loan": "Student loan",
        "other_loan": "Other loan",
    },
    "deposit": {"cd": "Certificate of deposit (CD)", "term_deposit": "Term deposit", "other_deposit": "Other fixed-term deposit"},
}
ON_BUDGET_ACCOUNT_TYPES = frozenset({"depository", "credit"})
LIABILITY_ACCOUNT_TYPES = frozenset({"credit", "loan", "other_liability"})


def is_on_budget(account: dict) -> bool:
    """Legacy cash/card records stay on budget; tracking records default off."""
    return account.get("onBudget", account["type"] in ON_BUDGET_ACCOUNT_TYPES)


def is_liability(account: dict) -> bool:
    return account["type"] in LIABILITY_ACCOUNT_TYPES


def normalize_account(account: dict) -> dict:
    """Validate explicit classification and supply compatible legacy defaults."""
    kind = account["type"]
    if type(kind) is not str or kind not in ACCOUNT_TYPES:
        raise StoreError("Invalid account type.")
    on_budget = is_on_budget(account)
    if type(on_budget) is not bool:
        raise StoreError("Invalid account budget scope.")
    if on_budget and kind not in ON_BUDGET_ACCOUNT_TYPES:
        raise StoreError("Only cash and credit accounts can be on budget.")
    subtype = account.get("subtype")
    if subtype is not None and (
        type(subtype) is not str or subtype not in ACCOUNT_SUBTYPES.get(kind, {})
    ):
        raise StoreError("Invalid account subtype.")
    return {**account, "onBudget": on_budget, "subtype": subtype}


def account_summary(accounts) -> dict:
    """Current known snapshots, in one currency, with debts subtracted once."""
    result = dict.fromkeys(("assets", "liabilities", "netWorth", "onBudget", "offBudget"), 0)
    for account in accounts:
        if account.get("stale") or account.get("balanceUnknown"):
            continue
        value = account["balance"] * (-1 if is_liability(account) else 1)
        result["assets"] += max(0, value)
        result["liabilities"] += max(0, -value)
        result["netWorth"] += value
        result["onBudget" if is_on_budget(account) else "offBudget"] += value
    if any(abs(value) > MAX_SAFE_INTEGER for value in result.values()):
        raise StoreError("Account totals exceed the supported range.")
    return result
