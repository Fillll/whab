"""Versioned, bounded ledger snapshots without banking connections or credentials.

The portable contract is independent of a storage implementation and plugin SDK.
Every field is allowlisted, including historical transaction annotations/tombstones.
Money stays in the original currency's integer minor units without conversion.
"""

import re

from .accounts import ACCOUNT_TYPES, normalize_account
from .categories import validate_child_category
from .errors import StoreError
from .money import MAX_SAFE_INTEGER, currency_code, minor_units
from .validation import validate_date, validate_month

ACCOUNT_FIELDS = frozenset({
    "id", "name", "institution", "type", "balance", "currency", "source", "stale",
    "balanceUnknown",
    "onBudget", "subtype",
})
_LEGACY_ACCOUNT_FIELDS = ACCOUNT_FIELDS - {"onBudget", "subtype"}
CATEGORY_FIELDS = frozenset({"id", "name", "group", "target", "currency"})
TRANSACTION_FIELDS = frozenset({
    "id", "accountId", "date", "merchant", "merchantLogo", "amount", "categoryId",
    "pending", "reviewed", "source", "note", "isTransfer", "inferredTransfer",
    "categoryEdited", "deleted",
})
ASSIGNMENT_FIELDS = frozenset({"month", "categoryId", "assigned"})
_ROOT_FIELDS = frozenset({
    "formatVersion", "budgetStartMonth", "accounts", "categories", "transactions", "assignments",
})


def _text(value: object, *, limit: int = 200, empty: bool = False) -> None:
    if (
        type(value) is not str or len(value) > limit or "\x00" in value
        or (not empty and not value.strip())
    ):
        raise StoreError("Invalid portable budget text.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise StoreError("Portable budget text must contain valid Unicode characters.") from None


def _records(
    value: object, fields: frozenset[str], limit: int, *, optional: frozenset[str] = frozenset(),
) -> list[dict]:
    if type(value) is not list or len(value) > limit:
        raise StoreError("Invalid portable budget collection.")
    result = []
    for record in value:
        if (
            type(record) is not dict or not fields <= record.keys()
            or not record.keys() <= fields | optional
        ):
            raise StoreError("Invalid portable budget record fields.")
        result.append(record.copy())
    return result


def _indexed(records: list[dict]) -> dict[str, dict]:
    result = {}
    for record in records:
        identifier = record["id"]
        _text(identifier)
        if identifier in result:
            raise StoreError("Duplicate portable budget identifier.")
        result[identifier] = record
    return result


def _booleans(record: dict, fields: tuple[str, ...]) -> None:
    if any(type(record[field]) is not bool for field in fields):
        raise StoreError("Invalid portable budget boolean.")


def validate_portable_budget(data: object) -> dict:
    """Validate exact JSON records and references, returning independent copies."""
    if type(data) is not dict or data.keys() != _ROOT_FIELDS:
        raise StoreError("Invalid portable budget fields.")
    if type(data["formatVersion"]) is not int or data["formatVersion"] != 1:
        raise StoreError("Unsupported portable budget version.")
    _text(data["budgetStartMonth"], limit=7)
    validate_month(data["budgetStartMonth"])
    accounts = _records(
        data["accounts"], _LEGACY_ACCOUNT_FIELDS, 1000,
        optional=frozenset({"onBudget", "subtype"}),
    )
    for index, account in enumerate(accounts):
        _text(account["type"])
        # Original v1 snapshots retained raw provider account types. An unknown
        # legacy type must remain readable without becoming budget funding.
        if not {"onBudget", "subtype"} & account.keys() and account["type"] not in ACCOUNT_TYPES:
            account["type"] = "other"
        accounts[index] = normalize_account(account)
    categories = _records(data["categories"], CATEGORY_FIELDS, 10000)
    transactions = _records(data["transactions"], TRANSACTION_FIELDS, 250000)
    assignments = _records(data["assignments"], ASSIGNMENT_FIELDS, 250000)
    account_map = _indexed(accounts)
    category_map = _indexed(categories)
    _indexed(transactions)
    totals = {}

    def money(value: object, currency: str) -> None:
        if type(value) is not int:
            raise StoreError("Portable budget money must be integer minor units.")
        minor_units(value, currency)
        totals[currency] = totals.get(currency, 0) + abs(value)
        if totals[currency] > MAX_SAFE_INTEGER:
            raise StoreError("Portable budget currency totals exceed the supported range.")

    for account in accounts:
        for field in ("name", "type", "source"):
            _text(account[field])
        _text(account["institution"], empty=True)
        currency_code(account["currency"])
        _booleans(account, ("stale", "balanceUnknown"))
        money(account["balance"], account["currency"])
    for category in categories:
        validate_child_category(category)
        _text(category["name"])
        _text(category["group"])
        money(category["target"], category["currency"])
    for transaction in transactions:
        _text(transaction["accountId"])
        account = account_map.get(transaction["accountId"])
        if account is None:
            raise StoreError("Portable budget account reference not found.")
        category_id = transaction["categoryId"]
        if category_id is not None:
            _text(category_id)
            category = category_map.get(category_id)
            if category is None:
                raise StoreError("Portable budget category reference not found.")
            if category["currency"] != account["currency"]:
                raise StoreError("Portable budget category currency must match its account.")
        _text(transaction["date"], limit=10)
        validate_date(transaction["date"])
        validate_month(transaction["date"][:7])
        _text(transaction["merchant"])
        _text(transaction["source"])
        _text(transaction["note"], limit=1000, empty=True)
        logo = transaction["merchantLogo"]
        if logo is not None and (
            type(logo) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", logo) is None
        ):
            raise StoreError("Invalid portable budget merchant logo key.")
        _booleans(transaction, (
            "pending", "reviewed", "isTransfer", "inferredTransfer", "categoryEdited", "deleted",
        ))
        money(transaction["amount"], account["currency"])
    assignment_keys = set()
    for assignment in assignments:
        _text(assignment["month"], limit=7)
        validate_month(assignment["month"])
        _text(assignment["categoryId"])
        category = category_map.get(assignment["categoryId"])
        if category is None:
            raise StoreError("Portable budget assignment category not found.")
        key = (assignment["month"], assignment["categoryId"])
        if key in assignment_keys:
            raise StoreError("Duplicate portable budget assignment.")
        assignment_keys.add(key)
        money(assignment["assigned"], category["currency"])
    return {
        "formatVersion": 1,
        "budgetStartMonth": data["budgetStartMonth"],
        "accounts": accounts,
        "categories": categories,
        "transactions": transactions,
        "assignments": assignments,
    }
