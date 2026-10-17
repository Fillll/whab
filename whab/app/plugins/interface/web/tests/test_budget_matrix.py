"""The web skin presents core periods and sends existing assignment commands."""

import re
from html import unescape

from whab_web.views import money_input, shift_month

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
    assert 'class="settings-label">Settings</span>' in text
    forms = assignment_forms(text)
    for month, label in (("2026-10", "October"), ("2026-11", "November"), ("2026-12", "December")):
        data = client.get(f"/api/workspace?workspace=demo&month={month}").json()
        summary_html = re.search(
            rf'aria-label="{label} 2026 summary">(.*?)</section>', text, re.S
        )[1]
        for field in ("readyToAssign", "assigned", "spent", "available"):
            assert format_money(data["summary"][field], "USD") in summary_html
        monthly_forms = [form for form in forms if form["month"] == month]
        assert len(monthly_forms) == len(data["categories"])
        for budget in data["budget"]:
            form = next(form for form in monthly_forms if form["api"] == f'/budget/{budget["categoryId"]}')
            assert form["workspace"] == "demo"
            assert form["value"] == money_input(budget["assigned"])
            assert 'data-minor-units="2"' in form["body"]
            assert 'role="alert" hidden' in form["body"]
            assert not form["disabled"]


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
