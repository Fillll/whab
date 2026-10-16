"""Financial commands over explicit ledger state, without storage or runtime access.

Adapters supply identifiers and the as-of month, then commit the resulting state
atomically. These commands validate references, money and user annotations before
changing state, so the same financial behavior applies to every persistence adapter.
"""

from .accounts import ON_BUDGET_ACCOUNT_TYPES, is_liability, is_on_budget, normalize_account
from .budget import ensure_assignment_allowed
from .categories import ensure_child_category
from .errors import StoreError
from .money import cents, currency_code
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
    """Reclassify existing history without changing balances or transactions."""
    account = state["accounts"].get(account_id)
    if account is None:
        raise StoreError("Account not found.")
    if type(changes) is not dict or not changes or changes.keys() - {"type", "onBudget", "subtype"}:
        raise StoreError("Invalid account classification fields.")
    candidate = {**account, **changes}
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
) -> None:
    """Preserve review-only edits and restore inferred transfers when uncategorized."""
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
