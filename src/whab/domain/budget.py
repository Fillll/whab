"""Pure month projections, envelope carryover and conservative cash allocation.

No imported income is added to balance snapshots. Pre-budget history remains
analytics data; stale cash and unknown credit balances cannot create funding.
"""

from .dates import shift_month
from .errors import StoreError
from .money import MAX_SAFE_INTEGER
from .transactions import public_transaction
from .validation import validate_month


def calculate_workspace(state: dict, month: str) -> dict:
    validate_month(month)
    transactions = [
        public_transaction(t) for t in state["transactions"].values() if not t.get("deleted")
    ]
    transactions.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    cleared = [t for t in transactions if not t["pending"] and not t["isTransfer"]]
    accounts = list(state["accounts"].values())

    def income_for(rows):
        return -sum(
            t["amount"]
            for t in rows
            if t["amount"] < 0
            and t["categoryId"] is None
            and state["accounts"].get(t["accountId"], {}).get("type") != "credit"
        )

    def spend_for(rows):
        return sum(t["amount"] for t in rows if t["amount"] > 0 or t["categoryId"] is not None)

    start = state["budgetStartMonth"]
    active = [t for t in cleared if t["date"].startswith(month)]
    prior = [t for t in cleared if start <= t["date"][:7] < month]
    budget = []
    for category in state["categories"].values():
        category_id = category["id"]
        assigned = state["budget"].get(month, {}).get(category_id, 0) if month >= start else 0
        carryover = sum(
            rows.get(category_id, 0) for m, rows in state["budget"].items() if start <= m < month
        ) - sum(t["amount"] for t in prior if t["categoryId"] == category_id)
        spent = (
            sum(t["amount"] for t in active if t["categoryId"] == category_id)
            if month >= start
            else 0
        )
        budget.append(
            {
                "categoryId": category_id,
                "assigned": assigned,
                "spent": spent,
                "carryover": carryover,
                "available": carryover + assigned - spent,
                "target": category["target"],
            }
        )
    later = [t for t in transactions if not t["pending"] and t["date"][:7] > month]

    def balance_at_month(account):
        return account["balance"] + (-1 if account["type"] == "credit" else 1) * sum(
            t["amount"] for t in later if t["accountId"] == account["id"]
        )

    cash = sum(
        balance_at_month(a) for a in accounts if a["type"] == "depository" and not a.get("stale")
    )
    debt = sum(max(0, balance_at_month(a)) for a in accounts if a["type"] == "credit")
    ready = cash - debt - sum(max(0, row["available"]) for row in budget)
    if any(a.get("balanceUnknown") and a["type"] == "credit" for a in accounts):
        ready = min(0, ready)
    summary = {
        "income": income_for(active),
        "spent": spend_for(active),
        "assigned": sum(b["assigned"] for b in budget),
        "available": sum(b["available"] for b in budget),
        "readyToAssign": ready,
    }
    if any(abs(value) > MAX_SAFE_INTEGER for value in summary.values()):
        raise StoreError("Ledger totals exceed the supported range.")
    trend = []
    for offset in range(-5, 1):
        m = shift_month(month, offset)
        rows = [t for t in cleared if t["date"].startswith(m)]
        trend.append({"month": m, "income": income_for(rows), "spent": spend_for(rows)})
    return {
        "month": month,
        "budgetStartMonth": start,
        "accounts": accounts,
        "categories": list(state["categories"].values()),
        "transactions": [t for t in transactions if t["date"].startswith(month)],
        "budget": budget,
        "summary": summary,
        "trend": trend,
    }


def ensure_assignment_allowed(
    state: dict, category_id: str, month: str, assigned: int, as_of_month: str
) -> None:
    if category_id not in state["categories"]:
        raise StoreError("Category not found.")
    if month < state["budgetStartMonth"]:
        raise StoreError(
            "This month is before the budget start month. Historical data remains available in analytics."
        )
    previous = state["budget"].get(month, {}).get(category_id, 0)
    as_of = min(month, as_of_month)
    available = calculate_workspace(state, as_of)["summary"]["readyToAssign"]
    future = sum(sum(rows.values()) for m, rows in state["budget"].items() if m > as_of)
    if assigned > previous and assigned - previous > available - future:
        raise StoreError("Assignment exceeds available money.")
