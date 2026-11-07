"""Read-only investment position arithmetic, independent of providers and UIs."""

from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import StoreError
from .money import minor_units
from .validation import validate_date

_ACCOUNT = {"id", "name", "mask", "currency", "balance"}
_POSITION = {
    "accountId",
    "securityId",
    "name",
    "ticker",
    "quantity",
    "price",
    "value",
    "costBasis",
    "currency",
    "priceAsOf",
}
_ACTIVITY = {
    "id",
    "accountId",
    "securityId",
    "date",
    "name",
    "type",
    "subtype",
    "amount",
    "fees",
    "quantity",
    "price",
    "currency",
    "cancelTransactionId",
}


def _text(value, *, nullable=False, limit=500):
    if value is None and nullable:
        return
    if type(value) is not str or not 1 <= len(value) <= limit or "\x00" in value:
        raise StoreError("Invalid investment text.")


def _decimal(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or len(value) > 80:
        raise StoreError("Invalid investment quantity or price.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise StoreError("Invalid investment quantity or price.") from None
    if not result.is_finite() or abs(result) > Decimal("1e18"):
        raise StoreError("Invalid investment quantity or price.")
    return result


def _timestamp(value, *, nullable=False):
    if value is None and nullable:
        return
    _text(value, limit=64)
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise StoreError("Invalid investment snapshot time.") from None


def validate_investments(value):
    """Reject arbitrary provider objects before they cross into public reports."""
    if type(value) is not dict or value.keys() != {
        "accounts",
        "positions",
        "activity",
        "asOf",
        "providerUpdatedAt",
        "startDate",
        "endDate",
    }:
        raise StoreError("Invalid investment snapshot.")
    _timestamp(value["asOf"])
    _timestamp(value["providerUpdatedAt"], nullable=True)
    validate_date(value["startDate"])
    validate_date(value["endDate"])
    if value["startDate"] > value["endDate"]:
        raise StoreError("Investment history start must not follow its end.")
    for field, limit in (("accounts", 500), ("positions", 10000), ("activity", 100000)):
        if type(value[field]) is not list or len(value[field]) > limit:
            raise StoreError("Investment snapshot exceeds its supported bound.")
    accounts = {}
    for row in value["accounts"]:
        if type(row) is not dict or row.keys() != _ACCOUNT:
            raise StoreError("Invalid investment account.")
        for key in ("id", "name"):
            _text(row[key])
        if type(row["mask"]) is not str or len(row["mask"]) > 20:
            raise StoreError("Invalid investment account mask.")
        minor_units(0, row["currency"])
        if row["balance"] is not None:
            minor_units(row["balance"], row["currency"])
        if row["id"] in accounts:
            raise StoreError("Duplicate investment account.")
        accounts[row["id"]] = row
    seen = set()
    for row in value["positions"]:
        if type(row) is not dict or row.keys() != _POSITION:
            raise StoreError("Invalid investment position.")
        for key in ("accountId", "securityId", "name"):
            _text(row[key])
        _text(row["ticker"], nullable=True)
        if (
            row["accountId"] not in accounts
            or row["currency"] != accounts[row["accountId"]]["currency"]
        ):
            raise StoreError("Investment position belongs to a different account or currency.")
        identity = row["accountId"], row["securityId"]
        if identity in seen:
            raise StoreError("Duplicate investment position.")
        seen.add(identity)
        _decimal(row["quantity"])
        _decimal(row["price"], nullable=True)
        minor_units(row["value"], row["currency"])
        if row["costBasis"] is not None:
            minor_units(row["costBasis"], row["currency"])
        if row["priceAsOf"] is not None:
            validate_date(row["priceAsOf"])
    seen = set()
    for row in value["activity"]:
        if type(row) is not dict or row.keys() != _ACTIVITY:
            raise StoreError("Invalid investment activity.")
        for key in ("id", "accountId", "name", "type"):
            _text(row[key])
        for key in ("securityId", "subtype", "cancelTransactionId"):
            _text(row[key], nullable=True)
        if row["id"] in seen:
            raise StoreError("Duplicate investment activity.")
        seen.add(row["id"])
        if (
            row["accountId"] not in accounts
            or row["currency"] != accounts[row["accountId"]]["currency"]
        ):
            raise StoreError("Investment activity belongs to a different account or currency.")
        validate_date(row["date"])
        if not value["startDate"] <= row["date"] <= value["endDate"]:
            raise StoreError("Investment activity is outside the requested history.")
        minor_units(row["amount"], row["currency"])
        if row["fees"] is not None:
            minor_units(row["fees"], row["currency"])
        _decimal(row["quantity"])
        _decimal(row["price"], nullable=True)
    return value


def investment_report(snapshot):
    """Describe held positions; never infer acquisition lots or realized returns."""
    result = deepcopy(validate_investments(snapshot))
    cancelled = {
        row["cancelTransactionId"] for row in result["activity"] if row["cancelTransactionId"]
    }
    additions = {}
    for row in result["activity"]:
        if (
            row["id"] not in cancelled
            and not row["cancelTransactionId"]
            and row["securityId"]
            and (
                row["type"] == "buy"
                or (row["type"] == "transfer" and row["subtype"] == "transfer_in")
            )
            and _decimal(row["quantity"]) > 0
        ):
            key = row["accountId"], row["securityId"]
            additions[key] = min(additions.get(key, row["date"]), row["date"])
    totals = {}
    for row in result["positions"]:
        quantity = _decimal(row["quantity"])
        basis = row["costBasis"]
        reason = (
            "cost_basis_unavailable"
            if basis is None
            else "nonpositive_cost_basis"
            if basis <= 0
            else "short_position_unavailable"
            if quantity < 0
            else None
        )
        gain = row["value"] - basis if reason is None else None
        percent = (
            str((Decimal(gain) * 100 / basis).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
            if gain is not None
            else None
        )
        row.update(
            status="short" if quantity < 0 else "closed" if quantity == 0 else "held",
            unrealizedGain=gain,
            unrealizedReturnPercent=percent,
            unrealizedUnavailableReason=reason,
            firstRecordedAddition=additions.get((row["accountId"], row["securityId"])),
            realizedGain=None,
            realizedReturnPercent=None,
        )
        total = totals.setdefault(
            row["currency"],
            {
                "currency": row["currency"],
                "value": 0,
                "coveredValue": 0,
                "costBasis": 0,
                "unrealizedGain": 0,
                "coveredPositions": 0,
            },
        )
        total["value"] += row["value"]
        if gain is not None:
            total["coveredValue"] += row["value"]
            total["costBasis"] += basis
            total["unrealizedGain"] += gain
            total["coveredPositions"] += 1
    for total in totals.values():
        for field in ("value", "coveredValue", "costBasis", "unrealizedGain"):
            minor_units(total[field], total["currency"])
        total["unrealizedReturnPercent"] = (
            str(
                (Decimal(total["unrealizedGain"]) * 100 / total["costBasis"]).quantize(
                    Decimal(".01"), rounding=ROUND_HALF_UP
                )
            )
            if total["costBasis"]
            else None
        )
    result.update(
        totals=list(totals.values()),
        realizedReturnsAvailable=False,
        additionDateMeaning="Earliest recorded buy or inbound transfer within the requested history; not an original tax-lot acquisition date.",
        returnMeaning="Unrealized change versus reported position cost basis; excludes distributions and realized returns. Realized returns require complete lot history.",
    )
    return result
