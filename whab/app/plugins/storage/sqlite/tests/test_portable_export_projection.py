"""Fresh portable exports reuse validated snapshots without bypassing validation."""

import pytest
from whab_plugin_sqlite import Store, portable
from whab_plugin_sqlite import store as store_module

from tests.receipt_images import png
from whab.plugins import StoreError


def previous_export(store, workspace):
    """Reference the original public restore route and exact database encoding."""
    snapshot = store.export_budget(workspace)
    images = [(key, store.get_receipt(workspace, key)) for key in store.receipt_ids(workspace)]
    clean = Store(":memory:", month=snapshot["budgetStartMonth"])
    try:
        clean.restore_budget("personal", snapshot)
        for key, image in images:
            clean.db.execute("INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES('personal',?,?)",
                             (key, image))
        for table in ("meta", *portable._ROW_LIMITS):
            clean.db.execute(f"DELETE FROM {table} WHERE workspace <> 'personal'")
        clean.db.execute("VACUUM")
        clean.db.execute("PRAGMA user_version=1")
        return {"budget.sqlite": clean.db.serialize()}
    finally:
        clean.close()


@pytest.fixture
def populated_export(store, imported_account, imported_transaction, sync_changes):
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 100000})
    key = store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Synthetic purchase",
        "amount": 100, "categoryId": "groceries", "note": "Saved annotation",
    })
    store.add_account("personal", {"name": "Yen", "type": "depository", "balance": 1000, "currency": "JPY"})
    store.add_category("personal", {"name": "Yen food", "group": "Travel", "target": 100, "currency": "JPY"})
    store.assign("personal", "groceries", "2026-10", 1000)
    store.assign_next_month("personal", "2026-10", "USD", 2000)
    store.set_rollover_mode("personal", "2026-10", "USD", "balance")
    store.save_item("private-storage-item", "private-storage-token", "Synthetic Bank")
    store.apply_sync("private-storage-item", sync_changes(added=[
        imported_transaction("active"), imported_transaction("removed"),
    ]), "private-storage-cursor", [imported_account(itemId="private-storage-item", mask="9876")])
    store.apply_sync("private-storage-item", sync_changes(removed=["removed"]), "private-final-cursor")
    store.save_receipt("personal", key, png())
    return store


@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_export_is_byte_equivalent_to_public_restore_with_one_full_validation(populated_export, monkeypatch, workspace):
    store = populated_export
    expected = previous_export(store, workspace)
    before = store.state("personal")
    validations = []
    validate = store_module.validate_portable_budget

    def counted_validation(snapshot):
        validations.append(snapshot)
        return validate(snapshot)

    monkeypatch.setattr(store_module, "validate_portable_budget", counted_validation)
    files = store.export_budget_files(workspace)
    assert len(validations) == 1
    assert files == expected
    assert store.state("personal") == before
    for private in (b"private-storage-item", b"private-storage-token", b"private-storage-cursor", b"private-final-cursor"):
        assert private not in files["budget.sqlite"]
    snapshot, images = portable.read_files(files, with_receipts=True)
    assert snapshot["transactions"]
    if workspace == "personal":
        assert images and images[0][1] == png()
        assert next(row for row in snapshot["accounts"] if row["id"] == "bank")["stale"] is True
        assert next(row for row in snapshot["transactions"] if row["id"] == "removed")["deleted"] is True
    else:
        assert images == []


@pytest.mark.parametrize("sql,parameters", [
    ("UPDATE transactions SET amount=? WHERE workspace='personal'", (1.5,)),
    ("UPDATE transactions SET category_id=? WHERE workspace='personal'", ("missing",)),
    ("UPDATE transactions SET date=? WHERE workspace='personal'", ("2026-02-30",)),
    ("UPDATE transactions SET note=? WHERE workspace='personal'", ("x" * 1001,)),
])
def test_export_still_validates_untrusted_runtime_records_before_populating_clean_database(
    populated_export, monkeypatch, sql, parameters,
):
    store = populated_export
    store.db.execute(sql, parameters)
    before = store.state("personal")
    monkeypatch.setattr(Store, "_restore_validated_snapshot",
                        lambda *_: pytest.fail("Invalid runtime data must never populate an export"))
    with pytest.raises(StoreError):
        store.export_budget_files()
    assert store.state("personal") == before


def test_public_restore_keeps_full_validation_and_pristine_workspace_guards(populated_export):
    snapshot = populated_export.export_budget()
    target = Store(":memory:", month="2026-10")
    try:
        before = target.state("personal")
        snapshot["transactions"][0]["amount"] = 1.5
        with pytest.raises(StoreError, match="integer minor units"):
            target.restore_budget("personal", snapshot)
        assert target.state("personal") == before
        snapshot = populated_export.export_budget()
        target.restore_budget("personal", snapshot)
        saved = target.state("personal")
        with pytest.raises(StoreError, match="empty personal workspace"):
            target.restore_budget("personal", snapshot)
        assert target.state("personal") == saved
    finally:
        target.close()
