"""The monthly carryover control renders core settings and sends source-month commands."""

import re

import pytest
from whab_plugin_web.views import money_number, month_label, shift_month


def rollover_forms(text):
    return [
        {
            "attributes": attributes,
            "body": body,
            **{
                field: re.search(rf'name="{field}" value="([^"]+)"', body)[1]
                for field in ("workspace", "month", "currency")
            },
            "options": [
                {
                    "id": re.search(r'id="([^"]+)"', attributes)[1],
                    "value": re.search(r'value="([^"]+)"', attributes)[1],
                    "checked": " checked" in attributes,
                }
                for attributes in re.findall(r'<input([^>]*type="radio"[^>]*)>', body)
            ],
        }
        for attributes, body in re.findall(
            r'<form class="budget-rollover-form"([^>]*)>(.*?)</form>', text, re.S
        )
    ]


def test_rollover_controls_have_two_accessible_choices_for_each_source_month(client):
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    forms = rollover_forms(text)
    assert [form["month"] for form in forms] == ["2026-10", "2026-11", "2026-12"]
    ids = []
    for form in forms:
        assert form["workspace"] == "demo" and form["currency"] == "USD"
        assert 'data-api="/budget/settings/rollover"' in form["attributes"]
        assert 'data-method="PUT"' in form["attributes"] and " data-auto" in form["attributes"]
        assert "<legend>Carryover" in form["body"]
        assert f'Into {month_label(shift_month(form["month"], 1))}' in form["body"]
        assert [option["value"] for option in form["options"]] == ["category", "balance"]
        assert [option["value"] for option in form["options"] if option["checked"]] == ["category"]
        assert ">By category</span>" in form["body"] and ">Overall balance</span>" in form["body"]
        assert re.search(r'class="field-error"[^>]*role="alert" hidden', form["body"])
        for option in form["options"]:
            assert f'for="{option["id"]}"' in form["body"]
            ids.append(option["id"])
    assert len(ids) == len(set(ids)) == 6
    summary = re.search(r'aria-label="October 2026 summary">(.*?)</section>', text, re.S)[1]
    assert summary.index('class="budget-month-heading"') < summary.index("<legend>Carryover")
    assert "budget-summary-lines" not in summary and "budget-ready" not in summary
    assert 'title="Carried over: ' in text and " · Assigned: " in text and " · Activity: " in text


@pytest.mark.parametrize("capability,mode", [
    (None, "category"), (False, "balance"), (1, "category"), ("true", "category"),
    (True, None), (True, "none"), (True, "unknown"),
])
def test_unsupported_rollover_projections_remain_read_only(client, store, monkeypatch, capability, mode):
    workspace = store.workspace

    def unsupported_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        data["rolloverMode"] = mode
        data["rolloverModesByCurrency"] = {
            currency: mode for currency in data["summariesByCurrency"]
        }
        if capability is None:
            data.pop("rolloverModeEditable", None)
        else:
            data["rolloverModeEditable"] = capability
        return data

    monkeypatch.setattr(store, "workspace", unsupported_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert not rollover_forms(text)
    assert text.count('class="budget-rollover-readonly"') == 3
    assert text.count("Editing requires a server update") == 3
    if mode == "balance":
        assert ">Overall balance</span>" in text
    elif mode == "category":
        assert ">By category</span>" in text
    else:
        assert ">Unavailable</span>" in text


def test_rollover_capability_is_period_specific_and_historical_and_final_months_cannot_change(
    client, store, monkeypatch,
):
    workspace = store.workspace

    def mixed_workspace(workspace_name, month=None):
        data = workspace(workspace_name, month)
        if month == "2026-11":
            data.pop("rolloverModeEditable", None)
        return data

    monkeypatch.setattr(store, "workspace", mixed_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert [form["month"] for form in rollover_forms(text)] == ["2026-10", "2026-12"]
    start = workspace("demo", "2026-10")["budgetStartMonth"]
    text = client.get(f"/budgets/demo/budget/{shift_month(start, -1)}/usd").text
    assert [form["month"] for form in rollover_forms(text)] == [
        value for value in (start, shift_month(start, 1)) if value != "2026-11"
    ]
    text = client.get("/budgets/demo/budget/2200-12/usd").text
    assert not rollover_forms(text)
    assert "2201" not in text


def test_older_rollover_projection_keeps_known_category_carry_in(client, store, monkeypatch):
    workspace = store.workspace

    def older_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        data.pop("rolloverMode", None)
        data.pop("rolloverModeEditable", None)
        for summary in (data["summary"], *data["summariesByCurrency"].values()):
            summary.pop("balanceCarryover", None)
        return data

    monkeypatch.setattr(store, "workspace", older_workspace)
    text = client.get("/budgets/demo/budget/2026-10/usd").text
    assert "Balance carried in" not in text
    assert not rollover_forms(text)
    incoming = re.search(
        r'<tr class="budget-overview-row budget-carried-in-row"[^>]*>(.*?)</tr>', text, re.S,
    )[1]
    assert incoming.count("Previously unassigned money") == 3
    assert "Overall category balance" not in incoming
    assert "—" not in incoming


@pytest.mark.parametrize("spent", [5000, 25000])
def test_rollover_form_saves_source_currency_and_shows_signed_pool_balance(client, store, spent):
    category_ids = {}
    for code in ("USD", "EUR"):
        account = store.add_account("personal", {
            "name": f"{code} cash", "type": "depository", "balance": 100000, "currency": code,
        })
        category = store.add_category("personal", {
            "name": f"{code} utilities", "group": "Home", "target": 0, "currency": code,
        })
        category_ids[code] = category
        store.assign("personal", category, "2026-10", 20000)
        store.assign("personal", category, "2026-11", 1234)
        store.add_transaction("personal", {
            "accountId": account, "date": "2026-10-05", "merchant": "Utilities fixture",
            "amount": spent, "categoryId": category,
        })

    text = client.get("/budgets/home/budget/2026-10/usd").text
    form = rollover_forms(text)[0]
    payload = {field: form[field] for field in ("workspace", "month", "currency")}
    assert payload == {"workspace": "personal", "month": "2026-10", "currency": "USD"}
    response = client.put("/api/budget/settings/rollover", json={**payload, "mode": "balance"})
    assert response.status_code == 200
    refreshed = client.get("/budgets/home/budget/2026-10/usd").text
    assert [option["value"] for option in rollover_forms(refreshed)[0]["options"] if option["checked"]] == [
        "balance",
    ]
    usd = client.get("/api/workspace?workspace=personal&month=2026-11&currency=USD").json()
    eur = client.get("/api/workspace?workspace=personal&month=2026-11&currency=EUR").json()
    assert usd["summary"]["balanceCarryover"] == 20000 - spent
    assert usd["summary"]["previousOverspending"] == 0
    assert all(row["carryover"] == 0 for row in usd["budget"])
    assert next(row for row in usd["budget"] if row["categoryId"] == category_ids["USD"])[
        "available"
    ] == 1234
    assert eur["summary"]["balanceCarryover"] == 0
    assert next(row for row in eur["budget"] if row["categoryId"] == category_ids["EUR"])[
        "carryover"
    ] == 20000 - spent
    following = client.get("/budgets/home/budget/2026-11/usd").text
    row = re.search(r'<tr class="budget-overview-row budget-carried-in-row"[^>]*>(.*?)</tr>', following, re.S)[1]
    first_cell = re.search(r'<td([^>]*)>(.*?)</td>', row, re.S)
    assert re.sub(r'<(?:details|small)\b[^>]*>.*?</(?:details|small)>', "", first_cell[2], flags=re.S).strip() == money_number(
        usd["summary"]["carriedIn"],
    )
    assert "Overall category balance" in first_cell[2]
    assert money_number(20000 - spent) in first_cell[2]
    assert ("negative" in first_cell[1]) is (usd["summary"]["carriedIn"] < 0)
    assert "<input" not in row and "<form" not in row
    assert client.put("/api/budget/settings/rollover", json={**payload, "mode": "category"}).status_code == 200
    restored = client.get("/api/workspace?workspace=personal&month=2026-11&currency=USD").json()
    assert restored["summary"]["balanceCarryover"] == 0
    assert next(row for row in restored["budget"] if row["categoryId"] == category_ids["USD"])[
        "carryover"
    ] == 20000 - spent
