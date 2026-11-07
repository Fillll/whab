"""Investments transport never activates products or mixes data into the ledger."""

import asyncio
from copy import deepcopy

import httpx
import pytest
from whab_plugin_plaid.provider import PlaidProvider

from whab.domain.investments import investment_report, validate_investments
from whab.plugins import BankProviderContext, ProviderFailure


def responses():
    return {
        "/item/get": {
            "item": {"products": ["investments"], "consented_products": ["investments"]},
            "status": {"investments": {"last_successful_update": "2026-11-07T10:00:00Z"}},
        },
        "/investments/holdings/get": {
            "accounts": [
                {
                    "account_id": "account",
                    "name": "Fictional Roth IRA",
                    "mask": "1234",
                    "type": "investment",
                    "balances": {"current": 120, "iso_currency_code": "USD"},
                }
            ],
            "securities": [
                {
                    "security_id": "fund",
                    "name": "Fictional Fund",
                    "ticker_symbol": "FUND",
                    "iso_currency_code": "USD",
                }
            ],
            "holdings": [
                {
                    "account_id": "account",
                    "security_id": "fund",
                    "quantity": 2,
                    "institution_price": 60,
                    "institution_value": 120,
                    "cost_basis": 100,
                    "iso_currency_code": "USD",
                    "institution_price_as_of": "2026-11-07",
                }
            ],
        },
        "/investments/transactions/get": {
            "securities": [],
            "total_investment_transactions": 1,
            "investment_transactions": [
                {
                    "account_id": "account",
                    "investment_transaction_id": "buy",
                    "security_id": "fund",
                    "date": "2025-01-01",
                    "name": "Fictional buy",
                    "type": "buy",
                    "subtype": "buy",
                    "amount": 100,
                    "fees": 0,
                    "quantity": 2,
                    "price": 50,
                    "iso_currency_code": "USD",
                    "cancel_transaction_id": None,
                }
            ],
        },
    }


def execute(plaid_env, plaid_client, data, *, account=None, transport=None):
    seen = []

    def request(req):
        import json

        value = json.loads(req.content)
        seen.append((req.url.path, value))
        assert value["access_token"] == "fictional-access"
        return transport(req, value) if transport else httpx.Response(200, json=data[req.url.path])

    async def scenario():
        async with plaid_client(request) as client:
            provider = PlaidProvider(
                BankProviderContext(
                    "fictional-budget",
                    "sandbox",
                    {
                        key: value
                        for key, value in plaid_env.items()
                        if key != "TOKEN_ENCRYPTION_KEY"
                    },
                ),
                client,
            )
            try:
                result = await provider.read_investments(
                    "item", "fictional-access", "2024-11-07", "2026-11-07", account
                )
                assert provider._access_token is None
                return result
            finally:
                await provider.close()

    return asyncio.run(scenario()), seen


def test_active_item_returns_normalized_positions_and_activity_without_writes(
    plaid_env, plaid_client
):
    value, seen = execute(plaid_env, plaid_client, responses())
    assert validate_investments(value) is value
    report = investment_report(value)
    assert report["positions"][0]["unrealizedGain"] == 2000
    assert report["positions"][0]["firstRecordedAddition"] == "2025-01-01"
    assert report["providerUpdatedAt"] == "2026-11-07T10:00:00Z"
    assert [path for path, _ in seen] == [
        "/item/get",
        "/investments/holdings/get",
        "/investments/transactions/get",
    ]
    assert "options" not in seen[1][1]


def test_account_filter_and_pagination_keep_every_returned_activity(plaid_env, plaid_client):
    data = responses()

    def transport(request, payload):
        value = deepcopy(data[request.url.path])
        if request.url.path == "/investments/holdings/get":
            assert payload["options"] == {"account_ids": ["account"]}
        if request.url.path == "/investments/transactions/get":
            assert payload["options"]["account_ids"] == ["account"]
            value["total_investment_transactions"] = 2
            value["investment_transactions"][0]["investment_transaction_id"] = (
                f"row-{payload['options']['offset']}"
            )
        return httpx.Response(200, json=value)

    value, _ = execute(plaid_env, plaid_client, data, account="account", transport=transport)
    assert len(value["activity"]) == 2


@pytest.mark.parametrize(
    "metadata,expected",
    [
        (
            {"products": ["transactions"], "consented_products": ["investments"]},
            "investments_unavailable",
        ),
        ({"products": ["investments"], "consented_products": []}, "investments_consent_required"),
    ],
)
def test_read_does_not_activate_or_request_unconsented_products(
    plaid_env, plaid_client, metadata, expected
):
    def transport(request, payload):
        assert request.url.path == "/item/get"
        return httpx.Response(200, json={"item": metadata})

    with pytest.raises(ProviderFailure) as error:
        execute(plaid_env, plaid_client, {}, transport=transport)
    assert error.value.code == expected


def test_nullable_optional_fields_and_legacy_already_active_consent(plaid_env, plaid_client):
    data = responses()
    data["/item/get"] = {"item": {"billed_products": ["investments"]}, "status": None}
    account = data["/investments/holdings/get"]["accounts"][0]
    account.update(name="", mask=None)
    account["balances"]["current"] = None
    security = data["/investments/holdings/get"]["securities"][0]
    security.update(name="", ticker_symbol="")
    holding = data["/investments/holdings/get"]["holdings"][0]
    holding.update(
        iso_currency_code=None,
        cost_basis=None,
        institution_price=None,
        institution_price_as_of=None,
    )
    data["/investments/transactions/get"]["investment_transactions"][0].update(
        quantity=None, fees=None, price=None
    )
    value, _ = execute(plaid_env, plaid_client, data)
    assert (
        value["providerUpdatedAt"] is None and value["accounts"][0]["name"] == "Investment account"
    )
    assert value["accounts"][0]["balance"] is None and value["accounts"][0]["mask"] == ""
    assert (
        value["positions"][0]["name"] == "Investment position"
        and value["positions"][0]["ticker"] is None
    )
    assert value["positions"][0]["costBasis"] is None and value["activity"][0]["fees"] is None
    validate_investments(value)


def test_security_ticker_is_a_safe_name_fallback_and_noninvestment_account_is_ignored(
    plaid_env, plaid_client
):
    data = responses()
    data["/investments/holdings/get"]["securities"][0]["name"] = None
    data["/investments/holdings/get"]["accounts"].append({"account_id": "card", "type": "credit"})
    result, _ = execute(plaid_env, plaid_client, data)
    assert result["positions"][0]["name"] == "FUND" and len(result["accounts"]) == 1


@pytest.mark.parametrize(
    "change,expected",
    [
        (
            lambda d: d["/investments/holdings/get"]["holdings"][0].update(
                unofficial_currency_code="BTC"
            ),
            "unsupported_currency",
        ),
        (
            lambda d: d["/investments/holdings/get"]["accounts"][0]["balances"].update(
                iso_currency_code=None
            ),
            "unsupported_currency",
        ),
        (
            lambda d: d["/investments/holdings/get"]["holdings"][0].update(account_id="foreign"),
            "transport",
        ),
        (
            lambda d: d["/investments/transactions/get"]["investment_transactions"][0].update(
                account_id="foreign"
            ),
            "transport",
        ),
        (
            lambda d: d["/investments/holdings/get"]["accounts"][0]["balances"].update(
                iso_currency_code="INVALID"
            ),
            "transport",
        ),
        (lambda d: d["/investments/holdings/get"].pop("securities"), "transport"),
        (lambda d: d["/investments/holdings/get"].update(accounts=None), "transport"),
        (
            lambda d: d["/investments/transactions/get"].update(
                investment_transactions=[], total_investment_transactions=1
            ),
            "investment_history_limit",
        ),
        (
            lambda d: d["/investments/holdings/get"].update(holdings=[{}] * 10001),
            "investment_history_limit",
        ),
        (
            lambda d: d["/investments/transactions/get"].update(
                investment_transactions=[{}] * 100001, total_investment_transactions=100001
            ),
            "investment_history_limit",
        ),
        (
            lambda d: d["/investments/transactions/get"].update(
                investment_transactions=[{}] * 100000, total_investment_transactions=100001
            ),
            "investment_history_limit",
        ),
    ],
)
def test_provider_rejects_foreign_unsupported_or_unbounded_data(
    plaid_env, plaid_client, change, expected
):
    data = responses()
    change(data)
    with pytest.raises(ProviderFailure) as error:
        execute(plaid_env, plaid_client, data)
    assert error.value.code == expected


def test_filtered_provider_cannot_return_a_different_account(plaid_env, plaid_client):
    with pytest.raises(ProviderFailure) as error:
        execute(plaid_env, plaid_client, responses(), account="foreign")
    assert error.value.code == "transport"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("ITEM_LOGIN_REQUIRED", "reconnect"),
        ("ADDITIONAL_CONSENT_REQUIRED", "investments_consent_required"),
        ("ACCESS_NOT_GRANTED", "investments_consent_required"),
        ("PRODUCT_NOT_READY", "investments_not_ready"),
        ("INTERNAL_SERVER_ERROR", "transport"),
    ],
)
def test_remote_errors_never_disclose_provider_payload(plaid_env, plaid_client, code, expected):
    with pytest.raises(ProviderFailure) as error:
        execute(
            plaid_env,
            plaid_client,
            {},
            transport=lambda request, payload: httpx.Response(
                400, json={"error_code": code, "error_message": "private-provider-text"}
            ),
        )
    assert error.value.code == expected and "private-provider" not in str(error.value)


def test_unconfigured_investments_never_calls_http(plaid_client):
    async def scenario():
        async with plaid_client(lambda _: pytest.fail("No config must not use network")) as client:
            provider = PlaidProvider(BankProviderContext("budget", "sandbox", {}), client)
            with pytest.raises(ProviderFailure, match="not configured"):
                await provider.read_investments("item", "access", "2024-11-07", "2026-11-07")

    asyncio.run(scenario())
