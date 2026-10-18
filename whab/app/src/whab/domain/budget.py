"""Pure month projections, envelope carryover and conservative cash allocation.

No imported income is added to balance snapshots. Pre-budget history remains
analytics data; stale cash and unknown credit balances cannot create funding.
"""

from .accounts import account_summary, is_liability, is_on_budget, normalize_account
from .categories import category_tree, ensure_child_category, public_categories
from .dates import shift_month
from .errors import StoreError
from .money import MAX_SAFE_INTEGER, currency_code, currency_minor_units, minor_units
from .transactions import public_transaction
from .validation import validate_month

ROLLOVER_MODES = frozenset({"category", "balance"})


def rollover_mode(state: dict, month: str, currency: str) -> str:
    """A source month's rule controls the boundary into its following month."""
    return state.get("rolloverModes", {}).get(month, {}).get(currency, "category")


def _carryover_start(state: dict, month: str, currency: str) -> str:
    boundaries = [
        shift_month(source, 1)
        for source, rows in state.get("rolloverModes", {}).items()
        if source < month and rows.get(currency) == "balance"
    ]
    return max([state["budgetStartMonth"], *boundaries])


def effective_month_funding(state: dict, month: str, currency: str) -> int:
    """One future month's funding, with categories consuming its saved floor."""
    allocated = sum(
        amount for category_id, amount in state["budget"].get(month, {}).items()
        if state["categories"][category_id].get("currency", "USD") == currency
    )
    floor = state.get("futureFunding", {}).get(month, {}).get(currency, 0)
    if abs(allocated) > MAX_SAFE_INTEGER:
        raise StoreError("Ledger totals exceed the supported range.")
    return max(0, allocated, floor)


def _calculate_currency(state: dict, month: str, currency: str, as_of_month: str) -> dict:
    validate_month(month)
    transactions = [
        public_transaction(t) for t in state["transactions"].values() if not t.get("deleted")
    ]
    transactions.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    cleared = [
        t for t in transactions if not t["pending"] and not t["isTransfer"]
        and is_on_budget(state["accounts"][t["accountId"]])
    ]
    accounts = list(state["accounts"].values())
    budget_accounts = [a for a in accounts if is_on_budget(a)]

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
    carry_start = _carryover_start(state, month, currency)
    prior = [t for t in cleared if carry_start <= t["date"][:7] < month]
    previous_month = shift_month(month, -1)
    balance_carryover = 0
    if previous_month >= start and rollover_mode(state, previous_month, currency) == "balance":
        pool_start = _carryover_start(state, previous_month, currency)
        balance_carryover = sum(
            amount
            for source, rows in state["budget"].items()
            if pool_start <= source <= previous_month
            for amount in rows.values()
        ) - sum(
            t["amount"] for t in cleared
            if pool_start <= t["date"][:7] <= previous_month and t["categoryId"] is not None
        )
    budget = []
    for category in state["categories"].values():
        category_id = category["id"]
        assigned = state["budget"].get(month, {}).get(category_id, 0) if month >= start else 0
        carryover = sum(
            rows.get(category_id, 0) for m, rows in state["budget"].items() if carry_start <= m < month
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
        return account["balance"] + (-1 if is_liability(account) else 1) * sum(
            t["amount"] for t in later if t["accountId"] == account["id"]
        )

    cash = sum(
        balance_at_month(a) for a in budget_accounts
        if a["type"] == "depository" and not a.get("stale") and not a.get("balanceUnknown")
    )
    debt = sum(max(0, balance_at_month(a)) for a in budget_accounts if a["type"] == "credit")
    if month < as_of_month:
        # Later plans may have been funded by later income. A historical month
        # retains only the explicit funding instruction made for its own boundary.
        future = state.get("futureFunding", {}).get(shift_month(month, 1), {}).get(currency, 0)
    else:
        future = sum(
            effective_month_funding(state, target, currency)
            for target in state["budget"].keys() | state.get("futureFunding", {}).keys()
            if target > month and target >= start
        )
    if future > MAX_SAFE_INTEGER:
        raise StoreError("Ledger totals exceed the supported range.")
    ready = cash - debt - sum(max(0, row["available"]) for row in budget) - future
    if any(a.get("balanceUnknown") and a["type"] == "credit" for a in budget_accounts):
        ready = min(0, ready)
    summary = {
        "income": income_for(active),
        "previousOverspending": sum(max(0, -b["carryover"]) for b in budget),
        "balanceCarryover": balance_carryover,
        "assignedNextMonth": effective_month_funding(state, shift_month(month, 1), currency),
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
        "rolloverMode": rollover_mode(state, month, currency),
        "rolloverModeEditable": True,
        "accounts": accounts,
        "categories": list(state["categories"].values()),
        "transactions": [t for t in transactions if t["date"].startswith(month)],
        "budget": budget,
        "summary": summary,
        "accountSummary": account_summary(accounts),
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
    increase = assigned - previous
    if month < as_of_month:
        available = calculate_workspace(state, month, as_of_month=as_of_month)[
            "summariesByCurrency"
        ][currency]["readyToAssign"]
    else:
        as_of = min(month, as_of_month)
        available = funding_capacity(state, as_of, currency)
        if month > as_of:
            allocated = sum(
                amount for key, amount in state["budget"].get(month, {}).items()
                if state["categories"][key].get("currency", "USD") == currency
            )
            floor = state.get("futureFunding", {}).get(month, {}).get(currency, 0)
            increase = max(0, floor, allocated + increase) - max(0, floor, allocated)
    if increase > 0 and increase > available:
        raise StoreError("Assignment exceeds available money.")
    if assigned > previous:
        candidate = {
            **state,
            "budget": {
                **state["budget"],
                month: {**state["budget"].get(month, {}), category_id: assigned},
            },
        }
        ensure_funding_capacity_preserved(state, candidate, month, as_of_month, currency)


def funding_capacity(state: dict, month: str, currency: str) -> int:
    """Protect all commitments independently of the historical display policy."""
    return calculate_workspace(state, month, as_of_month=month)["summariesByCurrency"].get(
        currency, {},
    ).get("readyToAssign", 0)


def ensure_funding_capacity_preserved(
    state: dict, candidate: dict, source_month: str, as_of_month: str, currency: str,
) -> None:
    """An edit must not create or worsen a deficit at any affected boundary.

    Cash, assignments and category carryover change only at ledger months or
    saved boundaries. Checking those sparse points also protects intervening
    historical months that a later pooling boundary would otherwise hide.
    """
    months = {source_month, as_of_month}
    if source_month < "2200-12":
        months.add(shift_month(source_month, 1))
    for ledger in (state, candidate):
        months.update(ledger["budget"])
        months.update(row["date"][:7] for row in ledger["transactions"].values())
        for source in ledger.get("rolloverModes", {}):
            months.update((source, shift_month(source, 1)))
        for target in ledger.get("futureFunding", {}):
            months.update((shift_month(target, -1), target))
    affected_start = min(source_month, as_of_month)
    for month in sorted(months):
        if not affected_start <= month <= "2200-12":
            continue
        before = calculate_workspace(state, month, as_of_month=as_of_month)[
            "summariesByCurrency"
        ].get(currency, {}).get("readyToAssign", 0)
        after = calculate_workspace(candidate, month, as_of_month=as_of_month)[
            "summariesByCurrency"
        ].get(currency, {}).get("readyToAssign", 0)
        if after < min(before, 0):
            context = "the current month" if month == as_of_month else month
            raise StoreError(f"Change exceeds available money in {context}.")


def calculate_workspace(state: dict, month: str, *, as_of_month: str | None = None) -> dict:
    """Project money independently with an adapter-supplied historical reference.

    Without an explicit reference, the selected month is the funding basis. This
    keeps direct domain calls deterministic and conservative, without a clock.
    """
    validate_month(month)
    as_of_month = month if as_of_month is None else as_of_month
    validate_month(as_of_month)
    accounts = {
        key: normalize_account({**row, "currency": currency_code(row.get("currency", "USD"))})
        for key, row in state["accounts"].items()
    }
    categories = {row["id"]: row for row in public_categories(state["categories"])}
    funding = state.get("futureFunding", {})
    if type(funding) is not dict:
        raise StoreError("Invalid next-month funding records.")
    for target, rows in funding.items():
        validate_month(target)
        if not state["budgetStartMonth"] < target <= "2200-12":
            raise StoreError("Next-month funding must target a supported month after the budget start.")
        if type(rows) is not dict:
            raise StoreError("Invalid next-month funding records.")
        for currency, assigned in rows.items():
            minor_units(assigned, currency)
            if assigned < 0:
                raise StoreError("Next-month funding cannot be negative.")
    modes = state.get("rolloverModes", {})
    if type(modes) is not dict:
        raise StoreError("Invalid rollover mode records.")
    for source, rows in modes.items():
        validate_month(source)
        if not state["budgetStartMonth"] <= source < "2200-12":
            raise StoreError("Rollover requires a supported source month from the budget start.")
        if type(rows) is not dict:
            raise StoreError("Invalid rollover mode records.")
        for currency, mode in rows.items():
            currency_code(currency)
            if type(mode) is not str or mode not in ROLLOVER_MODES:
                raise StoreError("Rollover mode must be category or balance.")
    currencies = sorted(
        {
            "USD",
            *(a["currency"] for a in accounts.values()),
            *(c["currency"] for c in categories.values()),
            *(currency for rows in funding.values() for currency in rows),
            *(currency for rows in modes.values() for currency in rows),
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
        projections[currency] = _calculate_currency(scoped, month, currency, as_of_month)
    default = projections["USD"]
    return {
        **default,
        "currency": "USD",
        "currencyMinorUnits": 2,
        "nextMonthFundingEditable": True,
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
        "rolloverModesByCurrency": {c: p["rolloverMode"] for c, p in projections.items()},
        "accountSummariesByCurrency": {c: p["accountSummary"] for c, p in projections.items()},
        "trendsByCurrency": {c: p["trend"] for c, p in projections.items()},
    }


def select_currency(data: dict, currency: str = "USD") -> dict:
    """A homogeneous public view for interfaces and immutable plugin snapshots."""
    currency = currency_code(currency)
    categories = [c for c in data["categories"] if c.get("currency", "USD") == currency]
    accounts = [a for a in data["accounts"] if a.get("currency", "USD") == currency]
    account_ids = {a["id"] for a in accounts}
    category_ids = {c["id"] for c in categories}
    empty_summary = dict.fromkeys((
        "income", "previousOverspending", "balanceCarryover", "assignedNextMonth", "spent", "assigned",
        "available", "readyToAssign",
    ), 0)
    return {
        **data,
        "currency": currency,
        "currencyMinorUnits": currency_minor_units(currency),
        "rolloverMode": data.get("rolloverModesByCurrency", {}).get(currency, "category"),
        "currencies": sorted({*data.get("currencies", ["USD"]), currency}),
        "accounts": accounts,
        "accountSummary": data.get("accountSummariesByCurrency", {}).get(
            currency, account_summary(accounts)
        ),
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
