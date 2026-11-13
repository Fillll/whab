"""Stable financial classifications and the current-state review invariant.

No provider, persistence or UI dependency. Historical snapshots remain immutable;
callers commit this compatible correction with their native audit boundary.
"""

from .errors import StoreError
from .money import currency_code

PREFIX = "whab-system:"
KINDS = {"transfer": "Transfer", "income": "Income"}


def system_category(kind, currency="USD"):
    if kind not in KINDS:
        raise StoreError("Unknown system category kind.")
    currency = currency_code(currency)
    return {"id": f"{PREFIX}{kind}:{currency}", "name": KINDS[kind],
            "group": "System", "target": 0, "currency": currency, "systemKind": kind}


def system_kind(category):
    return category.get("systemKind") if category else None


def protect_system_category(category):
    if system_kind(category):
        raise StoreError("System categories cannot be renamed, moved, deleted, targeted or assigned money.")


def ensure_system_categories(state):
    """Install real records per account/category currency; never adopt display names."""
    currencies = {"USD", *(a.get("currency", "USD") for a in state["accounts"].values()),
                  *(c.get("currency", "USD") for c in state["categories"].values())}
    changed = False
    for currency in sorted(currencies):
        for kind in KINDS:
            category = system_category(kind, currency)
            previous = state["categories"].get(category["id"])
            if previous is not None and previous != category:
                raise StoreError("Reserved system category identity conflicts with saved data. Preserve the budget for recovery.")
            if previous is None:
                state["categories"][category["id"]] = category
                changed = True
    return changed


def normalize_transaction(state, transaction, *, classify_transfers=False):
    """Correct only authoritative transfer classification and invalid review flags.

    An amount sign or merchant label is never evidence for Income. Existing
    categorized refunds and explicit user overrides retain their classification.
    """
    changed = False
    if transaction.get("deleted"):
        return changed
    if classify_transfers and transaction.get("categoryId") is None and transaction.get("isTransfer"):
        currency = state["accounts"][transaction["accountId"]].get("currency", "USD")
        transaction["categoryId"] = system_category("transfer", currency)["id"]
        changed = True
    if system_kind(state["categories"].get(transaction.get("categoryId"))) == "transfer" and not transaction.get("isTransfer"):
        transaction["isTransfer"] = True
        changed = True
    if transaction.get("categoryId") is None and transaction.get("reviewed"):
        transaction["reviewed"] = False
        changed = True
    return changed


def migrate_classifications(state):
    """Idempotent current-state correction; adapters own one durable audit commit."""
    legacy = system_category("transfer")["id"] not in state["categories"]
    changed = ensure_system_categories(state)
    for transaction in state["transactions"].values():
        changed = normalize_transaction(state, transaction, classify_transfers=legacy) or changed
    return changed


def validate_review(category_id, reviewed):
    if reviewed is True and category_id is None:
        raise StoreError("Categorize this transaction before marking it reviewed. Uncategorized transactions must remain non-reviewed.")


def classification_migration_required(state):
    """Read-only readiness test; a writable primary owns any required correction."""
    categories = state['categories']
    currencies = {"USD", *(a.get("currency", "USD") for a in state['accounts'].values()),
                  *(c.get("currency", "USD") for c in categories.values())}
    for currency in currencies:
        for kind in KINDS:
            expected = system_category(kind, currency)
            previous = categories.get(expected['id'])
            if previous is None:
                return True
            if previous != expected:
                raise StoreError("Reserved system category identity conflicts with saved data. Preserve the budget for recovery.")
    return any(not row.get('deleted') and (
        row.get('categoryId') is None and row.get('reviewed')
        or system_kind(categories.get(row.get('categoryId'))) == 'transfer' and not row.get('isTransfer')
    ) for row in state['transactions'].values())
