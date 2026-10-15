"""Pure month projections, envelope carryover and conservative cash allocation.

No imported income is added to balance snapshots. Pre-budget history remains
analytics data; stale cash and unknown credit balances cannot create funding.
"""

from .categories import category_tree, ensure_child_category, public_categories
from .dates import shift_month
from .errors import StoreError
from .money import MAX_SAFE_INTEGER, currency_code, currency_minor_units
from .transactions import public_transaction
from .validation import validate_month


def _calculate_currency(state: dict, month: str) -> dict:
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
    category = ensure_child_category(state["categories"], category_id)
    currency = category.get("currency", "USD")
    if month < state["budgetStartMonth"]:
        raise StoreError(
            "This month is before the budget start month. Historical data remains available in analytics."
        )
    previous = state["budget"].get(month, {}).get(category_id, 0)
    as_of = min(month, as_of_month)
    available = calculate_workspace(state, as_of)["summariesByCurrency"][currency]["readyToAssign"]
    future = sum(
        amount
        for m, rows in state["budget"].items()
        if m > as_of
        for key, amount in rows.items()
        if state["categories"][key].get("currency", "USD") == currency
    )
    if assigned > previous and assigned - previous > available - future:
        raise StoreError("Assignment exceeds available money.")


def calculate_workspace(state: dict, month: str) -> dict:
    """Project each currency independently; never add or convert unlike money."""
    validate_month(month)
    accounts = {
        key: {**row, "currency": currency_code(row.get("currency", "USD"))}
        for key, row in state["accounts"].items()
    }
    categories = {row["id"]: row for row in public_categories(state["categories"])}
    currencies = sorted(
        {
            "USD",
            *(a["currency"] for a in accounts.values()),
            *(c["currency"] for c in categories.values()),
        }
    )
    for rows in state["budget"].values():
        for category_id in rows:
            ensure_child_category(categories, category_id)
    transactions = {}
    for key, row in state["transactions"].items():
        account = accounts.get(row["accountId"])
        if account is None:
            raise StoreError("Transaction account not found.")
        category_id = row.get("categoryId")
        if category_id is not None:
            category = ensure_child_category(categories, category_id)
            if category["currency"] != account["currency"]:
                raise StoreError("Category currency must match the account currency.")
        transactions[key] = {**row, "currency": account["currency"]}
    projections = {}
    for currency in currencies:
        children = {key: c for key, c in categories.items() if c["currency"] == currency}
        scoped = {
            **state,
            "accounts": {key: a for key, a in accounts.items() if a["currency"] == currency},
            "categories": children,
            "transactions": {
                key: t for key, t in transactions.items() if t["currency"] == currency
            },
            "budget": {
                m: {key: amount for key, amount in rows.items() if key in children}
                for m, rows in state["budget"].items()
            },
        }
        projections[currency] = _calculate_currency(scoped, month)
    default = projections["USD"]
    return {
        **default,
        "currency": "USD",
        "currencyMinorUnits": 2,
        "currencies": currencies,
        "accounts": list(accounts.values()),
        "categories": list(categories.values()),
        "categoryGroups": category_tree(categories),
        "transactions": sorted(
            [t for p in projections.values() for t in p["transactions"]],
            key=lambda t: (t["date"], t["id"]),
            reverse=True,
        ),
        "budget": [
            {**b, "currency": currency} for currency, p in projections.items() for b in p["budget"]
        ],
        "summariesByCurrency": {c: p["summary"] for c, p in projections.items()},
        "trendsByCurrency": {c: p["trend"] for c, p in projections.items()},
    }


def select_currency(data: dict, currency: str = "USD") -> dict:
    """A homogeneous public view for interfaces and immutable plugin snapshots."""
    currency = currency_code(currency)
    categories = [c for c in data["categories"] if c.get("currency", "USD") == currency]
    accounts = [a for a in data["accounts"] if a.get("currency", "USD") == currency]
    account_ids = {a["id"] for a in accounts}
    category_ids = {c["id"] for c in categories}
    empty_summary = dict.fromkeys(("income", "spent", "assigned", "available", "readyToAssign"), 0)
    return {
        **data,
        "currency": currency,
        "currencyMinorUnits": currency_minor_units(currency),
        "currencies": sorted({*data.get("currencies", ["USD"]), currency}),
        "accounts": accounts,
        "categories": categories,
        "categoryGroups": category_tree(categories),
        "transactions": [t for t in data["transactions"] if t["accountId"] in account_ids],
        "budget": [b for b in data["budget"] if b["categoryId"] in category_ids],
        "summary": data.get("summariesByCurrency", {"USD": data["summary"]}).get(
            currency, empty_summary
        ),
        "trend": data.get("trendsByCurrency", {"USD": data["trend"]}).get(
            currency, [{"month": row["month"], "income": 0, "spent": 0} for row in data["trend"]]
        ),
    }
