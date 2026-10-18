"""The web skin presents core periods and sends existing assignment commands."""

import re
from html import unescape

import pytest
from whab_plugin_web.views import money_input, money_number, month_label, shift_month

from whab.plugins import format_money


def assignment_forms(text):
    return [
        {
            "api": unescape(re.search(r'data-api="([^"]+)"', attributes)[1]),
            "workspace": re.search(r'name="workspace" value="([^"]+)"', body)[1],
            "month": re.search(r'name="month" value="([^"]+)"', body)[1],
            "currency": (
                match[1] if (match := re.search(r'name="currency" value="([^"]+)"', body)) else None
            ),
            "value": re.search(r'name="assigned"[^>]+value="([^"]+)"', body)[1],
            "disabled": bool(re.search(r'name="assigned"[^>]+ disabled', body)),
            "body": body,
        }
        for attributes, body in re.findall(
            r'<form class="budget-assignment"([^>]*)>(.*?)</form>', text, re.S
        )
    ]


def category_assignment_forms(text):
    return [form for form in assignment_forms(text) if form["api"] != "/budget/funding/next-month"]


def displayed_amount(cell):
    """Extract the monetary value independently of its editing or summary treatment."""
    if match := re.search(r'name="assigned"[^>]+value="([^"]+)"', cell):
        return match[1]
    return re.sub(r'<small\b[^>]*>.*?</small>', "", cell, flags=re.S).strip()


def test_three_month_matrix_uses_core_summaries_and_category_values(client):
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert 'aria-label="Category budget across 3 months"' in text
    assert 'class="category-inspector"' not in text
    assert 'class="budget-group-toggle"' in text
    assert 'data-budget-group-member="1"' in text
    assert 'data-dialog="category-dialog"' in text
    assert 'class="sidebar-bottom"' in text
    assert text.index('class="sidebar-bottom"') < text.index('aria-label="Settings"')
    footer = re.search(r'<div class="sidebar-bottom">(.*?)</aside>', text, re.S)[1]
    assert footer.index('class="local-status-dot"') < footer.index('class="local-status-label"')
    assert footer.index('class="local-status-label"') < footer.index('aria-label="Settings"')
    settings = re.search(r'<button class="settings-trigger"[^>]*>(.*?)</button>', footer, re.S)[1]
    assert "<svg" in settings and not re.sub(r"<[^>]*>", "", settings).strip()
    forms = category_assignment_forms(text)
    for month, label in (("2026-10", "October"), ("2026-11", "November"), ("2026-12", "December")):
        data = client.get(f"/api/workspace?workspace=demo&month={month}").json()
        summary_html = re.search(
            rf'aria-label="{label} 2026 summary">(.*?)</section>', text, re.S
        )[1]
        for field in ("readyToAssign", "assigned", "spent", "available"):
            assert format_money(data["summary"][field], "USD") in summary_html
        positions = [summary_html.index(f">{label}<") for label in (
            "Available", "Assigned", "Spent", "To assign",
        )]
        assert positions == sorted(positions)
        heading = re.search(r'<div class="budget-month-heading">(.*?)</div>', summary_html, re.S)[1]
        assert "<svg" not in heading
        monthly_forms = [form for form in forms if form["month"] == month]
        assert len(monthly_forms) == len(data["categories"])
        for budget in data["budget"]:
            form = next(form for form in monthly_forms if form["api"] == f'/budget/{budget["categoryId"]}')
            assert form["workspace"] == "demo"
            assert form["value"] == money_input(budget["assigned"])
            assert 'data-minor-units="2"' in form["body"]
            assert 'role="alert" hidden' in form["body"]
            assert not form["disabled"]


def test_monthly_overview_precedes_categories_and_keeps_periods_and_currencies_separate(client, store):
    for code, factor in (("USD", 1), ("EUR", 10)):
        account = store.add_account("personal", {
            "name": f"{code} cash", "type": "depository", "balance": 1000000,
            "currency": code,
        })
        food = store.add_category("personal", {
            "name": f"{code} food", "group": f"{code} Living", "target": 0, "currency": code,
        })
        reserve = store.add_category("personal", {
            "name": f"{code} reserve", "group": f"{code} Living", "target": 0, "currency": code,
        })
        store.assign("personal", reserve, "2026-10", 4000 * factor)
        for month, assigned, spent, income in (
            ("2026-10", 500, 1000, 0),
            ("2026-11", 700, 900, 1111),
            ("2026-12", 1200, 400, 2222),
            ("2027-01", 1300, 100, 3333),
            ("2027-02", 1700, 0, 0),
        ):
            store.assign("personal", food, month, assigned * factor)
            for amount, category in ((spent, food), (-income, None)):
                if amount:
                    store.add_transaction("personal", {
                        "accountId": account, "date": f"{month}-05", "merchant": "Fixture ledger",
                        "amount": amount * factor, "categoryId": category,
                    })

    labels = (
        "Income", "Previous overspending", "Balance carried in", "Assigned for next month",
        "Assigned this month",
    )
    for code, factor in (("USD", 1), ("EUR", 10)):
        text = client.get(f"/budgets/home/budget/2026-11/{code.lower()}").text
        overview = re.findall(r'<tr class="budget-overview-row[^"]*">(.*?)</tr>', text, re.S)
        assert [re.search(r'<th scope="row">([^<]+)', row)[1] for row in overview] == list(labels)
        assert text.index("Assigned this month") < text.index("Spending categories")
        assert text.index("Spending categories") < text.index('class="budget-group-row"')
        assert "overspend this month" not in text.lower()
        cells = [re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S) for row in overview]
        assert all(len(row) == 3 for row in cells[:4])
        assert all('colspan="4"' in attributes for row in cells[:4] for attributes, _ in row)
        assert len(cells[4]) == 12

        for index, (month, income, previous, next_assigned, assigned, spent, available) in enumerate((
            ("2026-11", 1111, 500, 1200, 700, 900, 3300),
            ("2026-12", 2222, 700, 1300, 1200, 400, 4100),
            ("2027-01", 3333, 0, 1700, 1300, 100, 5300),
        )):
            data = client.get(
                f"/api/workspace?workspace=personal&month={month}&currency={code}"
            ).json()
            summary = data["summary"]
            assert data["currency"] == code
            expected = {
                "income": income, "previousOverspending": previous, "assignedNextMonth": next_assigned,
                "assigned": assigned, "spent": spent, "available": available,
            }
            assert {field: summary[field] for field in expected} == {
                field: value * factor for field, value in expected.items()
            }
            for row, value in zip(cells[:4], (
                summary["income"], -summary["previousOverspending"], summary["balanceCarryover"],
                summary["assignedNextMonth"],
            ), strict=True):
                rendered = displayed_amount(row[index][1])
                expected_value = (
                    money_input(value, code) if 'name="assigned"' in row[index][1]
                    else money_number(value, code)
                )
                assert rendered == expected_value
            assert [value.strip() for _, value in cells[4][index * 4:index * 4 + 4]] == [
                money_number(sum(row["carryover"] for row in data["budget"]), code),
                *[
                    money_number(summary[field] * sign, code)
                    for field, sign in (("assigned", 1), ("spent", -1), ("available", 1))
                ],
            ]


def test_older_remote_projections_show_unavailable_overview_totals_without_inventing_values(
    client, store, monkeypatch,
):
    workspace = store.workspace

    def older_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        for summary in (data["summary"], *data["summariesByCurrency"].values()):
            summary.pop("previousOverspending", None)
            summary.pop("assignedNextMonth", None)
        return data

    monkeypatch.setattr(store, "workspace", older_workspace)
    response = client.get("/budgets/demo/budget/2026-10/usd")
    assert response.status_code == 200
    for label in ("Previous overspending", "Assigned for next month"):
        row = re.search(rf'<th scope="row">{label}.*?</th>(.*?)</tr>', response.text, re.S)[1]
        cells = re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)
        assert len(cells) == 3
        assert all(displayed_amount(value) == "—" for _, value in cells)
        assert all("negative" not in attributes for attributes, _ in cells)
    assert assignment_forms(response.text)


def test_historical_income_remains_visible_while_overview_planning_is_unavailable(client, store):
    account = store.add_account("personal", {
        "name": "Cash", "type": "depository", "balance": 100000,
    })
    for amount, category in ((-12345, None), (6789, "groceries")):
        store.add_transaction("personal", {
            "accountId": account, "date": "2026-09-05", "merchant": "Historical fixture",
            "amount": amount, "categoryId": category,
        })
    store.assign("personal", "groceries", "2026-10", 1000)
    text = client.get("/budgets/home/budget/2026-09/usd").text
    overview = re.findall(r'<tr class="budget-overview-row[^"]*">(.*?)</tr>', text, re.S)
    cells = [re.findall(r'<td[^>]*>(.*?)</td>', row, re.S) for row in overview]
    assert cells[0][0].strip() == "123.45"
    assert cells[1][0].strip() == "—"
    assert displayed_amount(cells[2][0]) == "—"
    assert displayed_amount(cells[3][0]) == "—"
    assert [value.strip() for value in cells[4][:3]] == ["—", "—", "—"]
    assert cells[1][1].strip() == "0.00"
    assert [value.strip() for value in cells[4][3:6]] == ["10.00", "0.00", "10.00"]
    historical = re.search(r'aria-label="September 2026 summary">(.*?)</section>', text, re.S)[1]
    assert re.search(r'class="budget-ready-value[^"]*">([^<]*)</strong>', historical)[1] == "—"


def test_calculated_rows_and_group_totals_are_read_only_while_child_assignments_are_editable(client):
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert "Editable assigned amounts" in text and "Read-only summaries" in text
    overview = re.findall(r'<tr class="budget-overview-row[^"]*">(.*?)</tr>', text, re.S)
    for index in (0, 1, 2, 4):
        assert '<small class="budget-row-note">Summary</small>' in overview[index]
        assert "<input" not in overview[index] and "<form" not in overview[index]
    for row in re.findall(r'<tr class="budget-group-row">(.*?)</tr>', text, re.S):
        assert "Summary</small>" in row
        assert "<input" not in row and "<form" not in row
        assert 'data-budget-group="' in row
        assert len(re.findall(r'<td\b', row)) == 13
    for form in category_assignment_forms(text):
        assert 'class="budget-assignment-edit" aria-hidden="true"' in form["body"]
        assert 'type="text" name="assigned"' in form["body"]
    for row in re.findall(r'<tr class="budget-category-row[^"]*">(.*?)</tr>', text, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        for index in range(1, len(cells), 4):
            for readonly_index in (index, index + 2, index + 3):
                assert "<input" not in cells[readonly_index]
    forms = [form for form in assignment_forms(text) if form["api"] == "/budget/funding/next-month"]
    assert len(forms) == 3
    for form in forms:
        assert form["currency"] == "USD" and form["workspace"] == "demo"
        assert form["month"] in {"2026-10", "2026-11", "2026-12"}
        assert f'from {month_label(form["month"])}' in form["body"]


@pytest.mark.parametrize("capability,amount", [
    (None, 12345), (False, 12345), (1, 12345), ("true", 12345),
    (True, None), (True, True), (True, 1.5), (True, "12345"), (True, -1),
    (True, 9_007_199_254_740_992),
])
def test_next_month_editor_requires_explicit_capability_and_valid_minor_units(
    client, store, monkeypatch, capability, amount,
):
    workspace = store.workspace

    def unsupported_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        if capability is None:
            data.pop("nextMonthFundingEditable", None)
        else:
            data["nextMonthFundingEditable"] = capability
        for summary in (data["summary"], *data["summariesByCurrency"].values()):
            summary["assignedNextMonth"] = amount
        return data

    monkeypatch.setattr(store, "workspace", unsupported_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert not [form for form in assignment_forms(text) if form["api"] == "/budget/funding/next-month"]
    row = re.search(r'<th scope="row">Assigned for next month</th>(.*?)</tr>', text, re.S)[1]
    assert row.count("Editing requires a server update") == 3
    expected = "123.45" if type(amount) is int and amount == 12345 else "—"
    assert [displayed_amount(cell) for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)] == [
        expected,
    ] * 3
    assert category_assignment_forms(text)


@pytest.mark.parametrize("amount", [1_000_000_000_001, 9_007_199_254_740_991])
@pytest.mark.parametrize("capability", [True, None])
def test_large_next_month_totals_remain_visible_without_an_editor(
    client, store, monkeypatch, amount, capability,
):
    workspace = store.workspace

    def large_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        if capability is None:
            data.pop("nextMonthFundingEditable", None)
        for summary in (data["summary"], *data["summariesByCurrency"].values()):
            summary["assignedNextMonth"] = amount
        return data

    monkeypatch.setattr(store, "workspace", large_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    row = re.search(r'<th scope="row">Assigned for next month</th>(.*?)</tr>', text, re.S)[1]
    assert not [form for form in assignment_forms(text) if form["api"] == "/budget/funding/next-month"]
    assert [displayed_amount(cell) for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)] == [
        money_number(amount),
    ] * 3
    if capability is True:
        assert row.count("Edit next month’s category assignments") == 3
        assert "server update" not in row
    else:
        assert row.count("Editing requires a server update") == 3


def test_next_month_editor_uses_each_periods_capability_and_excludes_historical_and_final_months(
    client, store, monkeypatch,
):
    workspace = store.workspace

    def mixed_workspace(workspace_name, month=None):
        data = workspace(workspace_name, month)
        if month == "2026-11":
            data.pop("nextMonthFundingEditable", None)
        return data

    monkeypatch.setattr(store, "workspace", mixed_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert [form["month"] for form in assignment_forms(text) if form["api"] == "/budget/funding/next-month"] == [
        "2026-10", "2026-12",
    ]
    start = workspace("demo", "2026-10")["budgetStartMonth"]
    earlier = shift_month(start, -1)
    text = client.get(f"/budgets/demo/budget/{earlier}/usd").text
    assert [form["month"] for form in assignment_forms(text) if form["api"] == "/budget/funding/next-month"] == [
        value for value in (start, shift_month(start, 1)) if value != "2026-11"
    ]
    text = client.get("/budgets/demo/budget/2200-12/usd").text
    assert not [form for form in assignment_forms(text) if form["api"] == "/budget/funding/next-month"]
    row = re.search(r'<th scope="row">Assigned for next month</th>(.*?)</tr>', text, re.S)[1]
    assert displayed_amount(re.search(r'<td[^>]*>(.*?)</td>', row, re.S)[1]) == "—"
    assert "server update" not in row


def test_next_month_funding_form_saves_source_month_and_currency_without_changing_children(client, store):
    categories = {}
    for code in ("USD", "EUR"):
        store.add_account("personal", {
            "name": f"{code} cash", "type": "depository", "balance": 100000, "currency": code,
        })
        categories[code] = store.add_category("personal", {
            "name": f"{code} rent", "group": "Home", "target": 0, "currency": code,
        })
        store.assign("personal", categories[code], "2026-11", 1000)

    text = client.get("/budgets/home/budget/2026-10/eur").text
    form = next(form for form in assignment_forms(text) if form["api"] == "/budget/funding/next-month")
    assert form["month"] == "2026-10" and form["currency"] == "EUR"
    payload = {key: form[key] for key in ("workspace", "month", "currency")}
    assert client.put("/api" + form["api"], json={**payload, "assigned": 12345}).status_code == 200
    refreshed = client.get("/budgets/home/budget/2026-10/eur").text
    saved = next(form for form in assignment_forms(refreshed) if form["api"] == "/budget/funding/next-month")
    assert saved["month"] == "2026-10" and saved["value"] == "123.45"
    for code, expected in (("EUR", 12345), ("USD", 1000)):
        current = client.get(f"/api/workspace?workspace=personal&month=2026-10&currency={code}").json()
        assert current["summary"]["assignedNextMonth"] == expected
        following = client.get(f"/api/workspace?workspace=personal&month=2026-11&currency={code}").json()
        assert next(row for row in following["budget"] if row["categoryId"] == categories[code])[
            "assigned"
        ] == 1000
    for rejected in (999, 100001):
        assert client.put("/api" + form["api"], json={**payload, "assigned": rejected}).status_code == 400
    unchanged = client.get("/budgets/home/budget/2026-10/eur").text
    assert next(form for form in assignment_forms(unchanged) if form["api"] == "/budget/funding/next-month")[
        "value"
    ] == "123.45"


def test_future_assignment_uses_its_own_period_and_core_validation(client):
    assert client.post("/api/accounts", json={
        "workspace": "personal", "name": "Cash", "type": "depository", "balance": 100000,
    }).status_code == 200
    assert client.post("/api/categories", json={
        "workspace": "personal", "name": "Food", "group": "Living", "target": 20000,
    }).status_code == 200
    data = client.get("/api/workspace?workspace=personal&month=2026-10").json()
    category = data["categories"][0]["id"]
    text = client.get("/budgets/home/budget/2026-10/usd").text
    form = next(form for form in category_assignment_forms(text) if form["month"] == "2026-11")
    assert form["api"] == f"/budget/{category}"
    response = client.put("/api" + form["api"], json={
        "workspace": form["workspace"], "month": form["month"], "assigned": 12345,
    })
    assert response.status_code == 200
    refreshed = client.get("/budgets/home/budget/2026-10/usd").text
    assert {
        saved["month"]: saved["value"] for saved in assignment_forms(refreshed)
        if saved["api"] == form["api"]
    } == {"2026-10": "0.00", "2026-11": "123.45", "2026-12": "0.00"}
    future = client.get("/api/workspace?workspace=personal&month=2026-11").json()
    assert future["summary"]["assigned"] == 12345
    assert future["summary"]["readyToAssign"] == 87655
    assert client.put("/api" + form["api"], json={
        "workspace": "personal", "month": "2026-11", "assigned": 100001,
    }).status_code == 400


def test_inspector_requires_explicit_category_and_retains_selected_period(client):
    text = client.get("/budgets/demo/budget/2026-10/usd?category=missing").text
    assert 'class="category-inspector"' not in text
    text = client.get("/budgets/demo/budget/2026-11/usd?category=groceries").text
    assert 'aria-label="Category details"' in text
    assert 'aria-label="Close category details"' in text
    assert "Monthly target" in text and "Update assignment" in text
    inspector = re.search(r'<aside class="category-inspector"(.*?)</aside>', text, re.S)[1]
    assert 'value="2026-11"' in inspector
    assert 'href="/budgets/demo/budget/2026-11/usd"' in inspector
    assert "category=groceries&amp;" not in inspector
    assert '/2026-12/usd?category=groceries' in text


def test_historical_cells_are_disabled_and_navigation_stays_within_core_bounds(client):
    data = client.get("/api/workspace?workspace=demo&month=2026-10").json()
    start = data["budgetStartMonth"]
    earlier = shift_month(start, -1)
    text = client.get(f"/budgets/demo/budget/{earlier}/usd").text
    forms = assignment_forms(text)
    assert forms and all(form["disabled"] == (form["month"] < start) for form in forms)
    assert "Your budget begins" in text
    for boundary, absent in (("1900-01", "Previous month"), ("2200-12", "Next month")):
        response = client.get(f"/budgets/demo/budget/{boundary}/usd")
        assert response.status_code == 200
        assert f'aria-label="{absent}"' not in response.text
        months = re.findall(r'href="/budgets/demo/budget/(\d{4}-\d{2})/usd', response.text)
        assert months and all("1900-01" <= month <= "2200-12" for month in months)
    assert 'aria-label="Category budget across 1 month"' in response.text


def test_budget_labels_are_escaped_and_uncategorized_activity_is_period_specific(client):
    category_name = '<script>"Food"</script>'
    assert client.post("/api/accounts", json={
        "workspace": "personal", "name": "Cash", "type": "depository", "balance": 100000,
    }).status_code == 200
    assert client.post("/api/categories", json={
        "workspace": "personal", "name": category_name, "group": "<Living>", "target": 0,
    }).status_code == 200
    data = client.get("/api/workspace?workspace=personal&month=2026-10").json()
    assert client.post("/api/transactions", json={
        "workspace": "personal", "accountId": data["accounts"][0]["id"],
        "date": "2026-11-05", "merchant": "Future purchase", "amount": 12345,
        "categoryId": None,
    }).status_code == 200
    text = client.get("/budgets/home/budget/2026-10/usd").text
    assert category_name not in text and "&lt;script&gt;" in text
    assert "&lt;Living&gt;" in text
    row = re.search(r'<tr class="budget-category-row budget-uncategorized">(.*?)</tr>', text, re.S)[1]
    assert "-123.45" in row and "/2026-11/usd" in row
