"""Presentation totals include cleared uncategorized purchases without counting transfers."""

import pytest
from whab_plugin_web.views import build_context


def test_uncategorized_spending_counts_only_cleared_purchases():
    def transaction(identifier, amount, **changes):
        return {
            "id": identifier, "date": "2026-10-01", "merchant": identifier,
            "amount": amount, "categoryId": None, "pending": False, "reviewed": False,
            **changes,
        }

    data = {
        "categories": [], "budget": [], "accounts": [],
        "trend": [{"month": "2026-10", "spent": 2500}],
        "summary": {"spent": 2500},
        "transactions": [
            transaction("purchase", 2500),
            transaction("income", -10000),
            transaction("pending", 800, pending=True),
            transaction("transfer", 900, isTransfer=True),
        ],
    }
    context = build_context(
        data, {}, [], workspace="personal", page="insights", month="2026-10"
    )
    assert context["uncategorized"] == 2500
    assert context["purchases"] == 1
    assert context["payees"] == [("purchase", 2500)]
    assert context["selected"] is None and context["selected_budget"] is None


def test_exact_currency_amount_inputs():
    from whab_plugin_web.views import money_input, money_number, month_label, shift_month

    assert money_input(123456) == "1234.56"
    assert money_input(-123456) == "-1234.56"
    assert money_input(1_000_000_000_000) == "10000000000.00"
    assert money_input(1234, "JPY") == "1234"
    assert money_input(-1234, "JPY") == "-1234"
    assert money_input(1234, "KWD") == "1.234"
    assert money_input(0, "CLF") == "0.0000"
    assert money_number(-123456) == "-1,234.56"
    assert money_number(1234, "JPY") == "1,234"
    assert money_number(1234, "KWD") == "1.234"
    assert money_number(0, "CLF") == "0.0000"
    assert month_label("2026-10") == "October 2026"
    assert month_label("2026-10", short=True) == "Oct"
    assert shift_month("2026-12", 1) == "2027-01"
    assert shift_month("1900-01", -1) == "1900-01"
    assert shift_month("2200-12", 1) == "2200-12"


def test_tracking_accounts_stay_visible_without_entering_budget_insights():
    accounts = [
        {"id": "checking", "type": "depository", "source": "manual", "balance": 10000},
        {"id": "cd", "type": "depository", "source": "manual", "balance": 20000,
         "onBudget": False},
        {"id": "ira", "type": "retirement", "subtype": "roth_ira", "source": "manual",
         "balance": 30000},
        {"id": "loan", "type": "loan", "subtype": "mortgage", "source": "bank",
         "balance": 5000},
        {"id": "stale", "type": "depository", "source": "manual", "balance": 6000,
         "stale": True},
        {"id": "unknown", "type": "depository", "source": "manual", "balance": 0,
         "balanceUnknown": True},
    ]
    transactions = [
        {"id": account["id"], "accountId": account["id"], "date": "2026-10-01",
         "merchant": account["id"], "amount": 100, "pending": False,
         "reviewed": False, "categoryId": None}
        for account in accounts[:4]
    ]
    summary = {"assets": 60000, "liabilities": 5000, "netWorth": 55000}
    data = {
        "categories": [], "budget": [], "accounts": accounts,
        "trend": [{"month": "2026-10", "spent": 100}],
        "summary": {"spent": 100}, "transactions": transactions,
        "accountSummary": summary,
    }
    context = build_context(data, {}, [], workspace="personal", page="accounts", month="2026-10")
    assert len(context["transactions"]) == 4
    assert context["purchases"] == 1
    assert context["uncategorized"] == 100
    assert context["payees"] == [("checking", 100)]
    assert context["cash_balance"] == 10000
    assert [a["id"] for a in context["account_groups"][0]["accounts"]] == [
        "checking", "stale", "unknown",
    ]
    assert [a["id"] for a in context["account_groups"][1]["accounts"]] == ["cd", "ira", "loan"]
    assert context["account_summary"] == summary
    assert context["account_label"](accounts[0]) == "Cash / checking / savings"
    assert context["account_label"](accounts[2]) == "Roth IRA"
    assert context["is_liability"](accounts[3])
    assert context["on_budget_account_types"] == {"depository", "credit"}
    assert context["account_subtypes"]["deposit"]["cd"] == "Certificate of deposit (CD)"


def test_explicit_parent_categories_and_currency_bound_context():
    child = {"id": "food", "name": "Food", "parentId": "living", "currency": "JPY"}
    budget = {"categoryId": "food", "assigned": 1000, "spent": 250, "available": 750}
    data = {
        "currency": "JPY", "currencies": ["USD", "JPY"],
        "categories": [child], "budget": [budget], "accounts": [],
        "categoryGroups": [{"id": "living", "name": "Living", "children": [child]}],
        "trend": [{"month": "2026-10", "spent": 250}],
        "summary": {"spent": 250}, "transactions": [],
    }
    context = build_context(
        data, {}, [], workspace="personal", page="budget", month="2026-10", selected="food"
    )
    group = context["groups"][0]
    assert group["id"] == "living" and group["assigned"] == 1000
    assert group["rows"] == [
        {"category": child, **budget, "periods": [{"month": "2026-10", **budget}]}
    ]
    assert context["category_names"] == {"food": "Living / Food"}
    assert context["category_parents"] == {"food": "Living"}
    assert context["selected"] == child
    assert context["currency"] == "JPY" and context["minor_units"] == 0
    assert context["currencies"] == ["USD", "JPY"]
    assert context["money"](1234) == "JPY 1,234"
    assert context["money_input"](1234) == "1234"
    assert context["date_label"]("2026-10-01") == "2026-10-01"
    assert ("KWD", 3) in context["currency_choices"]
    assert context["client_context"]["currency"] == "JPY"
    assert all(isinstance(tick["amount"], int) for tick in context["chart_ticks"])


def test_periods_preserve_core_values_without_recalculating_budget_rules():
    child = {"id": 'food/ &?"', "name": "Groceries", "group": "Living"}
    projections = []
    for month, assigned, spent, available in (
        ("2026-12", 1234, 55, 8888), ("2027-01", 777, 222, -111),
        ("2027-02", 0, 0, 333),
    ):
        projections.append({
            "month": month, "categories": [child], "accounts": [], "transactions": [],
            "budget": [{"categoryId": child["id"], "assigned": assigned,
                        "spent": spent, "available": available}],
            "summary": {"assigned": assigned, "spent": spent, "available": available,
                        "readyToAssign": 4321},
            "trend": [{"month": month, "spent": spent}],
        })
    data = {**projections[0], "periods": projections}
    context = build_context(data, {}, [], workspace="demo", page="budget", month="2026-12")
    assert context["selected"] is None and context["selected_budget"] is None
    assert context["period_months"] == ["2026-12", "2027-01", "2027-02"]
    assert context["month_strip"] == [
        "2026-09", "2026-10", "2026-11", "2026-12", "2027-01",
        "2027-02", "2027-03", "2027-04", "2027-05",
    ]
    assert context["groups"][0]["periods"] == [
        {"month": period["month"], "carryover": 0, **{field: period["summary"][field]
         for field in ("assigned", "spent", "available")}}
        for period in projections
    ]
    assert [row["available"] for row in context["groups"][0]["rows"][0]["periods"]] == [
        8888, -111, 333,
    ]
    assert all(period["summary"]["readyToAssign"] == 4321 for period in context["periods"])
    assert context["money_number"](123456) == "1,234.56"
    assert context["category_path"](child["id"]) == "food%2F%20%26%3F%22"
    assert "uncategorized" not in projections[0]


def test_incoming_columns_and_carryover_totals_use_each_core_period_without_recalculating():
    children = [
        {"id": "food", "name": "Food", "group": "Living"},
        {"id": "rent", "name": "Rent", "group": "Living"},
    ]
    projections = []
    for month, mode, carryovers in (
        ("2026-12", "category", (900, -1400)),
        ("2027-01", "balance", (0, 0)),
        ("2027-02", None, (0, 0)),
    ):
        projections.append({
            "month": month, "incomingRolloverMode": mode,
            "categories": children, "accounts": [], "transactions": [],
            "budget": [
                {"categoryId": child["id"], "assigned": 123, "spent": 45,
                 "carryover": carryover, "available": 6789}
                for child, carryover in zip(children, carryovers, strict=True)
            ],
            "summary": {"assigned": 246, "spent": 90, "available": 13578},
            "trend": [{"month": month, "spent": 90}],
        })
    context = build_context(
        {**projections[0], "periods": projections}, {}, [],
        workspace="demo", page="budget", month="2026-12",
    )
    assert [period["show_carryover"] for period in context["periods"]] == [True, False, False]
    assert [period["column_count"] for period in context["periods"]] == [4, 3, 3]
    assert [period["carryover"] for period in context["periods"]] == [-500, 0, 0]
    group = context["groups"][0]
    assert group["carryover"] == -500
    assert [period["carryover"] for period in group["periods"]] == [-500, 0, 0]
    assert [row["carryover"] for row in group["rows"]] == [900, -1400]
    assert [period["available"] for period in group["periods"]] == [13578] * 3
    assert all("column_count" not in period for period in projections)


@pytest.mark.parametrize(("name", "expected"), [
    ("Rent", "home"), ("Home care", "home"), ("Groceries", "wallet"),
    ("Dining", "wallet"), ("Travel", "arrow-up"), ("Transport", "arrow-up"),
    ("Health", "plus"), ("Fun", "sparkles"), ("Other", "tag"),
])
def test_category_inspector_icons(name, expected):
    from whab_plugin_web.icons import category_icon

    assert category_icon(name) == expected


def test_navigation_bounds_do_not_replace_previous_insight_period_with_current_month():
    data = {
        "categories": [], "budget": [], "accounts": [], "transactions": [],
        "trend": [{"month": "1899-12", "spent": 200}, {"month": "1900-01", "spent": 100}],
        "summary": {"spent": 100},
    }
    context = build_context(data, {}, [], workspace="demo", page="insights", month="1900-01")
    assert context["change"] == -50
    assert context["month_strip"][0] == "1900-01"


def test_register_window_filters_complete_history_before_bounding_markup():
    from types import SimpleNamespace

    from starlette.datastructures import URL, QueryParams
    from whab_plugin_web.views import transaction_window

    rows = [
        {"id": str(index), "merchant": f"Merchant {index}", "note": "",
         "reviewed": index % 2 == 0}
        for index in range(205)
    ]
    rows[-1]["note"] = "A far-away STRASSE receipt"

    def window(query=""):
        url = URL("https://localhost:5173/budgets/demo/transactions/current-month/usd?" + query)
        return transaction_window(rows, SimpleNamespace(url=url, query_params=QueryParams(url.query)))

    first = window("accounts=one&accounts=two&period=custom&start_date=2026-01-01")
    assert len(first["rows"]) == 100 and first["count"] == first["total"] == 205
    assert first["start"] == 1 and first["end"] == 100 and first["page"] == 1
    assert first["previous"] is None
    assert QueryParams(URL(first["next"]).query).getlist("accounts") == ["one", "two"]
    assert ("start_date", "2026-01-01") in first["search_params"]
    middle = window("tx_page=2")
    assert len(middle["rows"]) == 100 and middle["start"] == 101 and middle["end"] == 200
    assert "tx_page=1" in middle["previous"] and "tx_page=3" in middle["next"]
    last = window("tx_page=999999")
    assert len(last["rows"]) == 5 and last["page"] == last["pages"] == 3
    assert last["requested_page"] == "999999" and last["next"] is None
    for value in ("invalid", "-2", "0"):
        assert window("tx_page=" + value)["page"] == 1
    searched = window("q=stra%C3%9Fe&tx_page=3")
    assert searched["rows"] == [rows[-1]] and searched["count"] == 1
    assert searched["search"] == "straße" and searched["page"] == 1
    assert searched["search_params"] == []
    assert window("q=merchant+204")["rows"] == [rows[-1]]
    assert window("review=reviewed")["count"] == 103
    assert window("review=needs-review")["count"] == 102
    assert window("q=missing")["rows"] == []
    assert window("q=missing")["start"] == window("q=missing")["end"] == 0
