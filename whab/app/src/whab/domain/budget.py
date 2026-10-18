"""Pure month projections, envelope carryover and conservative cash allocation.

No imported income is added to balance snapshots. Pre-budget history remains
analytics data; stale cash and unknown credit balances cannot create funding.
"""

from collections import defaultdict

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
    """Explicit money set aside for a target month, independent of its plans."""
    return state.get("futureFunding", {}).get(month, {}).get(currency, 0)


def _net_account_funds(
    state: dict, month: str, currency: str, *, opening: bool = False, history: dict | None = None,
) -> int:
    """Reconstruct a boundary from snapshots, never add imported income twice."""
    accounts = [
        account for account in state["accounts"].values()
        if account.get("currency", "USD") == currency and is_on_budget(account)
    ]
    later_by_account = defaultdict(int)
    if history is None:
        for row in state["transactions"].values():
            if (
                not row.get("deleted") and not row["pending"]
                and (row["date"][:7] >= month if opening else row["date"][:7] > month)
            ):
                later_by_account[row["accountId"]] += row["amount"]
    else:
        for source, totals in history["posted"].items():
            if source >= month if opening else source > month:
                for account_id, amount in totals.items():
                    later_by_account[account_id] += amount

    def balance_at(account):
        return account["balance"] + (-1 if is_liability(account) else 1) * later_by_account[account["id"]]

    cash = sum(
        balance_at(account) for account in accounts
        if account["type"] == "depository"
        and not account.get("stale") and not account.get("balanceUnknown")
    )
    debt = sum(max(0, balance_at(account)) for account in accounts if account["type"] == "credit")
    return cash - debt


def _cash_capacity(
    state: dict, month: str, currency: str, budget: list[dict], *, history: dict | None = None,
) -> int:
    """Keep category deficits from offsetting cash reserved in other envelopes."""
    safe = _net_account_funds(state, month, currency, history=history) - sum(
        max(0, row["available"]) for row in budget
    ) - effective_month_funding(state, shift_month(month, 1), currency)
    if any(
        account.get("balanceUnknown") and account["type"] == "credit"
        for account in state["accounts"].values()
        if account.get("currency", "USD") == currency and is_on_budget(account)
    ):
        safe = min(0, safe)
    return safe


def _currency_history(state: dict) -> dict:
    """Index one validated currency snapshot without copying historical records.

    Posted account movements include transfers. Envelope activity excludes
    transfers and off-budget accounts; the two totals must remain independent.
    """
    transactions = defaultdict(list)
    posted = defaultdict(lambda: defaultdict(int))
    spending = defaultdict(lambda: defaultdict(int))
    income = defaultdict(int)
    spent = defaultdict(int)
    on_budget = {key for key, account in state["accounts"].items() if is_on_budget(account)}
    for row in state["transactions"].values():
        if row.get("deleted"):
            continue
        source = row["date"][:7]
        transactions[source].append(row)
        if not row["pending"]:
            posted[source][row["accountId"]] += row["amount"]
        if row.get("pending") or row.get("isTransfer") or row["accountId"] not in on_budget:
            continue
        category = row.get("categoryId")
        amount = row["amount"]
        spending[source][category] += amount
        if amount < 0 and category is None and state["accounts"][row["accountId"]].get("type") != "credit":
            income[source] -= amount
        if amount > 0 or category is not None:
            spent[source] += amount
    return {"transactions": transactions, "posted": posted, "spending": spending,
            "income": income, "spent": spent}


def _calculate_currency(state: dict, month: str, currency: str, history: dict) -> dict:
    accounts = [account.copy() for account in state["accounts"].values()]

    start = state["budgetStartMonth"]
    carry_start = _carryover_start(state, month, currency)
    prior_spending = defaultdict(int)
    active_spending = history["spending"].get(month, {})
    prior_assignments = defaultdict(int)
    for source, totals in history["spending"].items():
        if carry_start <= source < month:
            for category_id, amount in totals.items():
                prior_spending[category_id] += amount
    for source, rows in state["budget"].items():
        if carry_start <= source < month:
            for category_id, amount in rows.items():
                prior_assignments[category_id] += amount
    previous_month = shift_month(month, -1)
    incoming_mode = rollover_mode(state, previous_month, currency) if previous_month >= start else None
    balance_carryover = 0
    if incoming_mode == "balance":
        pool_start = _carryover_start(state, previous_month, currency)
        balance_carryover = sum(
            amount
            for source, rows in state["budget"].items()
            if pool_start <= source <= previous_month
            for amount in rows.values()
        ) - sum(
            amount for source, totals in history["spending"].items()
            if pool_start <= source <= previous_month
            for category_id, amount in totals.items() if category_id is not None
        )
    budget = []
    for category in state["categories"].values():
        category_id = category["id"]
        assigned = state["budget"].get(month, {}).get(category_id, 0) if month >= start else 0
        carryover = prior_assignments[category_id] - prior_spending[category_id]
        spent = active_spending.get(category_id, 0) if month >= start else 0
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
    income = history["income"].get(month, 0)
    next_month_funding = effective_month_funding(state, shift_month(month, 1), currency)
    carried_in_set_aside = effective_month_funding(state, month, currency) if month > start else 0
    carried_in = (
        _net_account_funds(state, month, currency, opening=True, history=history)
        - sum(row["carryover"] for row in budget)
        if month >= start else 0
    )
    carried_in_unassigned = carried_in - carried_in_set_aside - balance_carryover
    ready = income + carried_in - next_month_funding - sum(row["assigned"] for row in budget)
    safe = _cash_capacity(state, month, currency, budget, history=history)
    if month < start:
        next_month_funding = ready = safe = 0
    summary = {
        "income": income,
        "carriedIn": carried_in,
        "carriedInUnassigned": carried_in_unassigned,
        "carriedInSetAside": carried_in_set_aside,
        "fundingCapacity": min(ready, safe),
        "previousOverspending": sum(max(0, -b["carryover"]) for b in budget),
        "balanceCarryover": balance_carryover,
        "assignedNextMonth": next_month_funding,
        "spent": history["spent"].get(month, 0),
        "assigned": sum(b["assigned"] for b in budget),
        "available": sum(b["available"] for b in budget),
        "readyToAssign": ready,
    }
    if any(abs(value) > MAX_SAFE_INTEGER for value in summary.values()):
        raise StoreError("Ledger totals exceed the supported range.")
    trend = []
    for offset in range(-5, 1):
        m = shift_month(month, offset)
        trend.append({"month": m, "income": history["income"].get(m, 0), "spent": history["spent"].get(m, 0)})
    return {
        "month": month,
        "budgetStartMonth": start,
        "rolloverMode": rollover_mode(state, month, currency),
        "incomingRolloverMode": incoming_mode,
        "rolloverModeEditable": True,
        "monthlyFundingFlow": True,
        "accounts": accounts,
        "categories": [category.copy() for category in state["categories"].values()],
        "transactions": sorted(
            [public_transaction({**row, "currency": currency}) for row in history["transactions"].get(month, ())],
            key=lambda t: (t["date"], t["id"]), reverse=True,
        ),
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
    projection = select_currency(calculate_workspace(state, month), currency)
    available = projection["summary"]["readyToAssign"]
    unknown_credit = any(
        account.get("balanceUnknown") and account["type"] == "credit"
        for account in projection["accounts"] if is_on_budget(account)
    )
    if increase > 0 and unknown_credit:
        raise StoreError("Assignment exceeds available money. Review unknown credit account balances.")
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
    """Protect real cash independently of the monthly allocation identity."""
    return calculate_workspace(state, month)["summariesByCurrency"].get(
        currency, {},
    ).get("fundingCapacity", 0)


def _affected_months(state: dict, candidate: dict, source_month: str, as_of_month: str) -> list[str]:
    """Include every sparse ledger boundary that an edited plan can affect."""
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
    return sorted(month for month in months if affected_start <= month <= "2200-12")


def ensure_projection_totals_supported(
    state: dict, candidate: dict, source_month: str, as_of_month: str,
) -> None:
    """Validate aggregate ranges without treating a negative balance as invalid."""
    for month in _affected_months(state, candidate, source_month, as_of_month):
        calculate_workspace(candidate, month)


def ensure_funding_capacity_preserved(
    state: dict, candidate: dict, source_month: str, as_of_month: str, currency: str,
) -> None:
    """New assignments must not create or worsen an affected funding deficit.

    Cash, assignments and category carryover change only at ledger months or
    saved boundaries. Checking those sparse points also protects intervening
    historical months that a later pooling boundary would otherwise hide.
    """
    for month in _affected_months(state, candidate, source_month, as_of_month):
        before = select_currency(calculate_workspace(state, month), currency)
        after = select_currency(calculate_workspace(candidate, month), currency)
        context = "the current month" if month == as_of_month else month
        before_cash = _cash_capacity(state, month, currency, before["budget"])
        after_cash = _cash_capacity(candidate, month, currency, after["budget"])
        if after_cash < min(before_cash, 0):
            detail = (
                " Review overspending, uncategorized spending, transfers, or account reserves."
                if after_cash < after["summary"]["readyToAssign"] else ""
            )
            raise StoreError(f"Change exceeds available money in {context}.{detail}")
        if after["summary"]["readyToAssign"] < min(before["summary"]["readyToAssign"], 0):
            raise StoreError(f"Change exceeds available money in {context}.")


def _known_child_category(categories: dict, category_id: str) -> dict:
    """Resolve a reference after every category record has already been validated."""
    if type(category_id) is not str or category_id not in categories:
        raise StoreError("Category not found. Only child categories can be assigned.")
    return categories[category_id]


def calculate_workspace(state: dict, month: str, *, as_of_month: str | None = None) -> dict:
    """Project each month's own income, incoming money and explicit allocations.

    The adapter's as-of month remains a validated compatibility argument. Monthly
    pools follow dated ledger boundaries, independently of the viewing date.
    """
    validate_month(month)
    as_of_month = month if as_of_month is None else as_of_month
    validate_month(as_of_month)
    return _project_workspace(_prepare_workspace(state), month)


def calculate_workspace_periods(
    state: dict, month: str, count: int, *, as_of_month: str | None = None,
) -> list[dict]:
    """Project consecutive months from one request-local validated ledger index.

    Validate the entire request before preparing history or returning a period.
    Each result owns its public records; no index survives this calculation.
    """
    validate_month(month)
    if type(count) is not int or not 1 <= count <= 12:
        raise StoreError("Period count must be an integer between 1 and 12.")
    months = [shift_month(month, offset) for offset in range(count)]
    for target in months:
        validate_month(target)
    validate_month(month if as_of_month is None else as_of_month)
    prepared = _prepare_workspace(state)
    return [_project_workspace(prepared, target) for target in months]


def _prepare_workspace(state: dict) -> dict:
    """Validate references once and retain only request-local currency indexes."""
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
            _known_child_category(categories, category_id)
    transactions = defaultdict(dict)
    for key, row in state["transactions"].items():
        account = accounts.get(row["accountId"])
        if account is None:
            raise StoreError("Transaction account not found.")
        category_id = row.get("categoryId")
        if category_id is not None:
            category = _known_child_category(categories, category_id)
            if category["currency"] != account["currency"]:
                raise StoreError("Category currency must match the account currency.")
        transactions[account["currency"]][key] = row
    scoped = {}
    histories = {}
    for currency in currencies:
        children = {key: c for key, c in categories.items() if c["currency"] == currency}
        scoped[currency] = {
            **state,
            "accounts": {key: a for key, a in accounts.items() if a["currency"] == currency},
            "categories": children,
            "transactions": transactions[currency],
            "budget": {
                m: {key: amount for key, amount in rows.items() if key in children}
                for m, rows in state["budget"].items()
            },
        }
        histories[currency] = _currency_history(scoped[currency])
    return {"accounts": accounts, "categories": categories, "currencies": currencies,
            "scoped": scoped, "histories": histories}


def _project_workspace(prepared: dict, month: str) -> dict:
    accounts, categories, currencies = (
        prepared["accounts"], prepared["categories"], prepared["currencies"],
    )
    projections = {
        currency: _calculate_currency(prepared["scoped"][currency], month, currency, prepared["histories"][currency])
        for currency in currencies
    }
    default = projections["USD"]
    return {
        **default,
        "currency": "USD",
        "currencyMinorUnits": 2,
        "nextMonthFundingEditable": True,
        "currencies": list(currencies),
        "accounts": [account.copy() for account in accounts.values()],
        "categories": [category.copy() for category in categories.values()],
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
        "incomingRolloverModesByCurrency": {
            c: p["incomingRolloverMode"] for c, p in projections.items()
        },
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
        "income", "carriedIn", "carriedInUnassigned", "carriedInSetAside", "fundingCapacity",
        "previousOverspending", "balanceCarryover", "assignedNextMonth", "spent", "assigned",
        "available", "readyToAssign",
    ), 0)
    return {
        **data,
        "currency": currency,
        "currencyMinorUnits": currency_minor_units(currency),
        "rolloverMode": data.get("rolloverModesByCurrency", {}).get(currency, "category"),
        "incomingRolloverMode": data.get("incomingRolloverModesByCurrency", {}).get(currency),
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
