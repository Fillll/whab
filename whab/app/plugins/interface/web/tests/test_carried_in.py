"""Assignable carry-in is a core total, with category carryover kept separate."""

import re

import pytest
from whab_plugin_web.views import build_context, money_number


def projection(mode="category", carryovers=(10000, -3000), balance=7654):
    children = [
        {"id": "rent", "name": "Rent", "group": "Home"},
        {"id": "utilities", "name": "Utilities", "group": "Home"},
    ]
    return {
        "month": "2026-11", "budgetStartMonth": "2026-10", "monthlyFundingFlow": True,
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
            "carriedIn": 12345, "carriedInUnassigned": 10000, "carriedInSetAside": 2345,
        },
        "trend": [{"month": "2026-11", "spent": 444}],
    }


def period_context(data):
    return build_context(
        data, {}, [], workspace="demo", page="budget", month=data["month"],
    )["periods"][0]


def carried_in_row(text):
    return re.search(
        r'<tr class="budget-overview-row budget-carried-in-row"[^>]*>(.*?)</tr>', text, re.S,
    )[1]


def total(markup):
    return re.sub(r'<(?:details|small)\b[^>]*>.*?</(?:details|small)>', "", markup, flags=re.S).strip()


@pytest.mark.parametrize("mode", ["category", "balance"])
def test_incoming_total_uses_core_amount_and_only_pooled_categories_appear_in_breakdown(mode):
    data = projection(mode)
    context = period_context(data)
    assert context["carried_in"] == 12345
    assert context["carried_in_parts"] == [
        ("Previously unassigned money", 10000), ("Set aside for this month", 2345),
    ] + ([("Overall category balance", 7654)] if mode == "balance" else [])
    assert context["summary"] == data["summary"]
    assert context["budget"] == data["budget"]
    assert "carried_in" not in data


@pytest.mark.parametrize("mode", [None, "unknown", "none", True, 1])
def test_unknown_incoming_policy_keeps_core_total_but_cannot_explain_its_breakdown(mode):
    data = projection(mode)
    data["rolloverMode"] = "category"
    context = period_context(data)
    assert context["carried_in"] == 12345 and context["carried_in_parts"] == []


@pytest.mark.parametrize("capability", [None, False, 1, "true"])
def test_new_carried_in_model_requires_explicit_capability(capability):
    data = projection()
    if capability is None:
        data.pop("monthlyFundingFlow")
    else:
        data["monthlyFundingFlow"] = capability
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_parts"] == []


@pytest.mark.parametrize("amount", [None, True, "12345", 1.25, 9_007_199_254_740_992,
                                    -9_007_199_254_740_992])
def test_malformed_core_total_is_unavailable_instead_of_recomputed(amount):
    data = projection()
    data["summary"]["carriedIn"] = amount
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_parts"] == []


def test_missing_core_total_is_unavailable():
    data = projection()
    data["summary"].pop("carriedIn")
    assert period_context(data)["carried_in"] is None


@pytest.mark.parametrize("field", ["carriedInUnassigned", "carriedInSetAside", "balanceCarryover"])
@pytest.mark.parametrize("amount", [None, True, "12345", 1.25, 9_007_199_254_740_992])
def test_missing_or_invalid_breakdown_does_not_invent_parts_or_hide_the_core_total(field, amount):
    data = projection("balance")
    if amount is None:
        data["summary"].pop(field)
    else:
        data["summary"][field] = amount
    context = period_context(data)
    assert context["carried_in"] == 12345 and context["carried_in_parts"] == []


def test_first_budget_month_uses_opening_funds_and_historical_month_has_no_planning_total():
    data = projection(None)
    data["budgetStartMonth"] = data["month"]
    context = period_context(data)
    assert context["carried_in"] == 12345
    assert context["carried_in_parts"][0] == ("Opening unassigned money", 10000)
    data["budgetStartMonth"] = "2026-12"
    context = period_context(data)
    assert context["carried_in"] is None and context["carried_in_parts"] == []


def test_category_carryover_does_not_affect_top_carried_in_even_without_categories():
    data = projection()
    data["budget"] = []
    data["categories"] = []
    context = period_context(data)
    assert context["carried_in"] == 12345
    assert len(context["carried_in_parts"]) == 2


@pytest.mark.parametrize(("currency", "amount"), [("JPY", -1234), ("KWD", 1234)])
def test_combined_row_formats_core_currency_and_has_an_explanatory_read_only_breakdown(
    client, store, monkeypatch, currency, amount,
):
    store.add_account("personal", {
        "name": "Fixture cash", "type": "depository", "balance": 100000, "currency": currency,
    })
    workspace = store.workspace

    def projected_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        for summary in (data["summary"], *data["summariesByCurrency"].values()):
            summary.update(carriedIn=amount, carriedInUnassigned=amount, carriedInSetAside=0)
        return data

    monkeypatch.setattr(store, "workspace", projected_workspace)
    # This fixture models a backend with only the single-month projection contract.
    monkeypatch.setattr(store, "workspace_periods", None)
    text = client.get(f"/budgets/home/budget/2026-11/{currency.lower()}").text
    row = carried_in_row(text)
    attributes, markup = re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)[0]
    assert total(markup) == money_number(amount, currency)
    assert ("negative" in attributes) is (amount < 0)
    assert '<th scope="row">Carried in</th>' in row
    assert 'class="budget-row-note"' not in row
    assert "Where it comes from" in markup
    assert "Previously unassigned money" in markup and "Set aside for this month" in markup
    assert "Overall category balance" not in markup
    assert "Balances carried by category stay in their categories." in markup
    assert "<input" not in row and "<form" not in row


@pytest.mark.parametrize("field", ["carriedIn", "carriedInUnassigned", "incomingRolloverMode"])
def test_unsupported_remote_breakdown_never_invents_component_values(client, store, monkeypatch, field):
    workspace = store.workspace

    def malformed_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        if field == "incomingRolloverMode":
            data.pop(field)
            data.pop("incomingRolloverModesByCurrency")
        else:
            for summary in (data["summary"], *data["summariesByCurrency"].values()):
                summary.pop(field, None)
        return data

    monkeypatch.setattr(store, "workspace", malformed_workspace)
    # This fixture models a backend with only the single-month projection contract.
    monkeypatch.setattr(store, "workspace_periods", None)
    response = client.get("/budgets/demo/budget/2026-10/usd")
    assert response.status_code == 200
    row = carried_in_row(response.text)
    cells = re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)
    assert len(cells) == 3
    assert "<details" not in row
    if field == "carriedIn":
        assert all(markup.strip() == "—" and "negative" not in attributes for attributes, markup in cells)
    else:
        assert row.count("Breakdown unavailable") == 3


def test_core_total_with_missing_start_metadata_is_available_without_guessing_policy():
    data = projection()
    data.pop("budgetStartMonth")
    assert period_context(data)["carried_in"] == 12345


def test_actual_monthly_flow_breakdown_keeps_category_money_out_and_receives_explicit_set_aside(
    client, store,
):
    account = store.add_account("personal", {
        "name": "Fixture cash", "type": "depository", "balance": 100000,
    })
    store.assign("personal", "utilities", "2026-10", 20000)
    store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-05", "merchant": "Fixture bill",
        "amount": 5000, "categoryId": "utilities",
    })
    store.assign_next_month("personal", "2026-10", "USD", 12345)
    category = client.get("/budgets/home/budget/2026-11/usd").text
    category_cell = re.findall(r'<td[^>]*>(.*?)</td>', carried_in_row(category), re.S)[0]
    assert total(category_cell) == "800.00"
    assert "Previously unassigned money</dt><dd>676.55</dd>" in category_cell
    assert "Set aside for this month</dt><dd>123.45</dd>" in category_cell
    assert "Overall category balance" not in category_cell
    assert "<input" not in category_cell
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    balance = client.get("/budgets/home/budget/2026-11/usd").text
    balance_cell = re.findall(r'<td[^>]*>(.*?)</td>', carried_in_row(balance), re.S)[0]
    assert total(balance_cell) == "950.00"
    assert "Previously unassigned money</dt><dd>676.55</dd>" in balance_cell
    assert "Set aside for this month</dt><dd>123.45</dd>" in balance_cell
    assert "Overall category balance</dt><dd>150.00</dd>" in balance_cell
    assert "<input" not in balance_cell


@pytest.mark.parametrize("carryovers", [(None, 1), (9_007_199_254_740_991, 1),
                                       (-9_007_199_254_740_991, -1)])
def test_invalid_category_totals_do_not_replace_the_core_assignable_carry_in(carryovers):
    data = projection(carryovers=carryovers)
    context = build_context(data, {}, [], workspace="demo", page="budget", month=data["month"])
    assert context["periods"][0]["carried_in"] == 12345
    assert context["periods"][0]["carryover"] is None
    assert context["groups"][0]["carryover"] is None
    assert context["groups"][0]["periods"][0]["carryover"] is None
