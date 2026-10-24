"""Backend-neutral construction and transfer of plain validated ledger state.

Persistence adapters own locks and commit boundaries. These helpers perform no
I/O and carry no host, plugin, transport, encryption-key or database dependency.
"""

from . import commands
from .accounts import normalize_account
from .dates import shift_month
from .errors import StoreError
from .portable import ACCOUNT_FIELDS, validate_portable_budget

CATEGORY_TEMPLATE = (
    ("rent", "Rent", "Home", 260000),
    ("groceries", "Groceries", "Everyday", 85000),
    ("dining", "Dining", "Everyday", 35000),
    ("transport", "Transport", "Everyday", 25000),
    ("utilities", "Utilities", "Home", 30000),
    ("health", "Health", "Life", 20000),
    ("fun", "Fun", "Life", 20000),
    ("travel", "Travel", "Goals", 50000),
    ("shopping", "Shopping", "Life", 25000),
)


def initial_state(workspace, month):
    """A pristine personal ledger or a small fictional demonstration ledger."""
    state = {
        "_id": workspace, "budgetStartMonth": month, "accounts": {}, "categories": {
            identifier: {"id": identifier, "name": name, "group": group, "target": target, "currency": "USD"}
            for identifier, name, group, target in CATEGORY_TEMPLATE
        }, "transactions": {}, "budget": {}, "futureFunding": {}, "rolloverModes": {},
        "items": {}, "receipts": {}, "importReceipt": None,
    }
    if workspace == "demo":
        state["budgetStartMonth"] = max("1900-01", shift_month(month, -1))
        commands.add_account(state, "demo-checking", {
            "name": "Fictional checking", "type": "depository", "balance": 400000,
        })
        state["accounts"]["demo-checking"].update(source="demo", institution="Fictional Bank")
        commands.add_transaction(state, "demo-groceries", {
            "accountId": "demo-checking", "date": f"{month}-01", "merchant": "Fictional market",
            "amount": 6425, "categoryId": "groceries",
        }, "demo")
        commands.assign(state, "groceries", month, 85000, as_of_month=month)
    return state


def portable_snapshot(state, *, validate=True):
    """Allowlist history, excluding bank records and associations.

    Native adapters may use ``validate=False`` only to hash already-trusted
    native state that exceeds portable transfer limits. Export paths validate.
    """
    snapshot = {
        "formatVersion": 1, "budgetStartMonth": state["budgetStartMonth"],
        "accounts": [
            {**{key: account[key] for key in ACCOUNT_FIELDS},
             **{key: account[key] for key in ("bankWebsite", "nameOverridden") if key in account}}
            for record in state["accounts"].values()
            for account in (normalize_account({"balanceUnknown": False, "stale": False, **record}),)
        ],
        "categories": list(state["categories"].values()),
        "transactions": list(state["transactions"].values()),
        "assignments": [
            {"month": month, "categoryId": category, "assigned": assigned}
            for month, rows in state["budget"].items() for category, assigned in rows.items()
        ],
        "futureFunding": [
            {"month": month, "currency": currency, "assigned": assigned}
            for month, rows in state.get("futureFunding", {}).items() for currency, assigned in rows.items()
        ],
        "rolloverModes": [
            {"month": month, "currency": currency, "mode": mode}
            for month, rows in state.get("rolloverModes", {}).items() for currency, mode in rows.items()
        ],
    }
    return validate_portable_budget(snapshot) if validate else snapshot


def require_pristine(state):
    """A transfer may replace untouched templates, never saved user/provider data."""
    if any(state.get(key) for key in (
        "accounts", "transactions", "budget", "futureFunding", "rolloverModes", "items",
        "receipts", "importReceipt",
    )):
        raise StoreError("Restoration requires an empty personal workspace.")
    template = initial_state("personal", state["budgetStartMonth"])["categories"]
    if state["categories"] and state["categories"] != template:
        raise StoreError("Restoration requires untouched template categories.")


def restore_snapshot(state, snapshot, *, mark_external_stale=True):
    """Populate plain state from validated neutral records, with independent maps."""
    restored = validate_portable_budget(snapshot)
    state["budgetStartMonth"] = restored["budgetStartMonth"]
    state["accounts"] = {
        record["id"]: {
            **record, "stale": record["stale"] or (
                mark_external_stale and record["source"] not in {"manual", "demo"}
            ),
        }
        for record in restored["accounts"]
    }
    state["categories"] = {record["id"]: record for record in restored["categories"]}
    state["transactions"] = {record["id"]: record for record in restored["transactions"]}
    for name, key, value in (
        ("assignments", "categoryId", "assigned"),
        ("futureFunding", "currency", "assigned"),
        ("rolloverModes", "currency", "mode"),
    ):
        target = "budget" if name == "assignments" else name
        state[target] = {}
        for record in restored[name]:
            state[target].setdefault(record["month"], {})[record[key]] = record[value]
