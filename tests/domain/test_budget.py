"""Budget projections operate on plain records without opening SQLite or HTTP."""

from copy import deepcopy

import pytest

from whab.domain.budget import calculate_workspace, ensure_assignment_allowed
from whab.domain.errors import StoreError


@pytest.fixture
def state():
    return {
        "budgetStartMonth": "2026-08",
        "accounts": {
            "cash": {"id": "cash", "type": "depository", "balance": 100000, "stale": False}
        },
        "categories": {
            "groceries": {
                "id": "groceries",
                "name": "Groceries",
                "group": "Everyday",
                "target": 20000,
            }
        },
        "transactions": {},
        "budget": {},
    }


def transaction(transaction_id, date, amount, *, pending=False, transfer=False):
    return {
        "id": transaction_id,
        "accountId": "cash",
        "date": date,
        "merchant": "Fixture grocer",
        "amount": amount,
        "categoryId": "groceries",
        "pending": pending,
        "reviewed": False,
        "source": "manual",
        "note": "",
        "isTransfer": transfer,
        "categoryEdited": False,
    }


def test_rollover_and_refund_preserve_money_without_mutating_state(state):
    state["budget"] = {"2026-08": {"groceries": 20000}, "2026-09": {"groceries": 10000}}
    state["transactions"] = {
        "purchase": transaction("purchase", "2026-08-10", 7000),
        "refund": transaction("refund", "2026-09-10", -500),
    }
    state["accounts"]["cash"]["balance"] = 93500
    before = deepcopy(state)
    view = calculate_workspace(state, "2026-09")
    category = view["budget"][0]
    assert (category["carryover"], category["spent"], category["available"]) == (13000, -500, 23500)
    assert view["summary"]["readyToAssign"] == 70000
    assert "categoryEdited" not in view["transactions"][0]
    assert state == before


def test_history_pending_and_transfers_do_not_consume_new_envelopes(state):
    state["budgetStartMonth"] = "2026-10"
    state["budget"] = {"2026-10": {"groceries": 20000}}
    state["transactions"] = {
        "history": transaction("history", "2024-11-01", 800000),
        "pending": transaction("pending", "2026-10-01", 6425, pending=True),
        "transfer": transaction("transfer", "2026-10-02", 6000, transfer=True),
    }
    view = calculate_workspace(state, "2026-10")
    assert view["budget"][0]["available"] == 20000
    assert view["summary"]["spent"] == 0
    assert view["summary"]["readyToAssign"] == 80000
    assert calculate_workspace(state, "2024-11")["summary"]["spent"] == 800000
    with pytest.raises(StoreError, match="before the budget start"):
        ensure_assignment_allowed(state, "groceries", "2024-11", 100, "2026-10")


def test_stale_cash_card_debt_and_negative_envelopes_do_not_invent_funding(state):
    state["accounts"]["old"] = {"id": "old", "type": "depository", "balance": 50000, "stale": True}
    state["accounts"]["card"] = {"id": "card", "type": "credit", "balance": 20000}
    state["categories"]["fun"] = {"id": "fun", "name": "Fun", "group": "Life", "target": 0}
    state["budget"] = {"2026-10": {"groceries": 10000}}
    spent = transaction("expense", "2026-10-01", 5000)
    spent["categoryId"] = "fun"
    state["transactions"]["expense"] = spent
    view = calculate_workspace(state, "2026-10")
    assert view["summary"]["available"] == 5000
    assert view["summary"]["readyToAssign"] == 70000
    state["accounts"]["card"]["balanceUnknown"] = True
    assert calculate_workspace(state, "2026-10")["summary"]["readyToAssign"] == 0


def test_future_assignments_are_reserved_but_deficit_reductions_are_allowed(state):
    state["budget"] = {"2026-10": {"groceries": 20000}, "2026-11": {"groceries": 30000}}
    with pytest.raises(StoreError, match="exceeds available money"):
        ensure_assignment_allowed(state, "groceries", "2026-10", 85000, "2026-10")
    state["accounts"]["cash"]["balance"] = 0
    ensure_assignment_allowed(state, "groceries", "2026-10", 0, "2026-10")
