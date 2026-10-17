"""The web skin presents core periods and sends existing assignment commands."""

import re
from html import unescape

from whab_plugin_web.views import money_input, money_number, shift_month

from whab.plugins import format_money


def assignment_forms(text):
    return [
        {
            "api": unescape(re.search(r'data-api="([^"]+)"', attributes)[1]),
            "workspace": re.search(r'name="workspace" value="([^"]+)"', body)[1],
            "month": re.search(r'name="month" value="([^"]+)"', body)[1],
            "value": re.search(r'name="assigned"[^>]+value="([^"]+)"', body)[1],
            "disabled": bool(re.search(r'name="assigned"[^>]+ disabled', body)),
            "body": body,
        }
        for attributes, body in re.findall(
            r'<form class="budget-assignment"([^>]*)>(.*?)</form>', text, re.S
        )
    ]


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
    forms = assignment_forms(text)
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

    labels = ("Income", "Previous overspending", "Assigned for next month", "Assigned this month")
    for code, factor in (("USD", 1), ("EUR", 10)):
        text = client.get(f"/budgets/home/budget/2026-11/{code.lower()}").text
        overview = re.findall(r'<tr class="budget-overview-row[^"]*">(.*?)</tr>', text, re.S)
        assert [re.search(r'<th scope="row">([^<]+)</th>', row)[1] for row in overview] == list(labels)
        assert text.index("Assigned this month") < text.index("Spending categories")
        assert text.index("Spending categories") < text.index('class="budget-group-row"')
        assert "overspend this month" not in text.lower()
        cells = [re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S) for row in overview]
        assert all(len(row) == 3 for row in cells[:3])
        assert all('colspan="3"' in attributes for row in cells[:3] for attributes, _ in row)
        assert len(cells[3]) == 9

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
            for row, value in zip(cells[:3], (
                summary["income"], -summary["previousOverspending"], summary["assignedNextMonth"],
            ), strict=True):
                assert row[index][1].strip() == money_number(value, code)
            assert [value.strip() for _, value in cells[3][index * 3:index * 3 + 3]] == [
                money_number(summary[field] * sign, code)
                for field, sign in (("assigned", 1), ("spent", -1), ("available", 1))
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
        row = re.search(rf'<th scope="row">{label}</th>(.*?)</tr>', response.text, re.S)[1]
        cells = re.findall(r'<td([^>]*)>(.*?)</td>', row, re.S)
        assert len(cells) == 3
        assert all(value.strip() == "—" for _, value in cells)
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
    assert cells[2][0].strip() == "—"
    assert [value.strip() for value in cells[3][:3]] == ["—", "—", "—"]
    assert cells[1][1].strip() == "0.00"
    assert [value.strip() for value in cells[3][3:6]] == ["10.00", "0.00", "10.00"]
    historical = re.search(r'aria-label="September 2026 summary">(.*?)</section>', text, re.S)[1]
    assert re.search(r'class="budget-ready-value[^"]*">([^<]*)</strong>', historical)[1] == "—"


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
    form = next(form for form in assignment_forms(text) if form["month"] == "2026-11")
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
