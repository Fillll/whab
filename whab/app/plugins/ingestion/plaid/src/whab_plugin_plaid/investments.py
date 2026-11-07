"""Plaid Investments reads for already-active products; no ledger mutations."""

from datetime import UTC, datetime
from decimal import Decimal

from whab.domain.money import amount_to_minor_units
from whab.plugins import ProviderFailure


def _currency(row, fallback=None):
    value = row.get("iso_currency_code") or (fallback or {}).get("iso_currency_code")
    if row.get("unofficial_currency_code") or not value:
        raise ProviderFailure("unsupported_currency")
    return value


def _amount(value, currency):
    return None if value is None else amount_to_minor_units(value, currency)


def _number(value):
    return None if value is None else str(Decimal(str(value)))


async def read_investments(call, access_token, start_date, end_date, account_id):
    async def get(endpoint, **payload):
        return await call(endpoint, {"access_token": access_token, **payload})

    item = await get("/item/get")
    metadata = item["item"]
    if "investments" not in metadata.get("products", metadata.get("billed_products", [])):
        raise ProviderFailure("investments_unavailable")
    if (
        metadata.get("consented_products") is not None
        and "investments" not in metadata["consented_products"]
    ):
        raise ProviderFailure("investments_consent_required")
    options = {"account_ids": [account_id]} if account_id is not None else {}
    holdings = await get("/investments/holdings/get", **({"options": options} if options else {}))
    securities = {row["security_id"]: row for row in holdings["securities"]}
    accounts = {
        row["account_id"]: row for row in holdings["accounts"] if row["type"] == "investment"
    }
    transactions = []
    offset = 0
    while True:
        page = await get(
            "/investments/transactions/get",
            start_date=start_date,
            end_date=end_date,
            options={**options, "count": 500, "offset": offset},
        )
        rows = page["investment_transactions"]
        transactions.extend(rows)
        securities.update({row["security_id"]: row for row in page["securities"]})
        offset += len(rows)
        if offset >= page["total_investment_transactions"]:
            break
        if not rows or offset >= 100000:
            raise ProviderFailure("investment_history_limit")
    if len(transactions) > 100000 or len(holdings["holdings"]) > 10000:
        raise ProviderFailure("investment_history_limit")
    if account_id is not None and (set(accounts) != {account_id}):
        raise ProviderFailure("transport")
    normalized_accounts = []
    for identifier, row in accounts.items():
        balances = row["balances"]
        currency = _currency(balances)
        normalized_accounts.append(
            {
                "id": identifier,
                "name": row.get("name") or "Investment account",
                "mask": row.get("mask") or "",
                "currency": currency,
                "balance": _amount(balances.get("current"), currency),
            }
        )
    positions = []
    for row in holdings["holdings"]:
        if row["account_id"] not in accounts:
            raise ProviderFailure("transport")
        security = securities[row["security_id"]]
        currency = _currency(row, security)
        positions.append(
            {
                "accountId": row["account_id"],
                "securityId": row["security_id"],
                "name": security.get("name")
                or security.get("ticker_symbol")
                or "Investment position",
                "ticker": security.get("ticker_symbol") or None,
                "quantity": _number(row["quantity"]),
                "price": _number(row.get("institution_price")),
                "value": _amount(row["institution_value"], currency),
                "costBasis": _amount(row.get("cost_basis"), currency),
                "currency": currency,
                "priceAsOf": row.get("institution_price_as_of"),
            }
        )
    activity = []
    for row in transactions:
        if row["account_id"] not in accounts:
            raise ProviderFailure("transport")
        currency = _currency(row)
        activity.append(
            {
                "id": row["investment_transaction_id"],
                "accountId": row["account_id"],
                "securityId": row.get("security_id"),
                "date": row["date"],
                "name": row["name"],
                "type": row["type"],
                "subtype": row.get("subtype"),
                "amount": _amount(row["amount"], currency),
                "fees": _amount(row.get("fees"), currency),
                "quantity": _number(row.get("quantity") or 0),
                "price": _number(row.get("price")),
                "currency": currency,
                "cancelTransactionId": row.get("cancel_transaction_id"),
            }
        )
    return {
        "accounts": normalized_accounts,
        "positions": positions,
        "activity": activity,
        "asOf": datetime.now(UTC).isoformat(),
        "providerUpdatedAt": ((item.get("status") or {}).get("investments") or {}).get(
            "last_successful_update"
        ),
        "startDate": start_date,
        "endDate": end_date,
    }
