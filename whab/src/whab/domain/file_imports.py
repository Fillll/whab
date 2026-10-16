"""Provider-independent, complete workspace imports in integer currency minor units.

Adapters construct these records without storage knowledge. The storage boundary
validates and copies them again while holding its write lock before committing.
"""

from dataclasses import dataclass

from .accounts import normalize_account
from .categories import validate_child_category
from .errors import StoreError
from .money import MAX_SAFE_INTEGER, cents, currency_code
from .validation import validate_date, validate_month


@dataclass(frozen=True)
class WorkspaceImport:
    accounts: tuple[dict, ...]
    categories: tuple[dict, ...]
    transactions: tuple[dict, ...]
    assignments: tuple[dict, ...]
    budget_start_month: str
    warnings: tuple[str, ...] = ()
    currency: str = "USD"

    def validated_copy(self) -> "WorkspaceImport":
        return validate_workspace_import(self)


def _text(value: object, *, limit: int = 200, empty: bool = False) -> None:
    if (
        type(value) is not str
        or len(value) > limit
        or "\x00" in value
        or (not empty and not value.strip())
    ):
        raise StoreError("Invalid workspace import text.")


def _records(
    records: object, fields: set[str], limit: int, *, optional: frozenset[str] = frozenset(),
) -> tuple[dict, ...]:
    if type(records) is not tuple or len(records) > limit:
        raise StoreError("Invalid workspace import record collection.")
    result = []
    for record in records:
        if (
            type(record) is not dict or not fields <= record.keys()
            or not record.keys() <= fields | optional
        ):
            raise StoreError("Invalid workspace import record fields.")
        result.append(record.copy())
    return tuple(result)


def _unique_ids(records: tuple[dict, ...]) -> set[str]:
    identifiers = set()
    for record in records:
        _text(record["id"])
        if record["id"] in identifiers:
            raise StoreError("Duplicate workspace import identifier.")
        identifiers.add(record["id"])
    return identifiers


def _booleans(record: dict, fields: tuple[str, ...]) -> None:
    if any(type(record[field]) is not bool for field in fields):
        raise StoreError("Invalid workspace import boolean.")


def validate_workspace_import(data: WorkspaceImport) -> WorkspaceImport:
    """Reject malformed or unbounded payloads and return independent records."""
    if type(data) is not WorkspaceImport:
        raise StoreError("Invalid workspace import.")
    currency_code(data.currency)
    _text(data.budget_start_month, limit=7)
    validate_month(data.budget_start_month)
    accounts = _records(
        data.accounts,
        {"id", "name", "institution", "type", "balance", "source", "stale", "balanceUnknown"},
        1000,
        optional=frozenset({"onBudget", "subtype"}),
    )
    accounts = tuple(normalize_account(account) for account in accounts)
    categories = _records(data.categories, {"id", "name", "group", "target"}, 10000)
    transactions = _records(
        data.transactions,
        {
            "id", "accountId", "date", "merchant", "amount", "categoryId", "pending",
            "reviewed", "source", "note", "isTransfer", "inferredTransfer", "categoryEdited",
            "deleted",
        },
        250000,
    )
    assignments = _records(data.assignments, {"month", "categoryId", "assigned"}, 250000)
    account_ids = _unique_ids(accounts)
    category_ids = _unique_ids(categories)
    _unique_ids(transactions)
    total = 0
    for account in accounts:
        _text(account["name"])
        _text(account["institution"], empty=True)
        _text(account["type"], limit=64)
        _text(account["source"], limit=64)
        _booleans(account, ("stale", "balanceUnknown"))
        total += abs(cents(account["balance"]))
    for category in categories:
        validate_child_category(category)
        _text(category["name"])
        _text(category["group"])
        if cents(category["target"]) < 0:
            raise StoreError("Workspace import category targets cannot be negative.")
        total += category["target"]
    for transaction in transactions:
        _text(transaction["accountId"])
        if transaction["accountId"] not in account_ids:
            raise StoreError("Workspace import account reference not found.")
        category_id = transaction["categoryId"]
        if category_id is not None:
            _text(category_id)
            if category_id not in category_ids:
                raise StoreError("Workspace import category reference not found.")
        _text(transaction["date"], limit=10)
        validate_date(transaction["date"])
        validate_month(transaction["date"][:7])
        _text(transaction["merchant"])
        _text(transaction["source"], limit=64)
        _text(transaction["note"], limit=1000, empty=True)
        _booleans(
            transaction,
            ("pending", "reviewed", "isTransfer", "inferredTransfer", "categoryEdited", "deleted"),
        )
        total += abs(cents(transaction["amount"]))
    assignment_keys = set()
    for assignment in assignments:
        _text(assignment["month"], limit=7)
        validate_month(assignment["month"])
        _text(assignment["categoryId"])
        if assignment["categoryId"] not in category_ids:
            raise StoreError("Workspace import category reference not found.")
        key = (assignment["month"], assignment["categoryId"])
        if key in assignment_keys:
            raise StoreError("Duplicate workspace import assignment.")
        assignment_keys.add(key)
        total += abs(cents(assignment["assigned"]))
    if total > MAX_SAFE_INTEGER:
        raise StoreError("Workspace import totals exceed the supported range.")
    if type(data.warnings) is not tuple or len(data.warnings) > 100:
        raise StoreError("Invalid workspace import warnings.")
    for warning in data.warnings:
        _text(warning, limit=1000)
    return WorkspaceImport(
        accounts, categories, transactions, assignments, data.budget_start_month, data.warnings,
        data.currency,
    )
