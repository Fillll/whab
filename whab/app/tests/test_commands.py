"""Core commands run on plain state without a database, SDK, clock or application."""

from copy import deepcopy

import pytest

from whab.domain import commands
from whab.domain.budget import calculate_workspace
from whab.domain.categories import create_category
from whab.domain.errors import StoreError
from whab.domain.money import MAX_CENTS


def ledger():
    return {
        "budgetStartMonth": "2026-10", "accounts": {}, "transactions": {}, "budget": {},
        "categories": {
            "food": create_category("food", "Food", "Everyday", currency="USD"),
            "travel": create_category("travel", "Travel", "Goals", currency="CAD"),
        },
    }


def purchase(account="cash", **changes):
    return {
        "accountId": account, "date": "2026-10-10", "merchant": "  Corner shop  ",
        "amount": 500, "categoryId": "food", "note": "A retained memo", **changes,
    }


def test_commands_apply_complete_cash_credit_and_envelope_flow_to_plain_state():
    state = ledger()
    assert commands.add_account(state, "cash", {
        "name": "  Wallet  ", "type": "depository", "balance": 10000,
    }) == "cash"
    assert commands.add_account(state, "card", {
        "name": "Card", "type": "credit", "balance": 1000,
    }) == "card"
    commands.assign(state, "food", "2026-10", 3000, as_of_month="2026-10")
    assert commands.add_transaction(state, "cash-purchase", purchase()) == "cash-purchase"
    commands.add_transaction(state, "card-purchase", purchase("card", amount=250))
    snapshot = calculate_workspace(state, "2026-10")
    assert state["accounts"]["cash"]["name"] == "Wallet"
    assert state["accounts"]["cash"]["balance"] == 9500
    assert state["accounts"]["card"]["balance"] == 1250
    assert snapshot["summary"]["readyToAssign"] == 6000
    assert next(row for row in snapshot["budget"] if row["categoryId"] == "food")["available"] == 2250
    commands.edit_transaction(state, "cash-purchase", reviewed=True)
    assert state["transactions"]["cash-purchase"]["reviewed"] is True
    assert state["transactions"]["cash-purchase"]["note"] == "A retained memo"
    assert state["transactions"]["cash-purchase"]["merchant"] == "Corner shop"


def test_transfer_annotations_and_source_defaults_are_independent_of_persistence():
    state = ledger()
    commands.add_account(state, "cash", {"name": "Cash", "type": "depository", "balance": 10000})
    commands.add_transaction(state, "fixture", purchase(categoryId=None), source="demo")
    transaction = state["transactions"]["fixture"]
    assert transaction["reviewed"] is True
    assert transaction["categoryEdited"] is False
    transaction.update(inferredTransfer=True, isTransfer=True)
    commands.edit_transaction(state, "fixture", reviewed=False)
    assert transaction["categoryEdited"] is False
    assert transaction["isTransfer"] is True
    commands.edit_transaction(state, "fixture", "food")
    assert transaction["categoryEdited"] is True
    assert transaction["isTransfer"] is False
    commands.edit_transaction(state, "fixture", None)
    assert transaction["isTransfer"] is True
    assert transaction["note"] == "A retained memo"


def test_explicit_as_of_month_and_currency_funding_do_not_use_machine_time():
    state = ledger()
    commands.add_account(state, "cash", {"name": "Cash", "type": "depository", "balance": 1000})
    commands.add_account(state, "canadian", {
        "name": "Canadian cash", "type": "depository", "balance": 1000000, "currency": "CAD",
    })
    commands.assign(state, "food", "2026-10", 400, as_of_month="2026-10")
    commands.assign(state, "food", "2026-11", 600, as_of_month="2026-10")
    before = deepcopy(state)
    with pytest.raises(StoreError, match="exceeds available money"):
        commands.assign(state, "food", "2026-12", 1, as_of_month="2026-10")
    assert state == before
    with pytest.raises(StoreError, match="currency must match"):
        commands.add_transaction(state, "foreign", purchase(categoryId="travel"))
    assert state == before


def test_failed_balance_validation_does_not_partially_mutate_plain_state():
    state = ledger()
    commands.add_account(state, "cash", {"name": "Cash", "type": "depository", "balance": -MAX_CENTS})
    before = deepcopy(state)
    with pytest.raises(StoreError, match="within the supported range"):
        commands.add_transaction(state, "overflow", purchase(amount=1))
    assert state == before


def test_failed_review_validation_does_not_partially_apply_category_annotations():
    state = ledger()
    commands.add_account(state, "cash", {"name": "Cash", "type": "depository", "balance": 1000})
    commands.add_transaction(state, "fixture", purchase(categoryId=None), source="demo")
    before = deepcopy(state)
    with pytest.raises(StoreError, match="Invalid review status"):
        commands.edit_transaction(state, "fixture", "food", reviewed="true")
    assert state == before


def test_memo_edits_preserve_financial_fields_and_allow_explicit_clearing():
    state = ledger()
    commands.add_account(state, "cash", {"name": "Cash", "type": "depository", "balance": 1000})
    commands.add_transaction(state, "fixture", purchase(categoryId=None), source="demo")
    before = deepcopy(state)
    memo = "Receipt: café\n" + "✓" * 986
    assert len(memo) == 1000
    commands.edit_transaction(state, "fixture", note=memo)
    expected = deepcopy(before)
    expected["transactions"]["fixture"]["note"] = memo
    assert state == expected
    commands.edit_transaction(state, "fixture", "food", reviewed=False)
    assert state["transactions"]["fixture"]["note"] == memo
    commands.edit_transaction(state, "fixture", note="")
    assert state["transactions"]["fixture"]["note"] == ""


@pytest.mark.parametrize("note", [None, False, 100, [], {}, "x" * 1001])
def test_invalid_memo_does_not_partially_apply_other_transaction_edits(note):
    state = ledger()
    commands.add_account(state, "cash", {"name": "Cash", "type": "depository", "balance": 1000})
    commands.add_transaction(state, "fixture", purchase(categoryId=None), source="demo")
    before = deepcopy(state)
    with pytest.raises(StoreError, match="Invalid note"):
        commands.edit_transaction(state, "fixture", "food", reviewed=False, note=note)
    assert state == before
