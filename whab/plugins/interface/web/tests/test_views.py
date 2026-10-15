"""Presentation totals include cleared uncategorized purchases without counting transfers."""

from whab_web.views import build_context


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
    from whab_web.views import money_input, month_label, shift_month

    assert money_input(123456) == "1234.56"
    assert money_input(-123456) == "-1234.56"
    assert money_input(1_000_000_000_000) == "10000000000.00"
    assert money_input(1234, "JPY") == "1234"
    assert money_input(-1234, "JPY") == "-1234"
    assert money_input(1234, "KWD") == "1.234"
    assert money_input(0, "CLF") == "0.0000"
    assert month_label("2026-10") == month_label("2026-10", short=True) == "2026-10"
    assert shift_month("2026-12", 1) == "2027-01"


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
    context = build_context(data, {}, [], workspace="personal", page="budget", month="2026-10")
    group = context["groups"][0]
    assert group["id"] == "living" and group["assigned"] == 1000
    assert group["rows"] == [{"category": child, **budget}]
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
    assert "currency=JPY" in context["url"]("transactions")
    assert "currency=EUR" in context["url"](currency="EUR")
    assert all(isinstance(tick["amount"], int) for tick in context["chart_ticks"])
