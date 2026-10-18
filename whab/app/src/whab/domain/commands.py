"""Financial commands over explicit ledger state, without storage or runtime access.

Adapters supply identifiers and the as-of month, then commit the resulting state
atomically. These commands validate references, money and user annotations before
changing state, so the same financial behavior applies to every persistence adapter.
"""

from .accounts import (
    ON_BUDGET_ACCOUNT_TYPES,
    bank_website_entry,
    is_liability,
    is_on_budget,
    normalize_account,
)
from .budget import (
    ROLLOVER_MODES,
    calculate_workspace,
    effective_month_funding,
    ensure_assignment_allowed,
    ensure_funding_capacity_preserved,
    ensure_projection_totals_supported,
)
from .categories import ensure_child_category
from .dates import shift_month
from .errors import StoreError
from .money import cents, currency_code, minor_units
from .transactions import UNSET
from .validation import validate_date, validate_label, validate_month


def add_account(state: dict, account_id: str, account: dict) -> str:
    """Add a manual budget or tracking account using an adapter-supplied identifier."""
    name = validate_label(account["name"])
    kind, balance = account["type"], cents(account["balance"])
    currency = currency_code(account.get("currency", "USD"))
    state["accounts"][account_id] = normalize_account({
        "id": account_id,
        "name": name,
        "institution": "Manual account",
        "type": kind,
        **{key: account[key] for key in ("onBudget", "subtype") if key in account},
        "balance": balance,
        "currency": currency,
        "source": "manual",
        "stale": False,
    })
    return account_id


def update_account(state: dict, account_id: str, changes: dict) -> None:
    """Edit account metadata without silently converting existing transaction history."""
    account = state["accounts"].get(account_id)
    if account is None:
        raise StoreError("Account not found.")
    if type(changes) is not dict or not changes or changes.keys() - {"type", "onBudget", "subtype", "name", "bankWebsite", "currency"}:
        raise StoreError("Invalid account classification fields.")
    candidate = {**account, **changes}
    if "name" in changes:
        candidate["name"] = validate_label(changes["name"])
        candidate["nameOverridden"] = True
    if "bankWebsite" in changes:
        candidate["bankWebsite"] = bank_website_entry(changes["bankWebsite"])
    if "currency" in changes:
        candidate["currency"] = currency_code(changes["currency"])
        if candidate["currency"] != account.get("currency", "USD") and any(
            row["accountId"] == account_id for row in state["transactions"].values()
        ):
            raise StoreError("Currency can only change before an account has transaction history.")
    if "type" in changes and changes["type"] != account["type"]:
        if "subtype" not in changes:
            candidate["subtype"] = None
        if "onBudget" not in changes:
            candidate["onBudget"] = (
                is_on_budget(account)
                if type(changes["type"]) is str and changes["type"] in ON_BUDGET_ACCOUNT_TYPES
                else False
            )
    state["accounts"][account_id] = normalize_account(candidate)


def add_transaction(
    state: dict, transaction_id: str, transaction: dict, source: str = "manual",
) -> str:
    """Apply a validated entry and its account balance change together."""
    amount = cents(transaction["amount"])
    validate_date(transaction["date"])
    merchant = validate_label(transaction["merchant"])
    note = transaction.get("note", "")
    if not isinstance(note, str) or len(note) > 1000:
        raise StoreError("Invalid note.")
    account = state["accounts"].get(transaction["accountId"])
    if not account:
        raise StoreError("Account not found.")
    if source == "manual" and account["source"] not in {"manual", "demo"}:
        raise StoreError(
            "Use a manual account for manual entries; bank imports cannot yet reconcile them."
        )
    category = transaction.get("categoryId")
    if category is not None:
        child = ensure_child_category(state["categories"], category)
        if child.get("currency", "USD") != account.get("currency", "USD"):
            raise StoreError("Category currency must match the account currency.")
    if transaction_id in state["transactions"]:
        raise StoreError("Transaction already exists.")
    balance = cents(account["balance"] + (amount if is_liability(account) else -amount))
    state["transactions"][transaction_id] = {
        "id": transaction_id,
        "accountId": account["id"],
        "date": transaction["date"],
        "merchant": merchant,
        "merchantLogo": None,
        "amount": amount,
        "categoryId": category,
        "pending": False,
        "reviewed": source == "demo",
        "source": source,
        "note": note,
        "isTransfer": False,
        "inferredTransfer": False,
        "categoryEdited": source == "manual",
        "deleted": False,
    }
    account["balance"] = balance
    return transaction_id


def edit_transaction(
    state: dict, transaction_id: str, category_id: object = UNSET, reviewed: bool | None = None,
    note: object = UNSET,
) -> None:
    """Validate annotations together before changing category, review status or memo."""
    transaction = state["transactions"].get(transaction_id)
    if not transaction or transaction.get("deleted"):
        raise StoreError("Transaction not found.")
    if category_id is not UNSET and category_id is not None:
        child = ensure_child_category(state["categories"], category_id)
        account = state["accounts"][transaction["accountId"]]
        if child.get("currency", "USD") != account.get("currency", "USD"):
            raise StoreError("Category currency must match the account currency.")
    if reviewed is not None and not isinstance(reviewed, bool):
        raise StoreError("Invalid review status.")
    if note is not UNSET and (not isinstance(note, str) or len(note) > 1000):
        raise StoreError("Invalid note.")
    if category_id is not UNSET:
        transaction.update(
            categoryId=category_id,
            categoryEdited=True,
            isTransfer=False
            if category_id is not None
            else transaction.get("inferredTransfer", False),
        )
    if reviewed is not None:
        transaction["reviewed"] = reviewed
    if note is not UNSET:
        transaction["note"] = note


def assign(
    state: dict, category_id: str, month: str, assigned: int, *, as_of_month: str,
) -> None:
    """Assign one category's currency using explicit funding and calendar context."""
    validate_month(month)
    cents(assigned)
    if assigned < 0:
        raise StoreError("Assignment cannot be negative.")
    ensure_assignment_allowed(state, category_id, month, assigned, as_of_month)
    state["budget"].setdefault(month, {})[category_id] = assigned


def assign_next_month(
    state: dict, month: str, currency: str, assigned: int, *, as_of_month: str,
) -> None:
    """Set aside this month's money for the next month, independently of its plans."""
    validate_month(month)
    validate_month(as_of_month)
    currency = currency_code(currency)
    minor_units(assigned, currency)
    if assigned < 0:
        raise StoreError("Next-month funding cannot be negative.")
    if month < state["budgetStartMonth"]:
        raise StoreError("This month is before the budget start month.")
    if month >= "2200-12":
        raise StoreError("Next-month funding is unavailable after November 2200.")
    target = shift_month(month, 1)
    previous_floor = effective_month_funding(state, target, currency)
    summary = calculate_workspace(state, month)["summariesByCurrency"].get(currency, {})
    available = summary.get("fundingCapacity", 0)
    if assigned > previous_floor and assigned - previous_floor > available:
        detail = (
            " Review overspending, uncategorized spending, transfers, or account reserves."
            if available < summary.get("readyToAssign", 0) else ""
        )
        raise StoreError(f"Assignment exceeds available money.{detail}")
    if assigned > previous_floor:
        candidate = {
            **state,
            "futureFunding": {
                **state.get("futureFunding", {}),
                target: {**state.get("futureFunding", {}).get(target, {}), currency: assigned},
            },
        }
        ensure_funding_capacity_preserved(state, candidate, month, as_of_month, currency)
    state.setdefault("futureFunding", {}).setdefault(target, {})[currency] = assigned


def set_rollover_mode(
    state: dict, month: str, currency: str, mode: str, *, as_of_month: str,
) -> None:
    """Choose a month's outgoing boundary, retaining any resulting deficits."""
    validate_month(month)
    validate_month(as_of_month)
    currency = currency_code(currency)
    if not state["budgetStartMonth"] <= month < "2200-12":
        raise StoreError("Rollover requires a supported source month from the budget start.")
    if type(mode) is not str or mode not in ROLLOVER_MODES:
        raise StoreError("Rollover mode must be category or balance.")
    candidate = {
        **state,
        "rolloverModes": {
            **state.get("rolloverModes", {}),
            month: {**state.get("rolloverModes", {}).get(month, {}), currency: mode},
        },
    }
    ensure_projection_totals_supported(state, candidate, month, as_of_month)
    state.setdefault("rolloverModes", {}).setdefault(month, {})[currency] = mode
