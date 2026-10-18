"""One incoming overview total explains either source-month rollover treatment."""

import re
from html import unescape

import pytest
from whab_plugin_web.views import build_context, money_number


def projection(mode="category", carryovers=(10000, -3000), balance=7654):
    children = [
        {"id": "rent", "name": "Rent", "group": "Home"},
        {"id": "utilities", "name": "Utilities", "group": "Home"},
    ]
    return {
        "month": "2026-11", "budgetStartMonth": "2026-10",
        "incomingRolloverMode": mode,
        "rolloverMode": "balance" if mode == "category" else "category",
        "categories": children, "accounts": [], "transactions": [],
        "budget": [
            {"categoryId": child["id"], "assigned": 111, "spent": 222,
             "carryover": carryover, "available": 333}
            for child, carryover in zip(children, carryovers, strict=True)
        ],
        "summary": {
            "assigned": 222, "spent": 444, "available": 666,
            "previousOverspending": 99999, "balanceCarryover": balance,
        },
        "trend": [{"month": "2026-11", "spent": 444}],
    }


def period_context(data):
    return build_context(
        data, {}, [], workspace="demo", page="budget", month=data["month"],
    )["periods"][0]


@pytest.mark.parametrize(("mode", "carryovers", "balance", "expected", "note"), [
    ("category", (10000, -3000), 7654, 7000, "In categories"),
    ("category", (-10000, 3000), 7654, -7000, "In categories"),
    ("category", (-3000, 3000), 7654, 0, "In categories"),
    ("balance", (0, 0), 7000, 7000, "Overall balance"),
    ("balance", (0, 0), -7000, -7000, "Overall balance"),
    ("balance", (0, 0), 0, 0, "Overall balance"),
])
def test_incoming_total_uses_source_policy_and_never_deducts_previous_overspending_twice(
    mode, carryovers, balance, expected, note,
):
    data = projection(mode, carryovers, balance)
    context = period_context(data)
    assert context["carried_in"] == expected
    assert context["carried_in_note"] == note
    assert context["summary"] == data["summary"]
    assert context["budget"] == data["budget"]
    assert "carried_in" not in data


@pytest.mark.parametrize("mode", [None, "unknown", "none", True, 1])
def test_unknown_incoming_policy_does_not_guess_from_outgoing_setting_or_balances(mode):
    data = projection(mode)
    data["rolloverMode"] = "category"
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_note"] is None


def test_missing_incoming_policy_remains_compatible_with_older_remote_servers():
    data = projection()
    data.pop("incomingRolloverMode")
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_note"] is None


@pytest.mark.parametrize("amount", [None, True, "12345", 1.25, 9_007_199_254_740_992,
                                    -9_007_199_254_740_992])
@pytest.mark.parametrize("mode", ["category", "balance"])
def test_malformed_incoming_amounts_are_unavailable_instead_of_inventing_a_total(mode, amount):
    data = projection(mode)
    if mode == "category":
        data["budget"][0]["carryover"] = amount
    else:
        data["summary"]["balanceCarryover"] = amount
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_note"] is None


@pytest.mark.parametrize("mode", ["category", "balance"])
def test_missing_incoming_amounts_are_unavailable(mode):
    data = projection(mode)
    if mode == "category":
        data["budget"][0].pop("carryover")
    else:
        data["summary"].pop("balanceCarryover")
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_note"] is None


@pytest.mark.parametrize("sign", [1, -1])
def test_unsafe_signed_sum_is_unavailable_even_if_each_category_amount_is_valid(sign):
    data = projection(carryovers=(sign * 9_007_199_254_740_991, sign))
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_note"] is None


def test_first_budget_month_starts_at_zero_and_historical_month_has_no_planning_total():
    data = projection()
    data["budgetStartMonth"] = data["month"]
    context = period_context(data)
    assert context["carried_in"] == 0 and context["carried_in_note"] is None
    data["budgetStartMonth"] = "2026-12"
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_note"] is None


def test_category_rollover_with_no_categories_has_a_zero_total():
    data = projection()
    data["budget"] = []
    data["categories"] = []
    context = period_context(data)
    assert context["carried_in"] == 0 and context["carried_in_note"] == "In categories"


@pytest.mark.parametrize(("currency", "amount"), [("JPY", -1234), ("KWD", 1234)])
def test_combined_row_formats_selected_currency_and_keeps_summary_read_only(client, store, currency, amount):
    account = store.add_account("personal", {
        "name": "Fixture cash", "type": "depository", "balance": 100000, "currency": currency,
    })
    category = store.add_category("personal", {
        "name": "Fixture category", "group": "Home", "target": 0, "currency": currency,
    })
    if amount > 0:
        store.assign("personal", category, "2026-10", amount)
    else:
        store.add_transaction("personal", {
            "accountId": account, "date": "2026-10-05", "merchant": "Fixture spending",
            "amount": -amount, "categoryId": category,
        })
    text = client.get(f"/budgets/home/budget/2026-11/{currency.lower()}").text
    row = re.search(r'<tr class="budget-overview-row budget-carried-in-row">(.*?)</tr>', text, re.S)[1]
    attributes, markup = re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)[0]
    number = unescape(re.sub(r'<small\b[^>]*>.*?</small>', "", markup, flags=re.S)).strip()
    assert number == money_number(amount, currency)
    assert ("negative" in attributes) is (amount < 0)
    assert "In categories</small>" in markup
    assert "<input" not in row and "<form" not in row
    assert "Previous overspending" not in text and "Balance carried in" not in text


@pytest.mark.parametrize("mode", ["category", "balance"])
def test_unsupported_remote_amount_renders_dash_without_breaking_the_budget(client, store, monkeypatch, mode):
    workspace = store.workspace

    def malformed_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        data["incomingRolloverMode"] = mode
        data["incomingRolloverModesByCurrency"] = {
            currency: mode for currency in data["summariesByCurrency"]
        }
        if mode == "category":
            data["budget"][0]["carryover"] = "invalid"
        else:
            for summary in (data["summary"], *data["summariesByCurrency"].values()):
                summary.pop("balanceCarryover", None)
        return data

    monkeypatch.setattr(store, "workspace", malformed_workspace)
    response = client.get("/budgets/demo/budget/2026-10/usd")
    assert response.status_code == 200
    row = re.search(
        r'<tr class="budget-overview-row budget-carried-in-row">(.*?)</tr>', response.text, re.S,
    )[1]
    cells = re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)
    assert len(cells) == 3
    assert all(markup.strip() == "—" and "negative" not in attributes for attributes, markup in cells)
    assert "In categories</small>" not in row and "Overall balance</small>" not in row
