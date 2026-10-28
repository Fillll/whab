"""Manual entries belong to any account; account origin does not limit ledger edits."""

from copy import deepcopy

import pytest

from whab.domain.bank_sync import merge_sync
from whab.domain.commands import add_account, add_transaction
from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state


@pytest.mark.parametrize("origin", ["manual", "demo", "ynab", "plaid", "custom-feed"])
@pytest.mark.parametrize("kind,expected", [("depository", 9750), ("credit", 10250), ("investment", 9750), ("loan", 10250)])
def test_manual_entry_works_for_every_origin_and_account_scope(origin, kind, expected):
    state = initial_state("personal", "2026-10")
    add_account(state, "account", {"name": "Fictional account", "type": kind, "balance": 10000})
    account = state["accounts"]["account"]
    account.update(source=origin, stale=True, balanceUnknown=True)
    metadata = deepcopy(account)
    add_transaction(state, "manual-entry", {"accountId": "account", "date": "2026-10-01", "merchant": "Fictional shop", "amount": 250, "categoryId": "groceries", "note": "Entered by hand"})
    assert account == {**metadata, "balance": expected}
    row = state["transactions"]["manual-entry"]
    assert row["source"] == "manual" and row["categoryEdited"] and not row["pending"]
    assert row["note"] == "Entered by hand" and row["categoryId"] == "groceries"
    add_transaction(state, "refund", {"accountId": "account", "date": "2026-10-02", "merchant": "Fictional refund", "amount": -250, "categoryId": "groceries"})
    assert account == metadata


def test_connected_bank_refresh_preserves_manual_entries_and_connection_ownership():
    state = initial_state("personal", "2026-10")
    state["items"]["item"] = {"id": "item", "provider": "plaid", "token": "fictional-ciphertext", "cursor": None}
    account = {"id": "bank", "name": "Fictional bank", "type": "depository", "balance": 10000, "currency": "USD", "source": "plaid", "itemId": "item", "institution": "Fictional Bank"}
    empty = {"added": [], "modified": [], "removed": []}
    merge_sync(state, "item", empty, "first", [account], synced_at="2026-10-01T00:00:00Z")
    add_transaction(state, "manual-entry", {"accountId": "bank", "date": "2026-10-02", "merchant": "Hand-entered purchase", "amount": 250, "categoryId": "groceries", "note": "Preserve this"})
    manual = deepcopy(state["transactions"]["manual-entry"])
    assert state["accounts"]["bank"]["balance"] == 9750
    merge_sync(state, "item", empty, "second", [{**account, "balance": 9500}], synced_at="2026-10-03T00:00:00Z")
    assert state["transactions"]["manual-entry"] == manual
    assert state["accounts"]["bank"]["source"] == "plaid"
    assert state["accounts"]["bank"]["itemId"] == "item"
    assert state["accounts"]["bank"]["balance"] == 9500
    with pytest.raises(StoreError, match="does not belong"):
        merge_sync(state, "item", {**empty, "removed": ["manual-entry"]}, "third", [account], synced_at="2026-10-04T00:00:00Z")
    assert state["transactions"]["manual-entry"] == manual
