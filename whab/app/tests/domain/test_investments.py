"""Exact position returns and defensible acquisition-date provenance."""

from copy import deepcopy

import pytest

from whab.domain.errors import StoreError
from whab.domain.investments import investment_report, validate_investments


def snapshot():
    return {
        "asOf": "2026-11-07T12:00:00+00:00",
        "providerUpdatedAt": None,
        "startDate": "2024-11-07",
        "endDate": "2026-11-07",
        "accounts": [
            {
                "id": "account",
                "name": "Fictional IRA",
                "mask": "1234",
                "currency": "USD",
                "balance": 125500,
            }
        ],
        "positions": [
            {
                "accountId": "account",
                "securityId": "fund",
                "name": "Fictional fund",
                "ticker": "FUND",
                "quantity": "10.5",
                "price": "119.047619",
                "value": 125000,
                "costBasis": 100000,
                "currency": "USD",
                "priceAsOf": "2026-11-06",
            }
        ],
        "activity": [],
    }


def event(identifier="buy", **changes):
    return {
        "id": identifier,
        "accountId": "account",
        "securityId": "fund",
        "name": "Fictional event",
        "date": "2025-01-03",
        "type": "buy",
        "subtype": "buy",
        "amount": 100000,
        "fees": 0,
        "quantity": "10.5",
        "price": "95.238095",
        "currency": "USD",
        "cancelTransactionId": None,
        **changes,
    }


def test_report_uses_exact_minor_units_and_does_not_infer_realized_or_added_dates():
    value = snapshot()
    original = deepcopy(value)
    report = investment_report(value)
    assert value == original
    position = report["positions"][0]
    assert position["unrealizedGain"] == 25000 and position["unrealizedReturnPercent"] == "25.00"
    assert position["status"] == "held" and position["firstRecordedAddition"] is None
    assert position["realizedGain"] is None and report["realizedReturnsAvailable"] is False
    assert report["totals"][0]["value"] == 125000 and report["totals"][0]["coveredPositions"] == 1


@pytest.mark.parametrize(
    "basis,quantity,expected",
    [
        (None, "1", "cost_basis_unavailable"),
        (0, "1", "nonpositive_cost_basis"),
        (-1, "1", "nonpositive_cost_basis"),
        (100, "-1", "short_position_unavailable"),
    ],
)
def test_missing_or_unsupported_basis_is_unknown_not_zero_return(basis, quantity, expected):
    value = snapshot()
    value["positions"][0].update(costBasis=basis, quantity=quantity)
    report = investment_report(value)
    position = report["positions"][0]
    assert position["unrealizedGain"] is None and position["unrealizedReturnPercent"] is None
    assert position["unrealizedUnavailableReason"] == expected
    assert report["totals"][0]["unrealizedReturnPercent"] is None


def test_returns_round_half_away_from_zero_and_keep_currencies_separate():
    value = snapshot()
    value["positions"][0].update(value=10001, costBasis=16000, quantity="0")
    value["accounts"].append(
        {"id": "eur", "name": "Fictional EUR", "mask": "", "currency": "EUR", "balance": None}
    )
    value["positions"].append(
        {**value["positions"][0], "accountId": "eur", "currency": "EUR", "value": 20000}
    )
    report = investment_report(value)
    assert report["positions"][0]["unrealizedReturnPercent"] == "-37.49"
    assert report["positions"][0]["status"] == "closed"
    assert {row["currency"]: row["value"] for row in report["totals"]} == {
        "USD": 10001,
        "EUR": 20000,
    }


def test_first_addition_uses_only_uncancelled_buys_or_explicit_inbound_transfers():
    value = snapshot()
    value["activity"] = [
        event("cancelled", date="2024-12-01"),
        event("reversal", cancelTransactionId="cancelled", type="cancel"),
        event("dividend", date="2024-11-20", type="cash", subtype="dividend", quantity="0"),
        event("out", date="2024-11-21", type="transfer", subtype="transfer_out"),
        event("in", date="2025-01-02", type="transfer", subtype="transfer_in"),
        event("later", date="2025-02-01"),
    ]
    assert investment_report(value)["positions"][0]["firstRecordedAddition"] == "2025-01-02"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.update(access_token="forbidden"),
        lambda s: s.update(asOf="2026-11-07"),
        lambda s: s.update(asOf="invalid"),
        lambda s: s.update(startDate="2027-01-01"),
        lambda s: s.update(accounts="bad"),
        lambda s: s["accounts"].append(deepcopy(s["accounts"][0])),
        lambda s: s["accounts"][0].update(mask=123),
        lambda s: s["accounts"][0].update(name=""),
        lambda s: s["accounts"][0].update(balance=1.2),
        lambda s: s["accounts"][0].update(extra=True),
        lambda s: s["positions"][0].update(accountId="foreign"),
        lambda s: s["positions"][0].update(currency="EUR"),
        lambda s: s["positions"][0].update(quantity="NaN"),
        lambda s: s["positions"][0].update(quantity="1e100"),
        lambda s: s["positions"][0].update(quantity=1),
        lambda s: s["positions"][0].update(quantity="bad"),
        lambda s: s["positions"][0].update(ticker="\0"),
        lambda s: s["positions"][0].update(costBasis=1.1),
        lambda s: s["positions"][0].update(priceAsOf="invalid"),
        lambda s: s["positions"][0].update(secret="bad"),
        lambda s: s["positions"].append(deepcopy(s["positions"][0])),
        lambda s: s["activity"].append(event(date="2020-01-01")),
        lambda s: s["activity"].append(event(accountId="foreign")),
        lambda s: s["activity"].extend([event(), event()]),
        lambda s: s["activity"].append(event(currency="EUR")),
        lambda s: s["activity"].append(event(extra=True)),
    ],
)
def test_untrusted_snapshot_cannot_escape_validation(mutate):
    value = snapshot()
    mutate(value)
    with pytest.raises(StoreError):
        validate_investments(value)
