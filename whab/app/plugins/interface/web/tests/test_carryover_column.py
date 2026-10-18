"""Incoming category balances are visible as read-only, currency-scoped columns."""

import re
from html import unescape

from whab_plugin_web.views import money_number


def matrix_row(text, row_class):
    return re.search(rf'<tr class="{row_class}">(.*?)</tr>', text, re.S)[1]


def row_cells(row):
    return re.findall(r'<t[dh]\b([^>]*)>(.*?)</t[dh]>', row, re.S)


def visible_text(markup):
    return unescape(re.sub(r"<[^>]*>", "", markup)).strip()


def visible_amount(markup):
    return visible_text(re.sub(r'<small\b[^>]*>.*?</small>', "", markup, flags=re.S))


def category_row(text, name):
    return next(
        row for row in re.findall(r'<tr class="budget-category-row[^\"]*"[^>]*>(.*?)</tr>', text, re.S)
        if visible_text(row_cells(row)[0][1]) == name
    )


def column_labels(text):
    return [visible_text(body).split("From")[0].strip() for _, body in row_cells(
        matrix_row(text, "budget-columns")
    )]


def test_incoming_category_column_uses_previous_month_and_keeps_mixed_month_rows_aligned(client, store):
    account = store.add_account("personal", {
        "name": "Fixture cash", "type": "depository", "balance": 1000000,
    })
    for category, assigned, spent in (("utilities", 30000, 27500), ("rent", 10000, 15000)):
        store.assign("personal", category, "2026-10", assigned)
        store.add_transaction("personal", {
            "accountId": account, "date": "2026-10-05", "merchant": "Carryover fixture",
            "amount": spent, "categoryId": category,
        })
    # November receives category balances, then pools its own ending balances for December.
    store.set_rollover_mode("personal", "2026-11", "USD", "balance")
    store.add_transaction("personal", {
        "accountId": account, "date": "2026-12-05", "merchant": "Uncategorized fixture",
        "amount": 100, "categoryId": None,
    })

    text = client.get("/budgets/home/budget/2026-11/usd").text
    assert column_labels(text) == [
        "Category", "Carryover", "Assigned", "Activity", "Available",
        "Assigned", "Activity", "Available",
        "Carryover", "Assigned", "Activity", "Available",
    ]
    header = matrix_row(text, "budget-columns")
    assert "From Oct 2026" in header and "From Dec 2026" in header
    assert "From Nov 2026" not in header
    cards = re.findall(r'<th colspan="(\d+)" scope="colgroup"', text)
    assert cards == ["4", "3", "4"]
    table = re.search(r'<table class="budget-matrix".*?</table>', text, re.S)[0]
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table, re.S):
        assert sum(
            int(match[1]) if (match := re.search(r'colspan="(\d+)"', attributes)) else 1
            for attributes, _ in row_cells(row)
        ) == 12

    for name, amount in (("Utilities", 2500), ("Rent", -5000), ("Groceries", 0)):
        cells = row_cells(category_row(text, name))
        attributes, body = cells[1]
        assert "budget-carryover" in attributes and "month-start" in attributes
        assert visible_text(body) == money_number(amount)
        assert ("negative" in attributes) is (amount < 0)
        assert ("zero" in attributes) is (amount == 0)
        assert "<input" not in body and "<form" not in body and "<a " not in body
        assert 'name="assigned"' in cells[2][1]
        assert 'name="assigned"' in cells[5][1]
        assert "budget-carryover" in cells[8][0] and visible_text(cells[8][1]) == "0.00"
        assert 'name="assigned"' in cells[9][1]

    home = next(
        row for row in re.findall(r'<tr class="budget-group-row">(.*?)</tr>', text, re.S)
        if re.search(r">Home</span>", row)
    )
    footer = matrix_row(text, "budget-total-row")
    for row in (home, footer):
        attributes, body = row_cells(row)[1]
        assert "budget-carryover" in attributes and "negative" in attributes
        assert visible_text(body) == "-25.00"
        assert "<input" not in body
    overview = matrix_row(text, "budget-overview-row budget-assigned-row")
    ready = matrix_row(text, "budget-overview-row budget-ready-row")
    for row, field in ((overview, "assigned"), (ready, "readyToAssign")):
        cells = row_cells(row)[1:]
        assert [re.search(r'colspan="(\d+)"', attributes)[1] for attributes, _ in cells] == [
            "4", "3", "4",
        ]
        assert "budget-carryover" not in row and "<input" not in row
        for (_, body), month in zip(cells, ("2026-11", "2026-12", "2027-01"), strict=True):
            data = client.get(f"/api/workspace?workspace=personal&month={month}").json()
            assert visible_amount(body) == money_number(data["summary"][field])
    carried_in = matrix_row(text, "budget-overview-row budget-carried-in-row")
    cells = row_cells(carried_in)
    assert [visible_amount(body) for _, body in cells[1:]] == ["-25.00", "-25.00", "0.00"]
    assert "In categories</small>" in cells[1][1]
    assert "Overall balance</small>" in cells[2][1]
    assert "In categories</small>" in cells[3][1]
    assert "Previous overspending" not in text and "Balance carried in" not in text
    assert "<input" not in carried_in and "<form" not in carried_in


def test_carryover_column_policy_and_amounts_are_bound_to_the_selected_currency(client, store):
    for code, assigned in (("USD", 10000), ("EUR", 30000)):
        store.add_account("personal", {
            "name": f"{code} cash", "type": "depository", "balance": 1000000, "currency": code,
        })
        category = store.add_category("personal", {
            "name": f"{code} reserve", "group": "Currency fixture", "target": 0, "currency": code,
        })
        store.assign("personal", category, "2026-10", assigned)
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")

    usd = client.get("/budgets/home/budget/2026-11/usd").text
    eur = client.get("/budgets/home/budget/2026-11/eur").text
    assert column_labels(usd)[1:4] == ["Assigned", "Activity", "Available"]
    assert column_labels(eur)[1:5] == ["Carryover", "Assigned", "Activity", "Available"]
    usd_first = row_cells(category_row(usd, "USD reserve"))[1]
    assert "budget-carryover" not in usd_first[0] and 'name="assigned"' in usd_first[1]
    eur_first = row_cells(category_row(eur, "EUR reserve"))[1]
    assert "budget-carryover" in eur_first[0] and visible_text(eur_first[1]) == "300.00"
    assert "USD reserve" not in eur and "EUR reserve" not in usd
    usd_in = matrix_row(usd, "budget-overview-row budget-carried-in-row")
    eur_in = matrix_row(eur, "budget-overview-row budget-carried-in-row")
    assert visible_amount(row_cells(usd_in)[1][1]) == "100.00"
    assert "Overall balance</small>" in row_cells(usd_in)[1][1]
    assert visible_amount(row_cells(eur_in)[1][1]) == "300.00"
    assert "In categories</small>" in row_cells(eur_in)[1][1]
    response = client.put("/api/budget/settings/rollover", json={
        "workspace": "personal", "month": "2026-10", "currency": "USD", "mode": "category",
    })
    assert response.status_code == 200
    restored = client.get("/budgets/home/budget/2026-11/usd").text
    assert column_labels(restored)[1:5] == ["Carryover", "Assigned", "Activity", "Available"]
    assert visible_text(row_cells(category_row(restored, "USD reserve"))[1][1]) == "100.00"


def test_first_budget_and_historical_months_do_not_have_incoming_columns(client):
    text = client.get("/budgets/home/budget/2026-09/usd").text
    assert re.findall(r'<th colspan="(\d+)" scope="colgroup"', text) == ["3", "3", "4"]
    header = matrix_row(text, "budget-columns")
    assert "From Oct 2026" in header
    assert "From Aug 2026" not in header and "From Sep 2026" not in header


def test_older_remote_projection_does_not_guess_incoming_mode_from_outgoing_mode(client, store, monkeypatch):
    workspace = store.workspace

    def older_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        data.pop("incomingRolloverMode", None)
        data.pop("incomingRolloverModesByCurrency", None)
        return data

    monkeypatch.setattr(store, "workspace", older_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert column_labels(text) == ["Category", *["Assigned", "Activity", "Available"] * 3]
    assert re.findall(r'<th colspan="(\d+)" scope="colgroup"', text) == ["3"] * 3
    assert 'class="budget-rollover-form"' in text
    incoming = matrix_row(text, "budget-overview-row budget-carried-in-row")
    assert [visible_amount(body) for _, body in row_cells(incoming)[1:]] == ["—"] * 3
    assert "In categories</small>" not in incoming and "Overall balance</small>" not in incoming
