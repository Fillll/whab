"""Private transaction pictures survive restart and portable images without leaking scopes."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.portable import read_files

from tests.receipt_images import png
from whab.plugins import StoreError


def transaction(store):
    account = store.add_account("personal", {"name": "Synthetic cash", "type": "depository", "balance": 0})
    return store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Synthetic shop",
        "amount": 50, "categoryId": "groceries", "note": "Synthetic memo",
    })


def test_receipts_are_private_isolated_validated_replaceable_and_removable(store, tmp_path):
    transaction_id = transaction(store)
    assert store.receipt_ids("personal") == []
    assert store.get_receipt("personal", transaction_id) is None
    with pytest.raises(StoreError):
        store.receipt_ids("unknown")
    for workspace, key in [("demo", transaction_id), ("personal", "missing"), ("unknown", transaction_id)]:
        for operation in (store.get_receipt, store.delete_receipt):
            with pytest.raises(StoreError):
                operation(workspace, key)
        with pytest.raises(StoreError):
            store.save_receipt(workspace, key, png())
    with pytest.raises(StoreError):
        store.save_receipt("personal", transaction_id, b"not an image")
    store.save_receipt("personal", transaction_id, png())
    assert store.receipt_ids("personal") == [transaction_id]
    assert store.receipt_ids("demo") == []
    store.save_receipt("personal", transaction_id, png(2, 1))
    assert store.get_receipt("personal", transaction_id) == png(2, 1)
    files = store.export_budget_files()
    assert read_files(files) == store.export_budget()
    target = Store(tmp_path / "portable.sqlite", month="2026-11")
    try:
        target.restore_budget_files("personal", files)
        assert target.get_receipt("personal", transaction_id) == png(2, 1)
        assert target.export_budget_files() == files
        target.prepare_budget_files("personal", files)
    finally:
        target.close()
    reopened = Store(tmp_path / "portable.sqlite", month="2026-11")
    try:
        assert reopened.get_receipt("personal", transaction_id) == png(2, 1)
        reopened.delete_receipt("personal", transaction_id)
        reopened.delete_receipt("personal", transaction_id)
        assert reopened.receipt_ids("personal") == []
    finally:
        reopened.close()
    store.delete_receipt("personal", transaction_id)


def test_legacy_portable_schema_without_attachment_table_is_still_readable(store):
    transaction(store)
    files = store.export_budget_files()
    image = sqlite3.connect(":memory:")
    try:
        image.deserialize(files["budget.sqlite"])
        image.execute("DROP TABLE transaction_receipts")
        image.commit()
        legacy = {"budget.sqlite": image.serialize()}
    finally:
        image.close()
    assert read_files(legacy) == store.export_budget()


@pytest.mark.parametrize("transaction_key,image", [("missing", png()), ("fixture", b"<svg/>")])
def test_untrusted_portable_images_validate_attachment_ownership_and_actual_bytes(store, transaction_key, image):
    key = transaction(store)
    files = store.export_budget_files()
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(files["budget.sqlite"])
        connection.execute("INSERT INTO transaction_receipts VALUES(?,?,?)", ("personal", key if transaction_key == "fixture" else transaction_key, image))
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    with pytest.raises(StoreError):
        read_files(files)


def test_pending_receipt_follows_posted_identity_in_same_sync(store, imported_account, imported_transaction, sync_changes):
    store.save_item("item", "synthetic-token", "Synthetic institution")
    store.apply_sync("item", sync_changes(added=[imported_transaction("pending", pending=True)]), "one", [imported_account()])
    store.save_receipt("personal", "pending", png())
    store.apply_sync("item", sync_changes(added=[imported_transaction("posted", pendingTransactionId="pending")], removed=["pending"]), "two")
    assert store.get_receipt("personal", "posted") == png()
    assert store.receipt_ids("personal") == ["posted"]
    assert store.db.execute("SELECT transaction_id FROM transaction_receipts").fetchall()[0][0] == "posted"
    store.delete_receipt("personal", "posted")
    assert store.db.execute("SELECT COUNT(*) FROM transaction_receipts").fetchone()[0] == 0


def test_upload_that_would_break_portability_rolls_back_new_or_replacement_image(store, monkeypatch):
    from whab_plugin_sqlite import portable

    key = transaction(store)
    store.save_receipt("personal", key, png())
    original = store.export_budget_files()
    with monkeypatch.context() as configured:
        configured.setattr(portable, "MAX_DATABASE_BYTES", 100)
        with pytest.raises(StoreError, match="size limit"):
            store.save_receipt("personal", key, png(2, 1))
        assert store.get_receipt("personal", key) == png()
        assert not store.db.in_transaction
    assert store.export_budget_files() == original
    store.delete_receipt("personal", key)
    with monkeypatch.context() as configured:
        configured.setattr(portable, "MAX_DATABASE_BYTES", 100)
        with pytest.raises(StoreError):
            store.save_receipt("personal", key, png())
    assert store.get_receipt("personal", key) is None
