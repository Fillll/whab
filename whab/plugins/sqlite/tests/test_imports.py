"""Atomic import checkpoints and annotation-preserving replay."""

import sqlite3

import pytest

from whab.domain.errors import StoreError


def test_pending_posted_preserves_edits_and_replay_is_unique(
    store, apply_import, imported_account, imported_transaction, save_item, sync_changes
):
    save_item(store)
    apply_import(
        "item", sync_changes([imported_transaction("pending", pending=True)]), "one", [imported_account()]
    )
    assert store.workspace("personal", "2026-10")["summary"]["spent"] == 0
    store.edit_transaction("personal", "pending", "fun", True)
    store.db.execute(
        "UPDATE transactions SET note='Family outing' WHERE id='pending' AND workspace='personal'"
    )
    posted = imported_transaction("posted", amount=1200, pendingTransactionId="pending")
    batch = sync_changes([posted], removed=["pending"])
    apply_import("item", batch, "two", [imported_account()])
    apply_import("item", batch, "two", [imported_account()])
    rows = store.transactions("personal")
    assert len(rows) == 1
    assert (rows[0]["categoryId"], rows[0]["reviewed"], rows[0]["note"], rows[0]["amount"]) == (
        "fun",
        True,
        "Family outing",
        1200,
    )
    apply_import(
        "item",
        sync_changes(modified=[imported_transaction("posted", amount=1300)]),
        "three",
        [imported_account()],
    )
    assert store.transactions("personal")[0]["categoryId"] == "fun"


def test_atomic_batch_and_cursor_rollback_on_invalid_bank_amount(
    store, apply_import, imported_account, imported_transaction, save_item, sync_changes
):
    save_item(store)
    apply_import("item", sync_changes([imported_transaction("prior")]), "one", [imported_account()])
    with pytest.raises(StoreError):
        apply_import(
            "item",
            sync_changes([imported_transaction("new"), imported_transaction("broken", amount="NaN")]),
            "advance",
            [imported_account(99900)],
        )
    assert [row["id"] for row in store.transactions("personal")] == ["prior"]
    assert store.get_item("item")["cursor"] == "one"
    assert store.accounts("personal")[0]["balance"] == 10000
    with pytest.raises(StoreError, match="cursor changed"):
        apply_import("item", sync_changes(), "later", [imported_account()], expected_cursor="stale")
    assert store.get_item("item")["cursor"] == "one"


def test_sql_commit_failure_rolls_back_every_ledger_change(
    store, apply_import, imported_account, imported_transaction, save_item, sync_changes
):
    save_item(store)
    store.db.execute(
        "CREATE TRIGGER fail_import BEFORE INSERT ON transactions WHEN NEW.id='blocked' BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        apply_import(
            "item",
            sync_changes([imported_transaction("valid"), imported_transaction("blocked")]),
            "advance",
            [imported_account(50000)],
        )
    assert store.transactions("personal") == []
    assert store.accounts("personal") == []
    assert store.get_item("item")["cursor"] is None
