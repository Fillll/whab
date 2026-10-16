"""Portable SQLite ledgers preserve history atomically without provider credentials."""

import json
import sqlite3

import pytest
from whab_sqlite import Store

from whab.domain.errors import StoreError


def populated(store, imported_account, imported_transaction, sync_changes):
    store.add_account("personal", {
        "name": "Travel cash", "type": "depository", "balance": 100000, "currency": "JPY",
    })
    cash = store.accounts("personal")[0]["id"]
    food = store.add_category("personal", {
        "name": "Food", "group": "Travel", "target": 1234, "currency": "JPY",
    })
    store.add_transaction("personal", {
        "id": "historical", "accountId": cash, "date": "2020-01-02", "merchant": "Old shop",
        "amount": 200, "categoryId": food, "note": "Preserve exact history",
    })
    store.assign("personal", food, "2026-10", 300)
    store.db.execute(
        "INSERT INTO budget VALUES('personal','2026-09',?,-10)", (food,),
    )
    store.save_item("item", "never-export-this-token", "Private bank")
    store.apply_sync("item", sync_changes(added=[
        imported_transaction("old", merchantLogo="market-1"),
        imported_transaction("removed", amount=250),
    ]), "never-export-this-cursor", [imported_account()])
    store.edit_transaction("personal", "old", "groceries", True)
    store.db.execute("UPDATE transactions SET note='User memo' WHERE id='old'")
    store.apply_sync("item", sync_changes(removed=["removed"]), "final-secret-cursor")
    return cash, food


def test_roundtrip_preserves_currency_history_annotations_and_demo_without_credentials(
    store, imported_account, imported_transaction, sync_changes,
):
    cash, food = populated(store, imported_account, imported_transaction, sync_changes)
    original = store.state("personal")
    snapshot = store.export_budget()
    encoded = json.dumps(snapshot)
    for forbidden in ("itemId", "mask", "token", "cursor", "items", "never-export", "final-secret"):
        assert forbidden not in encoded
    assert snapshot["transactions"][2]["deleted"] is True
    assert store.state("personal") == original
    restored = Store(":memory:", month="2026-11")
    try:
        demo = restored.state("demo")
        restored.restore_budget("personal", snapshot)
        state = restored.state("personal")
        expected = store.export_budget()
        next(record for record in expected["accounts"] if record["id"] == "bank")["stale"] = True
        assert restored.export_budget() == expected
        assert state["items"] == {}
        assert state["accounts"]["bank"]["stale"] is True
        assert state["accounts"][cash]["stale"] is False
        assert state["budget"]["2026-09"][food] == -10
        assert state["transactions"]["old"]["note"] == "User memo"
        assert state["transactions"]["old"]["categoryEdited"] is True
        assert state["transactions"]["old"]["reviewed"] is True
        assert state["transactions"]["historical"]["amount"] == 200
        assert state["budgetStartMonth"] == original["budgetStartMonth"]
        assert restored.state("demo") == demo
        # Portable JSON is detached from both stores, including nested records.
        snapshot["transactions"][0]["note"] = "Changed outside the store"
        assert restored.state("personal")["transactions"]["historical"]["note"] == "Preserve exact history"
    finally:
        restored.close()


def test_empty_snapshot_and_explicit_demo_exports_are_supported(store):
    personal = store.export_budget()
    demo = store.export_budget("demo")
    assert personal["accounts"] == personal["transactions"] == personal["assignments"] == []
    assert demo["accounts"] and demo["transactions"] and demo["assignments"]
    store.restore_budget("personal", personal)
    assert store.export_budget() == personal


@pytest.mark.parametrize("workspace", ["demo", "unknown", None, {}])
def test_restore_requires_personal_workspace_without_touching_demo(store, workspace):
    before = store.state("demo")
    with pytest.raises(StoreError, match="personal workspace"):
        store.restore_budget(workspace, store.export_budget())
    assert store.state("demo") == before


@pytest.mark.parametrize("existing", ["account", "categories", "connection", "receipt", "matching-receipt"])
def test_restore_refuses_existing_or_previously_imported_data(store, existing):
    snapshot = store.export_budget("demo")
    if existing == "account":
        store.add_account("personal", {"name": "Existing", "type": "depository", "balance": 0})
    elif existing == "categories":
        store.add_category("personal", {"name": "Existing", "group": "Mine"})
    elif existing == "connection":
        store.save_item("existing", "fixture-secret", "Fixture")
    else:
        plugin, fingerprint = ("portable-budget", "restore") if existing == "matching-receipt" else ("fixture", "old")
        store.db.execute(
            "INSERT INTO workspace_imports VALUES('personal',?,?,0,0,0,0)", (plugin, fingerprint),
        )
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.restore_budget("personal", snapshot)
    assert store.state("personal") == before
    assert not store.db.in_transaction


def test_invalid_snapshot_is_validated_inside_transaction_and_cannot_change_saved_data(store, monkeypatch):
    import whab_sqlite.store as module

    validate = module.validate_portable_budget
    observed = []

    def check(data):
        assert store.db.in_transaction
        observed.append(True)
        return validate(data)

    monkeypatch.setattr(module, "validate_portable_budget", check)
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.restore_budget("personal", {"items": [{"token": "secret"}]})
    assert observed == [True]
    assert store.state("personal") == before


def test_database_failure_rolls_back_template_removal_and_imported_records(store):
    snapshot = store.export_budget("demo")
    before = store.state("personal")
    store.db.execute(
        "CREATE TRIGGER reject_restore BEFORE INSERT ON transactions "
        "BEGIN SELECT RAISE(ABORT,'fixture restore failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="fixture restore failure"):
        store.restore_budget("personal", snapshot)
    assert store.state("personal") == before
    assert not store.db.in_transaction
    store.db.execute("DROP TRIGGER reject_restore")
    store.restore_budget("personal", snapshot)
    assert store.export_budget() == snapshot


def test_restored_history_and_start_month_survive_reopening(tmp_path):
    path = tmp_path / "restored.sqlite"
    ledger = Store(path, month="2026-10")
    snapshot = ledger.export_budget("demo")
    try:
        ledger.restore_budget("personal", snapshot)
    finally:
        ledger.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.export_budget() == snapshot
        assert reopened.items() == []
    finally:
        reopened.close()
