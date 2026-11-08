"""Explicit categorizer provenance remains independent of import and review state."""

from copy import deepcopy

import pytest

from whab.domain import commands
from whab.domain.errors import StoreError
from whab.domain.imports import annotated_transaction
from whab.domain.ledger_state import initial_state, portable_snapshot, restore_snapshot
from whab.domain.portable import validate_portable_budget
from whab.domain.transactions import public_transaction, validate_category_agent


def ledger():
    state = initial_state("personal", "2026-11")
    commands.add_account(
        state, "account", {"name": "Fictional checking", "type": "depository", "balance": 100000}
    )
    commands.add_transaction(
        state,
        "transaction",
        {
            "accountId": "account",
            "date": "2026-11-07",
            "merchant": "Fictional shop",
            "amount": 1250,
            "categoryId": None,
        },
    )
    return state


def test_agent_label_is_explicit_preserved_for_review_and_notes_and_replaced_by_manual_category():
    state = ledger()
    commands.edit_transaction(state, "transaction", "groceries", category_agent="Codex")
    row = state["transactions"]["transaction"]
    assert (
        row["categoryAgent"] == "Codex" and row["source"] == "manual" and row["reviewed"] is False
    )
    commands.edit_transaction(state, "transaction", reviewed=True, note="Reviewed")
    commands.edit_transaction_details(state, "transaction", {"merchant": "Fictional updated shop"})
    assert (
        row["categoryAgent"] == "Codex"
        and state["transactions"]["transaction"]["categoryAgent"] == "Codex"
    )
    assert public_transaction(state["transactions"]["transaction"])["categoryAgent"] == "Codex"
    commands.edit_transaction(state, "transaction", "dining")
    assert "categoryAgent" not in public_transaction(state["transactions"]["transaction"])


@pytest.mark.parametrize(
    "label",
    [
        "",
        " agent",
        "agent ",
        "agent\nname",
        "agent\x00name",
        "agent\x1bname",
        "a" * 81,
        False,
        23,
        {},
    ],
)
def test_invalid_agent_labels_are_atomic(label):
    state = ledger()
    before = deepcopy(state)
    with pytest.raises(StoreError):
        commands.edit_transaction(
            state, "transaction", "groceries", reviewed=True, note="Changed", category_agent=label
        )
    assert state == before


def test_agent_label_alone_cannot_rewrite_attribution_and_null_explicitly_clears_it():
    state = ledger()
    commands.edit_transaction(state, "transaction", "groceries", category_agent="Agent")
    before = deepcopy(state)
    with pytest.raises(StoreError):
        commands.edit_transaction(state, "transaction", category_agent="Other")
    with pytest.raises(StoreError):
        commands.edit_transaction_details(state, "transaction", {"categoryAgent": "Other"})
    assert state == before
    commands.edit_transaction_details(
        state, "transaction", {"categoryId": "dining", "categoryAgent": None}
    )
    assert "categoryAgent" not in state["transactions"]["transaction"]
    assert validate_category_agent(None) is None


def test_agent_attribution_survives_portable_roundtrip_and_import_refresh():
    state = ledger()
    commands.edit_transaction(state, "transaction", "groceries", category_agent="Codex")
    exported = portable_snapshot(state)
    restored = initial_state("personal", "2026-11")
    restore_snapshot(restored, exported)
    assert restored["transactions"]["transaction"]["categoryAgent"] == "Codex"
    incoming = {
        "id": "transaction",
        "accountId": "account",
        "date": "2026-11-07",
        "merchant": "Provider update",
        "amount": 1200,
        "pending": False,
        "source": "plaid",
        "suggestedCategoryId": "dining",
        "pendingTransactionId": None,
        "inferredTransfer": False,
    }
    refreshed = annotated_transaction(incoming, restored["transactions"]["transaction"])
    assert (
        refreshed["categoryAgent"] == "Codex"
        and refreshed["categoryId"] == "groceries"
        and refreshed["source"] == "plaid"
    )
    exported["transactions"][0]["categoryEdited"] = False
    with pytest.raises(StoreError):
        restore_snapshot(restored, exported)


def test_null_agent_in_portable_snapshot_normalizes_to_unlabelled_history():
    exported = portable_snapshot(ledger())
    exported["transactions"][0]["categoryAgent"] = None
    result = validate_portable_budget(exported)
    assert "categoryAgent" not in result["transactions"][0]
    assert exported["transactions"][0]["categoryAgent"] is None
