"""Monthly summary disclosures explain the selected core amounts without inventing math."""

import re
from html import unescape

import pytest
from whab_plugin_web.views import money_number, month_label


def overview_cells(text, label):
    row = re.search(
        rf'<th scope="row">{label}(?:<small\b[^>]*>.*?</small>)?</th>(.*?)</tr>', text, re.S,
    )[1]
    return re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)


def disclosure(cell, label, month):
    details = re.search(r'<details\b([^>]*)>(.*?)</details>', cell, re.S)
    assert details, f"Missing explanation for {label} in {month}"
    attributes, body = details.groups()
    assert "budget-summary-details" in attributes
    summary = re.search(r'<summary([^>]*)>(.*?)</summary>', body, re.S)
    assert summary[2] == "Where it comes from"
    assert unescape(re.search(r'aria-label="([^"]+)"', summary[1])[1]) == (
        f"{label} for {month_label(month)}: Where it comes from"
    )
    assert "<input" not in body and "<form" not in body
    return body


def terms(body):
    return [
        (unescape(re.sub(r"<[^>]*>", "", label)),
         unescape(re.sub(r"<[^>]*>", "", amount)).strip())
        for label, amount in re.findall(r'<dt>(.*?)</dt><dd[^>]*>(.*?)</dd>', body, re.S)
    ]


def equation(amount, operator, currency):
    number = money_number(amount, currency)
    return f"{operator} ({number})" if amount < 0 else f"{operator} {number}"


def test_each_month_explains_income_links_matching_assigned_total_and_funding_equation(client, store):
    for currency, factor in (("USD", 1), ("EUR", 10)):
        account = store.add_account("personal", {
            "name": f"{currency} cash", "type": "depository", "balance": 100000 * factor,
            "currency": currency,
        })
        children = [
            store.add_category("personal", {
                "name": name, "group": "Fixture living", "currency": currency, "target": 0,
            })
            for name in ("Fixture rent", "Fixture utilities")
        ]
        for month, income, assignments, spent, set_aside in (
            ("2026-10", 11111, (1000, 1100), 300, 500),
            ("2026-11", 22222, (1600, 1800), 400, 700),
            ("2026-12", 33333, (2600, 3000), 500, 900),
        ):
            for child, assigned in zip(children, assignments, strict=True):
                store.assign("personal", child, month, assigned * factor)
            for amount, category in ((-income, None), (spent, children[0])):
                store.add_transaction("personal", {
                    "accountId": account, "date": f"{month}-05", "merchant": "Fixture ledger",
                    "amount": amount * factor, "categoryId": category,
                })
            store.assign_next_month("personal", month, currency, set_aside * factor)

    for currency, factor in (("USD", 1), ("EUR", 10)):
        text = client.get(f"/budgets/home/budget/2026-10/{currency.lower()}").text
        cells = {
            label: overview_cells(text, label)
            for label in ("Income", "Carried in", "Assigned this month", "Available to assign",
                          "Assigned for next month")
        }
        for index, (month, income, carry_in, assignments, set_aside, ready) in enumerate((
            ("2026-10", 11111, 100000, (1000, 1100), 500, 108511),
            ("2026-11", 22222, 109011, (1600, 1800), 700, 127133),
            ("2026-12", 33333, 127833, (2600, 3000), 900, 154666),
        )):
            income_body = disclosure(cells["Income"][index][1], "Income", month)
            assert terms(income_body) == [("Received income", money_number(income * factor, currency))]
            assert f'/budgets/my-budget/transactions/{month}/{currency.lower()}' in income_body
            assigned_body = disclosure(
                cells["Assigned this month"][index][1], "Assigned this month", month,
            )
            assert not terms(assigned_body)
            assert "Fixture rent" not in assigned_body and "Fixture utilities" not in assigned_body
            assert f"Assigned total in the spending table for {month_label(month)}" in assigned_body
            target = f"assigned-total-{month}-{currency.lower()}"
            source = f"assigned-summary-{month}-{currency.lower()}"
            assert f'href="#{target}"' in assigned_body
            assert f'data-budget-source-link="{target}"' in assigned_body
            assert f'data-budget-source-overview="{source}"' in assigned_body
            assert f'aria-label="Show matching Assigned total for {month_label(month)} in {currency}"' in assigned_body
            assert f'id="{source}"' in cells["Assigned this month"][index][0]
            footer = re.search(rf'<td id="{target}"([^>]*)>(.*?)</td>', text, re.S)
            assert 'tabindex="-1"' in footer[1]
            total = money_number(sum(assignments) * factor, currency)
            assert footer[2] == total
            summary_value = re.sub(r'<details\b[^>]*>.*?</details>', '', cells["Assigned this month"][index][1], flags=re.S).strip()
            assert summary_value == total
            ready_body = disclosure(
                cells["Available to assign"][index][1], "Available to assign", month,
            )
            assert terms(ready_body) == [
                ("Income", equation(income * factor, "+", currency)),
                ("Carried in", equation(carry_in * factor, "+", currency)),
                ("Assigned for next month", equation(set_aside * factor, "−", currency)),
                ("Assigned this month", equation(sum(assignments) * factor, "−", currency)),
                ("Available to assign", equation(ready * factor, "=", currency)),
            ]
            disclosure(cells["Carried in"][index][1], "Carried in", month)
            editor = cells["Assigned for next month"][index][1]
            assert 'data-api="/budget/funding/next-month"' in editor
            assert "<details" not in editor


@pytest.mark.parametrize(("currency", "income", "carry_in", "assigned", "set_aside"), [
    ("JPY", 1234, -2000, 0, 0),
    ("KWD", 1234, 2000, 234, 3000),
    ("USD", 1234, 2000, 234, 1000),
])
def test_funding_equation_preserves_signs_zero_and_currency_minor_units(
    client, store, monkeypatch, currency, income, carry_in, assigned, set_aside,
):
    store.add_account("personal", {
        "name": "Fixture cash", "type": "depository", "balance": 100000, "currency": currency,
    })
    workspace = store.workspace
    ready = income + carry_in - assigned - set_aside

    def projected_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        for summary in (data["summary"], *data["summariesByCurrency"].values()):
            summary.update(income=income, carriedIn=carry_in, assigned=assigned,
                           assignedNextMonth=set_aside, readyToAssign=ready)
        return data

    monkeypatch.setattr(store, "workspace", projected_workspace)
    text = client.get(f"/budgets/home/budget/2026-10/{currency.lower()}").text
    attributes, cell = overview_cells(text, "Available to assign")[0]
    body = disclosure(cell, "Available to assign", "2026-10")
    assert terms(body) == [
        ("Income", equation(income, "+", currency)),
        ("Carried in", equation(carry_in, "+", currency)),
        ("Assigned for next month", equation(set_aside, "−", currency)),
        ("Assigned this month", equation(assigned, "−", currency)),
        ("Available to assign", equation(ready, "=", currency)),
    ]
    assert ("negative" in attributes) is (ready < 0)
    assert ("positive" in attributes) is (ready > 0)
    if ready < 0:
        assert "A negative amount means" in body


def test_older_server_keeps_explainable_income_and_assignments_but_not_new_funding_math(
    client, store, monkeypatch,
):
    workspace = store.workspace

    def older_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        data.pop("monthlyFundingFlow")
        return data

    monkeypatch.setattr(store, "workspace", older_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    for label in ("Income", "Assigned this month"):
        for index, month in enumerate(("2026-10", "2026-11", "2026-12")):
            disclosure(overview_cells(text, label)[index][1], label, month)
    for label in ("Carried in", "Assigned for next month", "Available to assign"):
        assert all(cell.strip() == "—" for _, cell in overview_cells(text, label))


def test_next_month_amount_gets_an_explanation_when_the_server_cannot_edit_it(
    client, store, monkeypatch,
):
    store.add_account("personal", {"name": "Fixture cash", "type": "depository", "balance": 100000})
    store.assign_next_month("personal", "2026-10", "USD", 12345)
    workspace = store.workspace

    def readonly_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        data.pop("nextMonthFundingEditable")
        return data

    monkeypatch.setattr(store, "workspace", readonly_workspace)
    text = client.get("/budgets/home/budget/2026-10/usd").text
    cell = overview_cells(text, "Assigned for next month")[0][1]
    assert "<input" not in cell and "<form" not in cell
    body = disclosure(cell, "Assigned for next month", "2026-10")
    assert terms(body) == [("Set aside in October 2026", "123.45")]
    assert "Carried in for November 2026" in body


def test_historical_income_has_an_explanation_but_no_fabricated_planning_breakdowns(client, store):
    account = store.add_account("personal", {
        "name": "Fixture cash", "type": "depository", "balance": 100000,
    })
    store.add_transaction("personal", {
        "accountId": account, "date": "2026-09-05", "merchant": "Historical fixture",
        "amount": -12345,
    })
    text = client.get("/budgets/home/budget/2026-09/usd").text
    body = disclosure(overview_cells(text, "Income")[0][1], "Income", "2026-09")
    assert terms(body) == [("Received income", "123.45")]
    for label in ("Carried in", "Assigned for next month", "Assigned this month",
                  "Available to assign"):
        assert overview_cells(text, label)[0][1].strip() == "—"


@pytest.mark.parametrize("field", ["income", "carriedIn", "assignedNextMonth"])
@pytest.mark.parametrize("invalid", [None, True, "12345", 1.5, 9_007_199_254_740_992])
def test_ready_breakdown_requires_exact_core_components_without_replacing_the_total(
    client, store, monkeypatch, field, invalid,
):
    workspace = store.workspace

    def malformed_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        for summary in (data["summary"], *data["summariesByCurrency"].values()):
            if invalid is None:
                summary.pop(field, None)
            else:
                summary[field] = invalid
        return data

    monkeypatch.setattr(store, "workspace", malformed_workspace)
    response = client.get("/budgets/demo/budget/2026-10/usd")
    assert response.status_code == 200
    if field == "income":
        assert all(cell.strip() == "—" for _, cell in overview_cells(response.text, "Income"))
    for _, cell in overview_cells(response.text, "Available to assign"):
        assert "<details" not in cell
        assert "Breakdown unavailable" in cell
        assert not cell.strip().startswith("—")


def test_assigned_total_links_keep_stable_targets_in_budget_refresh_fragments(client):
    path = "/budgets/demo/budget/2026-10/usd"
    page = client.get(path).text
    fragment = client.get(path, headers={"X-WHAB-Budget-Fragment": "1"}).text
    for month in ("2026-10", "2026-11", "2026-12"):
        for text in (page, fragment):
            body = disclosure(overview_cells(text, "Assigned this month")[("2026-10", "2026-11", "2026-12").index(month)][1], "Assigned this month", month)
            assert f'data-budget-source-link="assigned-total-{month}-usd"' in body
            assert f'data-budget-source-overview="assigned-summary-{month}-usd"' in body
            assert len(re.findall(rf'id="assigned-total-{month}-usd"', text)) == 1
            assert len(re.findall(rf'id="assigned-summary-{month}-usd"', text)) == 1
