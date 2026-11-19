"""Account classification and budget scope, independent of providers and storage.

Assets carry their signed value; liabilities carry a positive amount owed. Only
on-budget cash and credit accounts participate in envelope funding and activity.
"""

import ipaddress
import re
from urllib.parse import urlsplit

from .errors import StoreError
from .money import MAX_SAFE_INTEGER, cents, currency_code
from .validation import validate_label

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


def bank_website(value):
    """Keep only a public HTTPS hostname; never save a credential-bearing bank URL."""
    if value is None or value == "":
        return None
    if type(value) is not str or len(value) > 2048 or any(character.isspace() for character in value):
        raise StoreError("Use the bank's public HTTPS website.")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or parsed.query or parsed.fragment or not parsed.hostname):
            raise ValueError()
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        labels = hostname.split(".")
        if (len(hostname) > 253 or len(labels) < 2
                or labels[-1] in {"localhost", "local", "internal", "lan", "home", "test", "invalid"}
                or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1])
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
            raise ValueError()
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            return "https://" + hostname
    except (ValueError, UnicodeError):
        pass
    raise StoreError("Use the bank's public HTTPS website without credentials, a port, query or fragment.")


def bank_website_entry(value):
    """Best-effort optional artwork input; never block financial metadata edits.

    Users may paste a public address without a scheme. Only its public hostname
    is retained; the strict validator still guards persisted/provider targets.
    Unusable artwork input clears this optional field instead of raising.
    """
    if type(value) is not str:
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    elif value[:7].lower() == "http://":
        value = "https://" + value[7:]
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or not parsed.hostname):
            return None
        return bank_website("https://" + parsed.hostname)
    except (ValueError, UnicodeError):
        return None


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
    closed = account.get("closed", False)
    if type(closed) is not bool:
        raise StoreError("Invalid account lifecycle state.")
    result = {**account, "onBudget": on_budget, "subtype": subtype}
    # Elide the compatible default in canonical saved state: existing financial
    # history hashes and native recovery evidence must remain byte-compatible.
    result.pop("closed", None)
    if closed:
        result["closed"] = True
    if "bankWebsite" in account:
        result["bankWebsite"] = bank_website(account["bankWebsite"])
    if "nameOverridden" in account and type(account["nameOverridden"]) is not bool:
        raise StoreError("Invalid account name override.")
    return result


def normalize_manual_account(account: dict, existing_accounts=()) -> dict:
    """Validate a creation payload without assigning an ID or changing a ledger.

    Unknown balances start a zero-origin activity register, not a verified zero
    balance. Optional uniqueName is a creation guard, never persisted metadata.
    Native creation repeats this validation against its atomic workspace state.
    """
    required = {"name", "type", "balance"}
    optional = {"currency", "onBudget", "subtype", "closed", "balanceUnknown", "uniqueName"}
    if type(account) is not dict or not required <= account.keys() or account.keys() - required - optional:
        raise StoreError("Invalid manual account creation fields; name, type and balance are required.")
    result = normalize_account({
        **account, "name": validate_label(account["name"]),
        "balance": cents(account["balance"]),
        "currency": currency_code(account.get("currency", "USD")),
    })
    for field in ("balanceUnknown", "uniqueName"):
        if field in account and type(account[field]) is not bool:
            raise StoreError(f"{field} must be true or false.")
    if result.get("balanceUnknown") and result["balance"] != 0:
        raise StoreError("An unknown opening balance requires balance 0 as the empty activity register, not an opening balance estimate.")
    if result.get("uniqueName"):
        matches = [row for row in existing_accounts
                   if row["name"].strip().casefold() == result["name"].casefold()]
        if matches:
            candidates = ", ".join(f"{row['name']} ({row['id']})" for row in matches)
            raise StoreError(f"An account with this name already exists: {candidates}. Choose another name or use the existing account ID.")
    return result


def account_balance_position(account: dict) -> dict:
    """Return owned-positive/owed-negative display money and its evidence status.

    YNAB export totals are already signed and remain estimates, including legacy
    records lacking freshness flags. Other liabilities use canonical positive
    amounts owed, so a negative stored card balance becomes a positive credit.
    This projection never verifies, normalizes or changes the saved account.
    """
    balance = account.get('balance')
    if type(balance) is not int or abs(balance) > MAX_SAFE_INTEGER:
        return {'value': None, 'status': 'unavailable'}
    if account.get('source') == 'ynab':
        return {'value': balance, 'status': 'estimated'}
    if account.get('balanceUnknown'):
        return {'value': None, 'status': 'unavailable'}
    return {'value': -balance if is_liability(account) else balance,
            'status': 'stale' if account.get('stale') else 'current'}


def recorded_account_activity(accounts, transactions) -> dict:
    """Signed inflows less outflows for unknown manual registers, never balances.

    Include all dates, sources and pending records; exclude deleted/voided rows.
    Classification does not rewrite ledger amounts, so this projection is stable
    across asset/liability changes even when the raw balance accumulator is not.
    """
    totals = {row['id']: 0 for row in accounts
              if row.get('source') == 'manual' and row.get('balanceUnknown')}
    if totals:
        for row in transactions:
            if row['accountId'] in totals and not row.get('deleted'):
                totals[row['accountId']] -= row['amount']
    return {identifier: cents(value) for identifier, value in totals.items()}


def account_summary(accounts) -> dict:
    """Current known snapshots, in one currency, with debts subtracted once."""
    result = dict.fromkeys(("assets", "liabilities", "netWorth", "onBudget", "offBudget"), 0)
    for account in accounts:
        position = account_balance_position(account)
        if position['status'] != 'current':
            continue
        value = position['value']
        result["assets"] += max(0, value)
        result["liabilities"] += max(0, -value)
        result["netWorth"] += value
        result["onBudget" if is_on_budget(account) else "offBudget"] += value
    if any(abs(value) > MAX_SAFE_INTEGER for value in result.values()):
        raise StoreError("Account totals exceed the supported range.")
    return result
