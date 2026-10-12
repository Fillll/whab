"""Readable view helpers. Budget calculations remain in Store, not in templates."""

from collections import defaultdict
from datetime import date, datetime
from urllib.parse import urlencode

from .icons import category_icon, icon
from .store import today


def money(cents, digits=2):
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.{digits}f}"


def month_label(month, short=False):
    return datetime.strptime(month, "%Y-%m").strftime("%b %Y" if short else "%B %Y")


def shift_month(month, step):
    year, number = map(int, month.split("-"))
    index = year * 12 + number - 1 + step
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def build_context(data, status, items, *, workspace, page, month, selected=None):
    categories = data["categories"]
    budgets = {row["categoryId"]: row for row in data["budget"]}
    selected = next((c for c in categories if c["id"] == selected), None) or next(
        (c for c in categories if c["name"].lower() == "groceries"),
        categories[0] if categories else None,
    )
    groups = []
    for group in dict.fromkeys(c["group"] for c in categories):
        rows = [{"category": c, **budgets[c["id"]]} for c in categories if c["group"] == group]
        groups.append(
            {
                "name": group,
                "rows": rows,
                **{
                    field: sum(r[field] for r in rows)
                    for field in ("assigned", "spent", "available")
                },
            }
        )
    transactions = sorted(data["transactions"], key=lambda t: t["date"], reverse=True)
    spending = defaultdict(int)
    payees = defaultdict(int)
    purchases = 0
    uncategorized = 0
    for transaction in transactions:
        if transaction["pending"] or transaction.get("isTransfer"):
            continue
        if transaction["amount"] > 0:
            purchases += 1
        if transaction["categoryId"]:
            spending[transaction["categoryId"]] += transaction["amount"]
        if transaction["amount"] > 0 or transaction["categoryId"]:
            payees[transaction["merchant"]] += transaction["amount"]
        if transaction["amount"] > 0 and not transaction["categoryId"]:
            uncategorized += transaction["amount"]
    spending_rows = [
        {"category": c, "spent": spending[c["id"]]} for c in categories if spending[c["id"]] > 0
    ]
    spending_rows.sort(key=lambda row: row["spent"], reverse=True)
    previous = next((t for t in data["trend"] if t["month"] == shift_month(month, -1)), None)
    change = (
        (data["summary"]["spent"] - previous["spent"]) / previous["spent"] * 100
        if previous and previous["spent"] > 0
        else None
    )
    trend = data["trend"]
    maximum = max(10000, *(t["spent"] for t in trend)) * 1.2
    points = [
        {
            "x": 64 + i * 800 / max(1, len(trend) - 1),
            "y": 192 - max(0, row["spent"]) / maximum * 170,
            **row,
        }
        for i, row in enumerate(trend)
    ]
    chart_path = " ".join(
        f"{'M' if i == 0 else 'L'}{p['x']:.2f},{p['y']:.2f}" for i, p in enumerate(points)
    )
    nav = [
        ("budget", "Budget"),
        ("transactions", "Transactions"),
        ("insights", "Insights"),
        ("accounts", "Accounts"),
    ]

    def url(next_page=page, **overrides):
        values = {"page": next_page, "workspace": workspace, "month": month, **overrides}
        return "/?" + urlencode({key: value for key, value in values.items() if value is not None})

    return {
        "data": data,
        "status": status,
        "items": items,
        "workspace": workspace,
        "page": page,
        "month": month,
        "nav": nav,
        "url": url,
        "money": money,
        "month_label": month_label,
        "shift_month": shift_month,
        "icon": icon,
        "category_icon": category_icon,
        "groups": groups,
        "selected": selected,
        "selected_budget": budgets.get(selected["id"]) if selected else None,
        "transactions": transactions,
        "accounts_by_id": {a["id"]: a for a in data["accounts"]},
        "manual_accounts": [a for a in data["accounts"] if a["source"] != "plaid"],
        "to_review": sum(not t["reviewed"] and not t["pending"] for t in transactions),
        "cash_balance": sum(
            a["balance"]
            for a in data["accounts"]
            if a["type"] == "depository" and not a.get("stale") and not a.get("balanceUnknown")
        ),
        "spending": spending_rows,
        "payees": sorted(
            ((name, amount) for name, amount in payees.items() if amount > 0),
            key=lambda row: row[1],
            reverse=True,
        )[:5],
        "purchases": purchases,
        "change": change,
        "points": points,
        "chart_path": chart_path,
        "chart_max": maximum,
        "uncategorized": uncategorized,
        "today": today(),
        "client_context": {"workspace": workspace, "page": page, "month": month},
        "date_label": lambda value: date.fromisoformat(value).strftime("%b %-d"),
    }
