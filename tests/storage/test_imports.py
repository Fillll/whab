"""Atomic import checkpoints and annotation-preserving replay."""

import sqlite3

import pytest

from whab.domain.errors import StoreError


def test_pending_posted_preserves_edits_and_replay_is_unique(
    store, apply_plaid_sync, bank_account, bank_transaction, save_plaid_item, sync_changes
):
    save_plaid_item(store)
    apply_plaid_sync(
        "item", sync_changes([bank_transaction("pending", pending=True)]), "one", [bank_account()]
    )
    assert store.workspace("personal", "2026-10")["summary"]["spent"] == 0
    store.edit_transaction("personal", "pending", "fun", True)
    store.db.execute(
        "UPDATE transactions SET note='Family outing' WHERE id='pending' AND workspace='personal'"
    )
    posted = bank_transaction("posted", amount=12, pending_transaction_id="pending")
    batch = sync_changes([posted], removed=[{"transaction_id": "pending"}])
    apply_plaid_sync("item", batch, "two", [bank_account()])
    apply_plaid_sync("item", batch, "two", [bank_account()])
    rows = store.transactions("personal")
    assert len(rows) == 1
    assert (rows[0]["categoryId"], rows[0]["reviewed"], rows[0]["note"], rows[0]["amount"]) == (
        "fun",
        True,
        "Family outing",
        1200,
    )
    apply_plaid_sync(
        "item",
        sync_changes(modified=[bank_transaction("posted", amount=13)]),
        "three",
        [bank_account()],
    )
    assert store.transactions("personal")[0]["categoryId"] == "fun"


def test_atomic_batch_and_cursor_rollback_on_invalid_bank_amount(
    store, apply_plaid_sync, bank_account, bank_transaction, save_plaid_item, sync_changes
):
    save_plaid_item(store)
    apply_plaid_sync("item", sync_changes([bank_transaction("prior")]), "one", [bank_account()])
    with pytest.raises(StoreError):
        apply_plaid_sync(
            "item",
            sync_changes([bank_transaction("new"), bank_transaction("broken", amount="NaN")]),
            "advance",
            [bank_account(999)],
        )
    assert [row["id"] for row in store.transactions("personal")] == ["prior"]
    assert store.get_item("item")["cursor"] == "one"
    assert store.accounts("personal")[0]["balance"] == 10000
    with pytest.raises(StoreError, match="cursor changed"):
        apply_plaid_sync("item", sync_changes(), "later", [bank_account()], expected_cursor="stale")
    assert store.get_item("item")["cursor"] == "one"


def test_sql_commit_failure_rolls_back_every_ledger_change(
    store, apply_plaid_sync, bank_account, bank_transaction, save_plaid_item, sync_changes
):
    save_plaid_item(store)
    store.db.execute(
        "CREATE TRIGGER fail_import BEFORE INSERT ON transactions WHEN NEW.id='blocked' BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        apply_plaid_sync(
            "item",
            sync_changes([bank_transaction("valid"), bank_transaction("blocked")]),
            "advance",
            [bank_account(500)],
        )
    assert store.transactions("personal") == []
    assert store.accounts("personal") == []
    assert store.get_item("item")["cursor"] is None
