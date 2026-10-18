"""Consecutive projections share private history indexes and own public records."""

from copy import deepcopy

import pytest

from whab.domain import budget
from whab.domain.errors import StoreError


def ledger():
    return {
        "budgetStartMonth": "2026-08",
        "accounts": {
            "cash": {"id": "cash", "type": "depository", "balance": 100000, "currency": "USD"},
            "card": {"id": "card", "type": "credit", "balance": 10000, "currency": "USD"},
            "euro": {"id": "euro", "type": "depository", "balance": 30000, "currency": "EUR"},
        },
        "categories": {
            "food": {"id": "food", "name": "Food", "group": "Living", "target": 0, "currency": "USD"},
            "trip": {"id": "trip", "name": "Trip", "group": "Travel", "target": 0, "currency": "EUR"},
        },
        "transactions": {
            key: {"id": key, "accountId": account, "date": date, "amount": amount,
                  "categoryId": category, "pending": False, "isTransfer": transfer,
                  "merchant": "Synthetic purchase", "note": "Saved note"}
            for key, account, date, amount, category, transfer in (
                ("prior", "cash", "2026-08-10", 1000, "food", False),
                ("current", "cash", "2026-09-10", 2000, "food", False),
                ("transfer", "cash", "2026-10-10", 3000, None, True),
                ("euro", "euro", "2026-09-10", -500, "trip", False),
            )
        },
        "budget": {"2026-08": {"food": 5000}, "2026-09": {"food": 2000, "trip": 1000}},
        "futureFunding": {"2026-10": {"USD": 1000}},
        "rolloverModes": {"2026-09": {"USD": "balance"}},
    }


def test_batch_matches_single_month_rules_with_currency_specific_pooling_and_transfers():
    state = ledger()
    before = deepcopy(state)
    expected = [budget.calculate_workspace(state, month) for month in ("2026-08", "2026-09", "2026-10")]
    result = budget.calculate_workspace_periods(state, "2026-08", 3, as_of_month="2026-10")
    assert result == expected
    october = result[-1]
    assert october["summariesByCurrency"]["USD"]["balanceCarryover"] == 4000
    assert october["summariesByCurrency"]["EUR"]["balanceCarryover"] == 0
    assert october["summariesByCurrency"]["USD"]["spent"] == 0
    assert october["summariesByCurrency"]["EUR"]["available"] == 1500
    assert state == before


def test_batch_validates_history_references_once_and_indexes_each_currency_once(monkeypatch):
    state = ledger()
    calls, validated = [], []
    history, child = budget._currency_history, budget._known_child_category

    def counted_history(scoped):
        calls.append(tuple(scoped["accounts"]))
        return history(scoped)

    def counted_child(categories, identifier):
        validated.append(identifier)
        return child(categories, identifier)

    monkeypatch.setattr(budget, "_currency_history", counted_history)
    monkeypatch.setattr(budget, "_known_child_category", counted_child)
    budget.calculate_workspace_periods(state, "2026-08", 12)
    assert len(calls) == 2
    assert len(validated) == sum(len(rows) for rows in state["budget"].values()) + 3


@pytest.mark.parametrize("month,count,as_of", [
    ("bad", 3, None), ("2026-10", 0, None), ("2026-10", 13, None),
    ("2026-10", True, None), ("2026-10", 1.5, None), ("2026-10", 1, "bad"),
    ("2299-12", 2, None),
])
def test_batch_rejects_the_complete_request_before_preparing_history(monkeypatch, month, count, as_of):
    monkeypatch.setattr(budget, "_prepare_workspace", lambda *_: pytest.fail("Invalid batch prepared history"))
    with pytest.raises(StoreError):
        budget.calculate_workspace_periods(ledger(), month, count, as_of_month=as_of)


def test_batch_public_metadata_and_rows_are_independent_from_other_periods_and_input():
    state = ledger()
    before = deepcopy(state)
    periods = budget.calculate_workspace_periods(state, "2026-08", 3)
    unchanged = deepcopy(periods[1:])
    periods[0]["accounts"][0]["balance"] = 999
    periods[0]["currencies"].append("JPY")
    periods[0]["categories"][0]["name"] = "Changed"
    periods[0]["categoryGroups"][0]["children"][0]["name"] = "Changed group"
    periods[0]["transactions"][0]["note"] = "Changed note"
    periods[0]["budget"][0]["available"] = 999
    periods[0]["summariesByCurrency"]["EUR"]["available"] = 999
    assert periods[1:] == unchanged
    assert state == before


def test_later_batch_calls_read_new_values_without_retaining_a_financial_cache():
    state = ledger()
    first = budget.calculate_workspace_periods(state, "2026-08", 3)
    original = deepcopy(first)
    state["transactions"]["current"]["amount"] = 100
    state["transactions"]["current"]["note"] = "Updated note"
    state["accounts"]["card"]["balanceUnknown"] = True
    current = budget.calculate_workspace_periods(state, "2026-08", 3)
    assert first == original
    assert current[1]["summariesByCurrency"]["USD"]["spent"] == 100
    assert current[1]["summariesByCurrency"]["USD"]["fundingCapacity"] <= 0
    assert next(row for row in current[1]["transactions"] if row["id"] == "current")["note"] == "Updated note"
