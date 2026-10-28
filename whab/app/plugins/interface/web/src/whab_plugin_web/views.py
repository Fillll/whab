"""Web presentation helpers over already-calculated public ledger views."""

from collections import defaultdict
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import quote

from whab import __version__
from whab.domain.dates import shift_month as period_shift
from whab.domain.dates import today
from whab.plugins import (
    ACCOUNT_SUBTYPES,
    ACCOUNT_TYPES,
    ON_BUDGET_ACCOUNT_TYPES,
    SUPPORTED_CURRENCIES,
    currency_code,
    currency_minor_units,
    format_date,
    format_money,
    is_liability,
    is_on_budget,
)

from .icons import category_icon, icon

WEB_ASSET_VERSION = package_version("whab_plugin_web")
WEB_MODULES = tuple(path.name for path in (Path(__file__).parent / "static").glob("*.js"))
TRANSACTION_PAGE_SIZE = 100


def module_imports(asset_prefix):
    """Give transitive ES modules the same upgrade boundary as their entry point."""
    return {
        "imports": {
            f"{asset_prefix}/static/{name}": f"{asset_prefix}/static/{name}?v={WEB_ASSET_VERSION}"
            for name in WEB_MODULES
        }
    }


def transaction_window(transactions, request, projection=None):
    """Bound register markup after searching the complete account/date selection."""
    params = request.query_params
    search = params.get("q", "")
    requested_page = params.get("tx_page", "1")
    if projection is None:
        query = search.strip().casefold()
        review = params.get("review", "all")
        matches = [
            row for row in transactions
            if (not query or query in f"{row['merchant']} {row['note']}".casefold())
            and (review != "needs-review" or not row["reviewed"])
            and (review != "reviewed" or row["reviewed"])
        ]
        pages = max(1, (len(matches) + TRANSACTION_PAGE_SIZE - 1) // TRANSACTION_PAGE_SIZE)
        try:
            page = max(1, min(int(requested_page), pages))
        except ValueError:
            page = 1
        offset = (page - 1) * TRANSACTION_PAGE_SIZE
        projection = {
            "total": len(transactions), "count": len(matches),
            "start": offset + 1 if matches else 0,
            "end": min(offset + TRANSACTION_PAGE_SIZE, len(matches)),
            "page": page, "pages": pages,
        }
        transactions = matches[offset:offset + TRANSACTION_PAGE_SIZE]
    page, pages = projection["page"], projection["pages"]
    return {
        **projection, "rows": transactions, "requested_page": requested_page,
        "search": search,
        "previous": str(request.url.include_query_params(tx_page=page - 1)) if page > 1 else None,
        "next": str(request.url.include_query_params(tx_page=page + 1)) if page < pages else None,
        "search_params": [(key, value) for key, value in params.multi_items() if key not in {"q", "tx_page"}],
    }


def valid_money(value):
    """Accept exact public-view amounts within the browser's safe integer range."""
    return type(value) is int and -9007199254740991 <= value <= 9007199254740991


def _carryover_total(rows):
    values = [row.get("carryover", 0) for row in rows]
    if not all(valid_money(value) for value in values):
        return None
    total = sum(values)
    return total if valid_money(total) else None


def _carried_in(period, budget_start_month):
    """Present the core's assignable carry-in without pooling category balances here."""
    if period.get("monthlyFundingFlow") is not True or (
        budget_start_month is not None and period["month"] < budget_start_month
    ):
        return None, []
    summary = period["summary"]
    amount = summary.get("carriedIn")
    if not valid_money(amount):
        return None, []
    mode = period.get("incomingRolloverMode")
    if mode not in ("category", "balance") and period["month"] != budget_start_month:
        return amount, []
    parts = [
        (
            "Opening unassigned money" if period["month"] == budget_start_month
            else "Previously unassigned money",
            summary.get("carriedInUnassigned"),
        ),
        ("Set aside for this month", summary.get("carriedInSetAside")),
    ]
    if mode == "balance":
        parts.append(("Overall category balance", summary.get("balanceCarryover")))
    return amount, parts if all(valid_money(value) for _, value in parts) else []


def money_input(value, currency="USD"):
    """Render exact minor units for an ungrouped amount input."""
    digits = currency_minor_units(currency)
    whole, fraction = divmod(abs(value), 10**digits)
    sign = "-" if value < 0 else ""
    return f"{sign}{whole}.{fraction:0{digits}d}" if digits else f"{sign}{whole}"


def money_number(value, currency="USD"):
    """Display exact, grouped minor units beneath a shared currency heading."""
    digits = currency_minor_units(currency)
    whole, fraction = divmod(abs(value), 10**digits)
    sign = "-" if value < 0 else ""
    return f"{sign}{whole:,}.{fraction:0{digits}d}" if digits else f"{sign}{whole:,}"


def sidebar_money(value, currency="USD"):
    """Round sidebar balances to whole major units, half away from zero."""
    unit = 10**currency_minor_units(currency)
    whole = (abs(value) + unit // 2) // unit
    sign = "-" if value < 0 and whole else ""
    prefix = "$" if currency == "USD" else f"{currency} "
    return f"{sign}{prefix}{whole:,}"


def month_label(month, short=False):
    """The web skin gives core ISO periods readable month names."""
    names = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    year, number = month.split("-")
    name = names[int(number) - 1]
    return name[:3] if short else f"{name} {year}"


def shift_month(month, step):
    """Keep skin navigation links within the core's supported calendar."""
    return min("2200-12", max("1900-01", period_shift(month, step)))


def uncategorized_spending(data):
    """Sum the displayed cleared purchases that have no category to review."""
    accounts = {account["id"]: account for account in data["accounts"]}
    return sum(
        transaction["amount"]
        for transaction in data["transactions"]
        if not transaction["pending"] and not transaction.get("isTransfer")
        and transaction["amount"] > 0 and not transaction["categoryId"]
        and (
            transaction.get("accountId") not in accounts
            or is_on_budget(accounts[transaction["accountId"]])
        )
    )


def budget_month_count(value):
    """A malformed or unsupported view range retains the three-month default."""
    return int(value) if value in ("1", "2", "3", "4", "5") else 3


def build_context(data, status, items, *, workspace, page, month, selected=None):
    categories = data["categories"]
    all_accounts = data.get("allAccounts", data["accounts"])
    currency = currency_code(data.get("currency", "USD"))
    budgets = {row["categoryId"]: row for row in data.get("budget", [])}
    selected = next((c for c in categories if c["id"] == selected), None)
    periods = [
        {
            **period, "month": period.get("month", month),
            "uncategorized": uncategorized_spending(period),
            "show_carryover": period.get("incomingRolloverMode") == "category",
            "column_count": 4 if period.get("incomingRolloverMode") == "category" else 3,
            "carryover": _carryover_total(period["budget"]),
        }
        for period in data.get("periods", [] if page == "transactions" else [data])
    ]
    for period in periods:
        period["carried_in"], period["carried_in_parts"] = _carried_in(
            period, data.get("budgetStartMonth")
        )
    period_budgets = [
        {row["categoryId"]: row for row in period["budget"]} for period in periods
    ]
    category_groups = data.get("categoryGroups")
    if category_groups is None:
        category_groups = [
            {
                "name": name,
                "children": [c for c in categories if c["group"] == name],
            }
            for name in dict.fromkeys(c["group"] for c in categories)
        ]
    groups = []
    category_names = {}
    category_parents = {}
    for group in category_groups:
        rows = [
            {
                "category": c, **budgets.get(c["id"], {}),
                "periods": [
                    {"month": period["month"], **projection[c["id"]]}
                    for period, projection in zip(periods, period_budgets, strict=True)
                ],
            }
            for c in group["children"]
        ]
        for child in group["children"]:
            category_names[child["id"]] = f"{group['name']} / {child['name']}"
            category_parents[child["id"]] = group["name"]
        groups.append(
            {
                **group,
                "rows": rows,
                **{
                    field: (
                        _carryover_total(rows) if field == "carryover"
                        else sum(r.get(field, 0) for r in rows)
                    )
                    for field in ("carryover", "assigned", "spent", "available")
                },
                "periods": [
                    {
                        "month": period["month"],
                        **{
                            field: (
                                _carryover_total([row["periods"][index] for row in rows])
                                if field == "carryover"
                                else sum(row["periods"][index].get(field, 0) for row in rows)
                            )
                            for field in ("carryover", "assigned", "spent", "available")
                        },
                    }
                    for index, period in enumerate(periods)
                ],
            }
        )
    transactions = sorted(data["transactions"], key=lambda t: t["date"], reverse=True)
    accounts_by_id = {a["id"]: a for a in data["accounts"]}
    spending = defaultdict(int)
    payees = defaultdict(int)
    purchases = 0
    for transaction in transactions:
        account = accounts_by_id.get(transaction.get("accountId"))
        if (
            transaction["pending"] or transaction.get("isTransfer")
            or (account is not None and not is_on_budget(account))
        ):
            continue
        if transaction["amount"] > 0:
            purchases += 1
        if transaction["categoryId"]:
            spending[transaction["categoryId"]] += transaction["amount"]
        if transaction["amount"] > 0 or transaction["categoryId"]:
            payees[transaction["merchant"]] += transaction["amount"]
    spending_rows = [
        {"category": c, "spent": spending[c["id"]]} for c in categories if spending[c["id"]] > 0
    ]
    spending_rows.sort(key=lambda row: row["spent"], reverse=True)
    previous = next((t for t in data.get("trend", []) if t["month"] == period_shift(month, -1)), None)
    change = (
        (data["summary"]["spent"] - previous["spent"]) / previous["spent"] * 100
        if previous and previous["spent"] > 0
        else None
    )
    trend = data.get("trend", [])
    minimum = 100 * 10**currency_minor_units(currency)
    maximum = (max([minimum, *(t["spent"] for t in trend)]) * 6 + 4) // 5
    points = [
        {
            "x": 112 + i * 752 / max(1, len(trend) - 1),
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

    return {
        "version": __version__,
        "web_asset_version": WEB_ASSET_VERSION,
        "module_imports": module_imports,
        "data": data,
        "status": status,
        "items": items,
        "workspace": workspace,
        "page": page,
        "month": month,
        "nav": nav,
        "currency": currency,
        "currencies": data.get("currencies", [currency]),
        "currency_choices": [(code, currency_minor_units(code)) for code in SUPPORTED_CURRENCIES],
        "minor_units": currency_minor_units(currency),
        "default_currency": data.get("viewPreferences", {}).get("defaultCurrency", "USD"),
        "account_totals": data.get("accountSummariesByCurrency", {}),
        "all_accounts": all_accounts,
        "currency_money": format_money,
        "account_money": lambda account: format_money(account["balance"], account.get("currency", "USD")),
        "account_sidebar_money": lambda value, account: sidebar_money(value, account.get("currency", "USD")),
        "budget_month_count": budget_month_count,
        "money": lambda value: format_money(value, currency),
        "sidebar_money": lambda value: sidebar_money(value, currency),
        "money_input": lambda value: money_input(value, currency),
        "money_number": lambda value: money_number(value, currency),
        "valid_money": valid_money,
        "month_label": month_label,
        "shift_month": shift_month,
        "icon": icon,
        "category_icon": category_icon,
        "groups": groups,
        "periods": periods,
        "monthly_funding_flow_available": all(
            period.get("monthlyFundingFlow") is True for period in periods
        ),
        "month_strip": list(dict.fromkeys(shift_month(month, step) for step in range(-4, 9))),
        "period_months": [period["month"] for period in periods],
        "category_path": lambda identifier: quote(identifier, safe=""),
        "category_names": category_names,
        "category_labels": {row["id"]: row["name"] for row in categories},
        "category_parents": category_parents,
        "selected": selected,
        "selected_budget": budgets.get(selected["id"]) if selected else None,
        "transactions": transactions,
        "transaction_window": transaction_window,
        "accounts_by_id": accounts_by_id,
        "account_groups": [
            {
                "label": "On-budget", "on_budget": True,
                "description": "Checking, savings and credit accounts used for everyday budgeting.",
                "accounts": [a for a in all_accounts if is_on_budget(a)],
            },
            {
                "label": "Off-budget", "on_budget": False,
                "description": (
                    "Investments, retirement, property, loans and deposits tracked in your net worth. "
                    "Their balances and transactions stay outside your budget."
                ),
                "accounts": [a for a in all_accounts if not is_on_budget(a)],
            },
        ],
        "account_types": ACCOUNT_TYPES,
        "on_budget_account_types": ON_BUDGET_ACCOUNT_TYPES,
        "account_subtypes": ACCOUNT_SUBTYPES,
        "account_summary": data.get(
            "accountSummary", {"assets": 0, "liabilities": 0, "netWorth": 0}
        ),
        "account_label": lambda account: ACCOUNT_SUBTYPES.get(account["type"], {}).get(
            account.get("subtype"), ACCOUNT_TYPES.get(account["type"], account["type"])
        ),
        "is_liability": is_liability,
        "is_on_budget": is_on_budget,
        "transaction_accounts": data["accounts"],
        "to_review": data.get("toReview", sum(not t["reviewed"] and not t["pending"] for t in transactions)),
        "cash_balance": sum(
            a["balance"]
            for a in data["accounts"]
            if a["type"] == "depository" and is_on_budget(a)
            and not a.get("stale") and not a.get("balanceUnknown")
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
        "chart_ticks": [
            {"y": 192 - index * 170 / 4, "amount": maximum * index // 4}
            for index in range(5)
        ],
        "uncategorized": uncategorized_spending(data),
        "today": today(),
        "client_context": {
            "workspace": workspace, "page": page, "month": month, "currency": currency,
            "pageFragments": True,
        },
        "date_label": format_date,
    }
