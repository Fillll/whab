"""Web investment reads use the same scoped, non-mutating domain report as CLI."""

import pytest

from tests.domain.test_investments import snapshot
from tests.web.test_bank_setup import bank_app  # noqa: F401


@pytest.fixture
def investment_app(request):
    bank = request.getfixturevalue('bank_app')
    original = bank.provider.call
    bank.coordinator.supports_investments = True
    bank.coordinator.config["supportsInvestments"] = True

    def call(command, **values):
        if command == "read_investments":
            bank.provider.calls.append(command)
            result = snapshot()
            result["startDate"] = values["start_date"]
            result["endDate"] = values["end_date"]
            return result
        return original(command, **values)

    bank.provider.call = call
    client = bank.client
    flow = client.post("/api/plaid/link-token", json={}).json()
    assert (
        client.post(
            "/api/plaid/exchange",
            json={
                "flowId": flow["flow_id"],
                "publicToken": "fictional",
                "institutionName": "Fictional",
            },
        ).status_code
        == 200
    )
    return bank


def test_investment_report_preserves_native_ledger_cursor_and_credentials(investment_app):
    value = investment_app
    before = value.store.get_item("item").copy()
    response = value.client.post(
        "/api/plaid/investments",
        json={"itemId": "item", "startDate": "2024-11-07", "endDate": "2026-11-07"},
    )
    assert response.status_code == 200
    report = response.json()
    assert (
        report["positions"][0]["unrealizedGain"] == 25000
        and report["totals"][0]["unrealizedReturnPercent"] == "25.00"
    )
    assert report["positions"][0]["realizedGain"] is None
    assert "fictional-access" not in response.text and "access_token" not in response.text
    assert value.store.get_item("item") == before and value.store.transactions("personal") == []
    assert "no-store" in response.headers["cache-control"]
    page = value.client.get("/budgets/home/accounts/2026-10/usd")
    assert "data-plaid-investments=" in page.text and "investments.css" in page.text


@pytest.mark.parametrize(
    "body,status",
    [
        ({"itemId": "foreign"}, 409),
        ({"itemId": "item", "startDate": "invalid"}, 400),
        ({"itemId": "item", "startDate": "2027-01-01", "endDate": "2026-11-07"}, 400),
        ({"itemId": "item", "bankAccountId": "foreign"}, 400),
    ],
)
def test_report_rejects_other_items_invalid_dates_and_different_response_accounts(
    investment_app, body, status
):
    response = investment_app.client.post("/api/plaid/investments", json=body)
    assert response.status_code == status


def test_investment_report_is_local_and_optional(investment_app):
    client = investment_app.client
    assert (
        client.post(
            "/api/plaid/investments",
            json={"itemId": "item"},
            headers={"origin": "https://foreign.example"},
        ).status_code
        == 403
    )
    investment_app.coordinator.supports_investments = False
    response = client.post("/api/plaid/investments", json={"itemId": "item"})
    assert response.status_code == 400 and "does not support" in response.text


def test_default_history_handles_leap_day_and_rejects_mismatched_provider_window(investment_app):
    value = investment_app
    response = value.client.post(
        "/api/plaid/investments", json={"itemId": "item", "endDate": "2024-02-29"}
    )
    assert response.status_code == 200
    assert response.json()["startDate"] == "2022-02-28"
    original = value.provider.call

    def different_window(command, **values):
        result = original(command, **values)
        if command == "read_investments":
            result["startDate"] = "2020-01-01"
        return result

    value.provider.call = different_window
    response = value.client.post("/api/plaid/investments", json={"itemId": "item"})
    assert response.status_code == 400 and "different history window" in response.text
