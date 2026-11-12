"""Native bulk evidence and database rollback retain exact financial state."""

import sqlite3
from uuid import uuid4

import pytest
from whab_plugin_sqlite import Store

from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    native_state_digest,
)


def test_explicit_parent_order_is_identical_in_cached_and_reopened_native_receipts(tmp_path):
    path = tmp_path / "fictional.sqlite"
    store = Store(path, month="2026-11")
    try:
        account = store.add_account(
            "personal", {"name": "Fictional", "type": "other", "onBudget": False, "balance": 10000}
        )
        store.add_category_group("personal", "Fictional empty parent")
        transaction = store.add_transaction(
            "personal",
            {
                "accountId": account,
                "date": "2026-11-01",
                "merchant": "Fictional",
                "amount": 100,
                "categoryId": None,
            },
        )
        for name, edits in [
            ("edit_transactions", [{"transactionId": transaction, "note": "exact"}]),
            ("classify_accounts", [{"accountId": account, "type": "credit"}]),
        ]:
            before = store.commit_evidence()
            operation = StorageOperation(
                uuid4().hex,
                before.authority_epoch,
                before.generation,
                "personal",
                name,
                canonical_storage_json({"edits": edits}),
            )
            saved = store.execute_operation(operation)
            actual = native_state_digest(
                store._local_states(),
                chunks=lambda value, limit: (canonical_storage_json(value, limit=limit),),
            )
            assert saved.receipt.evidence.state_digest == actual
        evidence = store.commit_evidence()
    finally:
        store.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.commit_evidence() == evidence
        assert reopened.execute_operation(operation).receipt == saved.receipt
    finally:
        reopened.close()


def test_sql_failure_after_first_row_rolls_back_all_rows_history_generation_and_receipt(tmp_path):
    store = Store(tmp_path / "fictional.sqlite", month="2026-11")
    try:
        account = store.add_account(
            "personal", {"name": "Fictional", "type": "depository", "balance": 10000}
        )
        ids = [
            store.add_transaction(
                "personal",
                {
                    "accountId": account,
                    "date": "2026-11-01",
                    "merchant": "Fictional",
                    "amount": 100,
                    "categoryId": None,
                },
            )
            for _ in range(2)
        ]
        # This owning-backend fault fixture fails SQL after one validated update.
        store.db.execute(
            "CREATE TRIGGER fictional_failure BEFORE UPDATE OF note ON transactions WHEN NEW.id='"
            + ids[1]
            + "' BEGIN SELECT RAISE(ABORT,'fictional disk-path fault'); END"
        )
        before = store.export_budget("personal")
        evidence = store.commit_evidence()
        operation = StorageOperation(
            uuid4().hex,
            evidence.authority_epoch,
            evidence.generation,
            "personal",
            "edit_transactions",
            canonical_storage_json(
                {"edits": [{"transactionId": key, "note": "must roll back"} for key in ids]}
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="fictional"):
            store.execute_operation(operation)
        assert store.export_budget("personal") == before
        assert store.commit_evidence() == evidence
        assert store.operation_receipt(operation.operation_id) is None
    finally:
        store.close()
