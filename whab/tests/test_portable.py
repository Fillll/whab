"""Portable ledgers keep exact money and references without credential-shaped fields."""

from copy import deepcopy

import pytest

from whab.domain.errors import StoreError
from whab.domain.money import MAX_MINOR_UNITS, MAX_SAFE_INTEGER
from whab.domain.portable import validate_portable_budget


def budget():
    return {
        "formatVersion": 1,
        "budgetStartMonth": "2026-10",
        "accounts": [{
            "id": "cash", "name": "Checking", "institution": "", "type": "depository",
            "balance": 10000, "currency": "JPY", "source": "manual", "stale": False,
            "balanceUnknown": False,
        }],
        "categories": [{
            "id": "food", "name": "Food", "group": "Everyday", "target": 100,
            "currency": "JPY",
        }],
        "transactions": [{
            "id": "old", "accountId": "cash", "date": "2025-01-02", "merchant": "Shop",
            "merchantLogo": "shop-1", "amount": -50, "categoryId": "food", "pending": False,
            "reviewed": True, "source": "manual", "note": "Original memo", "isTransfer": False,
            "inferredTransfer": True, "categoryEdited": True, "deleted": True,
        }],
        "assignments": [{"month": "2026-09", "categoryId": "food", "assigned": -100}],
    }


def test_exact_independent_json_records_preserve_history_tombstones_and_negative_allocations():
    data = budget()
    restored = validate_portable_budget(data)
    assert restored == data
    for key in ("accounts", "categories", "transactions", "assignments"):
        assert restored[key] is not data[key]
        assert restored[key][0] is not data[key][0]
    data["transactions"][0]["note"] = "Changed afterward"
    assert restored["transactions"][0]["note"] == "Original memo"


@pytest.mark.parametrize("collection,field", [
    ("accounts", "name"), ("categories", "name"), ("categories", "group"),
    ("transactions", "note"),
])
def test_lone_surrogates_cannot_enter_portable_ledger_text(collection, field):
    data = budget()
    data[collection][0][field] = "invalid-\ud800"
    with pytest.raises(StoreError, match="Unicode"):
        validate_portable_budget(data)


@pytest.mark.parametrize("data", [None, [], {}, {**budget(), "items": []}])
def test_unknown_root_fields_and_invalid_objects_are_rejected(data):
    with pytest.raises(StoreError):
        validate_portable_budget(data)


@pytest.mark.parametrize("version", [True, "1", 0, 2])
def test_version_is_exact_and_supported(version):
    data = budget()
    data["formatVersion"] = version
    with pytest.raises(StoreError, match="version"):
        validate_portable_budget(data)


@pytest.mark.parametrize("collection,field,value", [
    ("accounts", "itemId", "private-item"),
    ("accounts", "mask", "1234"),
    ("accounts", "token", "secret"),
    ("transactions", "cursor", "private-cursor"),
    ("categories", "credential", "secret"),
    ("assignments", "currency", "JPY"),
])
def test_unknown_record_fields_including_connections_cannot_cross_the_boundary(collection, field, value):
    data = budget()
    data[collection][0][field] = value
    with pytest.raises(StoreError, match="fields"):
        validate_portable_budget(data)


@pytest.mark.parametrize("collection,field,value", [
    ("accounts", "balance", True), ("accounts", "balance", 1.25),
    ("accounts", "balance", MAX_MINOR_UNITS + 1), ("accounts", "currency", "ZZZ"),
    ("accounts", "stale", 0), ("accounts", "balanceUnknown", "false"),
    ("accounts", "id", " "), ("accounts", "name", "x\x00"),
    ("categories", "target", -1), ("categories", "currency", "EUR"),
    ("transactions", "amount", "50"), ("transactions", "accountId", "missing"),
    ("transactions", "categoryId", "missing"), ("transactions", "categoryId", []),
    ("transactions", "date", "2026-02-30"), ("transactions", "date", "1800-01-01"),
    ("transactions", "merchantLogo", "https://example.test/image"),
    ("transactions", "merchantLogo", 1), ("transactions", "note", "x" * 1001),
    ("transactions", "reviewed", 1), ("assignments", "month", "2026-13"),
    ("assignments", "categoryId", "missing"), ("assignments", "assigned", False),
])
def test_invalid_money_flags_dates_text_and_references_are_rejected(collection, field, value):
    data = budget()
    data[collection][0][field] = value
    with pytest.raises(StoreError):
        validate_portable_budget(data)


@pytest.mark.parametrize("collection", ["accounts", "categories", "transactions", "assignments"])
def test_duplicate_identifiers_or_allocations_are_rejected(collection):
    data = budget()
    data[collection].append(data[collection][0].copy())
    with pytest.raises(StoreError, match="Duplicate"):
        validate_portable_budget(data)


@pytest.mark.parametrize("collection,value", [
    ("accounts", ()), ("categories", [None]),
    ("accounts", [{}] * 1001), ("categories", [{}] * 10001),
    ("transactions", [{}] * 250001), ("assignments", [{}] * 250001),
])
def test_collections_and_record_counts_are_bounded(collection, value):
    data = budget()
    data[collection] = value
    with pytest.raises(StoreError):
        validate_portable_budget(data)


@pytest.mark.parametrize("month", [None, "2026-00", "2026-10-extra"])
def test_start_month_is_validated(month):
    data = budget()
    data["budgetStartMonth"] = month
    with pytest.raises(StoreError):
        validate_portable_budget(data)


def test_uncategorized_transactions_and_empty_snapshots_are_valid():
    data = budget()
    data["transactions"][0].update(categoryId=None, merchantLogo=None)
    assert validate_portable_budget(data) == data
    for collection in ("accounts", "categories", "transactions", "assignments"):
        data[collection] = []
    assert validate_portable_budget(data) == data


def test_safe_totals_are_checked_per_currency_without_combining_unlike_money():
    data = budget()
    data["accounts"][0]["balance"] = 0
    data["categories"][0]["target"] = 0
    data["assignments"] = []
    count = MAX_SAFE_INTEGER // MAX_MINOR_UNITS
    template = data["transactions"][0]
    data["transactions"] = [
        {**template, "id": f"jpy-{index}", "amount": MAX_MINOR_UNITS}
        for index in range(count)
    ]
    euros = deepcopy(data["accounts"][0])
    euros.update(id="euros", currency="EUR", balance=MAX_MINOR_UNITS)
    data["accounts"].append(euros)
    assert validate_portable_budget(data) == data
    data["transactions"].append({**template, "id": "overflow", "amount": MAX_MINOR_UNITS})
    with pytest.raises(StoreError, match="currency totals"):
        validate_portable_budget(data)
