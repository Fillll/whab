"""Atomic file imports initialize personal ledgers without rewriting retries."""

import sqlite3
from dataclasses import replace

import pytest
from whab_sqlite import Store

from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport


def file_import():
    return WorkspaceImport(
        accounts=(
            {
                "id": "cash", "name": "Checking", "institution": "Imported file",
                "type": "depository", "balance": 100000, "source": "file-fixture",
                "stale": False, "balanceUnknown": False,
            },
            {
                "id": "tracking", "name": "Tracking account", "institution": "Imported file",
                "type": "other", "balance": 50000000, "source": "manual",
                "stale": True, "balanceUnknown": True,
            },
        ),
        categories=({"id": "food", "name": "Food", "group": "Everyday", "target": 3000},),
        transactions=(
            {
                "id": "purchase", "accountId": "cash", "date": "2026-10-01",
                "merchant": "Fixture market", "amount": 500, "categoryId": "food",
                "pending": False, "reviewed": False, "source": "file-fixture", "note": "Memo",
                "isTransfer": False, "inferredTransfer": False, "categoryEdited": True,
                "deleted": False,
            },
            {
                "id": "income", "accountId": "cash", "date": "2025-10-01",
                "merchant": "Historical income", "amount": -9000000, "categoryId": None,
                "pending": False, "reviewed": True, "source": "file-fixture", "note": "",
                "isTransfer": False, "inferredTransfer": False, "categoryEdited": False,
                "deleted": False,
            },
        ),
        assignments=(
            {"month": "2026-09", "categoryId": "food", "assigned": 3000},
            {"month": "2026-10", "categoryId": "food", "assigned": -100},
        ),
        budget_start_month="2026-09",
    )


def test_import_preserves_snapshot_money_history_annotations_and_demo(store):
    data = file_import()
    demo = store.state("demo")
    assert store.preview_workspace_import("personal", "fixture", "fingerprint") == {
        "canImport": True, "reason": "", "alreadyImported": False,
    }
    result = store.import_workspace("personal", "fixture", "fingerprint", data)
    assert result == {
        "counts": {"accounts": 2, "categories": 1, "transactions": 2, "assignments": 2},
        "alreadyImported": False,
    }
    state = store.state("personal")
    assert tuple(state["accounts"].values()) == tuple(
        {**record, "currency": data.currency} for record in data.accounts
    )
    assert tuple(state["categories"].values()) == tuple(
        {**record, "currency": data.currency} for record in data.categories
    )
    assert tuple(state["transactions"].values()) == data.transactions
    assert state["budget"] == {"2026-09": {"food": 3000}, "2026-10": {"food": -100}}
    assert state["budgetStartMonth"] == "2026-09"
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 97600
    assert store.workspace("personal", "2025-10")["summary"]["income"] == 9000000
    assert store.state("demo") == demo
    data.accounts[0]["balance"] = 0
    assert store.state("personal")["accounts"]["cash"]["balance"] == 100000


def test_identical_retry_is_a_database_noop_and_keeps_original_counts(store):
    data = file_import()
    store.import_workspace("personal", "fixture", "same", data)
    store.edit_transaction("personal", "purchase", None, True)
    store.add_category("personal", {"name": "New category", "group": "Added", "target": 100})
    before = store.state("personal")
    assert store.preview_workspace_import("personal", "fixture", "same") == {
        "canImport": True, "reason": "", "alreadyImported": True,
    }
    for table in ("accounts", "categories", "transactions", "budget"):
        store.db.execute(
            f"CREATE TRIGGER no_retry_{table} BEFORE UPDATE ON {table} "
            "BEGIN SELECT RAISE(ABORT,'retry must not rewrite records'); END"
        )
    result = store.import_workspace("personal", "fixture", "same", data)
    assert result["alreadyImported"] is True
    assert result["counts"]["categories"] == 1
    assert store.state("personal") == before
    assert store.db.execute("SELECT COUNT(*) FROM workspace_imports").fetchone()[0] == 1


@pytest.mark.parametrize("plugin_id,fingerprint", [("other", "same"), ("fixture", "different")])
def test_different_import_cannot_replace_an_imported_workspace(store, plugin_id, fingerprint):
    store.import_workspace("personal", "fixture", "same", file_import())
    before = store.state("personal")
    preview = store.preview_workspace_import("personal", plugin_id, fingerprint)
    assert preview["canImport"] is False
    assert "different file import" in preview["reason"]
    with pytest.raises(StoreError, match="different file import"):
        store.import_workspace("personal", plugin_id, fingerprint, file_import())
    assert store.state("personal") == before


@pytest.mark.parametrize("existing", ["accounts", "transactions", "budget", "items", "disconnected"])
def test_import_requires_a_pristine_personal_workspace(store, existing):
    if existing == "accounts":
        store.add_account("personal", {"name": "Wallet", "type": "depository", "balance": 1})
    elif existing == "transactions":
        store.db.execute(
            "INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source) "
            "VALUES('orphan','personal','legacy','2026-10-01','Fixture',1,'manual')"
        )
    elif existing == "budget":
        store.db.execute(
            "INSERT INTO budget(workspace,month,category_id,assigned) "
            "VALUES('personal','2026-10','groceries',0)"
        )
    else:
        store.save_item("fixture", "synthetic-ciphertext", "Fixture bank")
        if existing == "disconnected":
            store.disconnect_item("fixture")
    before = store.state("personal")
    assert store.preview_workspace_import("personal", "fixture", "same") == {
        "canImport": False,
        "reason": "File imports require an empty personal workspace.",
        "alreadyImported": False,
    }
    with pytest.raises(StoreError, match="empty personal workspace"):
        store.import_workspace("personal", "fixture", "same", file_import())
    assert store.state("personal") == before
    assert not store.db.execute("SELECT 1 FROM workspace_imports").fetchone()


@pytest.mark.parametrize("customized", ["added", "edited"])
def test_customized_categories_are_not_silently_removed(store, customized):
    if customized == "added":
        store.add_category("personal", {"name": "Custom", "group": "Custom", "target": 1})
    else:
        store.db.execute("UPDATE categories SET target=1 WHERE workspace='personal' AND id='fun'")
    before = store.state("personal")
    assert "untouched template categories" in store.preview_workspace_import(
        "personal", "fixture", "same"
    )["reason"]
    with pytest.raises(StoreError, match="untouched template categories"):
        store.import_workspace("personal", "fixture", "same", file_import())
    assert store.state("personal") == before


def test_empty_categories_and_empty_payload_can_initialize_a_workspace(store):
    store.db.execute("DELETE FROM categories WHERE workspace='personal'")
    result = store.import_workspace(
        "personal", "fixture", "empty", WorkspaceImport((), (), (), (), "2026-08")
    )
    assert result["counts"] == {"accounts": 0, "categories": 0, "transactions": 0, "assignments": 0}
    assert store.state("personal")["budgetStartMonth"] == "2026-08"


@pytest.mark.parametrize("workspace", ["demo", "unknown", None, {}])
def test_demo_and_invalid_workspaces_cannot_receive_file_imports(store, workspace):
    assert store.preview_workspace_import(workspace, "fixture", "same")["canImport"] is False
    with pytest.raises(StoreError, match="personal workspace"):
        store.import_workspace(workspace, "fixture", "same", file_import())


@pytest.mark.parametrize("plugin_id,fingerprint", [
    ("", "same"), (None, "same"), ("x" * 65, "same"), ("fixture", ""),
    ("fixture", []), ("fixture", "invalid\x00"), ("fixture", "x" * 201),
])
def test_receipt_identifiers_are_validated(store, plugin_id, fingerprint):
    assert store.preview_workspace_import("personal", plugin_id, fingerprint)["canImport"] is False
    with pytest.raises(StoreError, match="identifier"):
        store.import_workspace("personal", plugin_id, fingerprint, file_import())


def test_payload_is_validated_inside_the_write_transaction(store, monkeypatch):
    import whab_sqlite.store as module

    validate = module.validate_workspace_import
    validated = []

    def observe(data):
        assert store.db.in_transaction
        validated.append(True)
        return validate(data)

    monkeypatch.setattr(module, "validate_workspace_import", observe)
    data = replace(file_import(), budget_start_month="invalid")
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.import_workspace("personal", "fixture", "invalid", data)
    assert validated == [True]
    assert store.state("personal") == before
    assert not store.db.in_transaction


def test_preview_uses_a_read_snapshot_and_preserves_an_outer_transaction(store, monkeypatch):
    before = store.state("personal")
    changes = store.db.total_changes
    check = store._workspace_import_receipt

    def observe(state, plugin_id, fingerprint):
        assert store.db.in_transaction
        return check(state, plugin_id, fingerprint)

    monkeypatch.setattr(store, "_workspace_import_receipt", observe)
    store.db.execute("BEGIN")
    try:
        assert store.preview_workspace_import("personal", "fixture", "same")["canImport"] is True
        assert store.db.in_transaction
        assert store.db.total_changes == changes
    finally:
        store.db.execute("ROLLBACK")
    assert store.state("personal") == before


@pytest.mark.parametrize("table", ["workspace_imports", "transactions"])
def test_failures_roll_back_template_removal_receipt_and_all_imported_records(store, table):
    before = store.state("personal")
    store.db.execute(
        f"CREATE TRIGGER reject_import BEFORE INSERT ON {table} "
        "BEGIN SELECT RAISE(ABORT,'fixture import failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="fixture import failure"):
        store.import_workspace("personal", "fixture", "same", file_import())
    assert store.state("personal") == before
    assert not store.db.execute("SELECT 1 FROM workspace_imports").fetchone()
    assert not store.db.in_transaction
    store.db.execute("DROP TRIGGER reject_import")
    assert store.import_workspace("personal", "fixture", "same", file_import())["alreadyImported"] is False


def test_import_receipts_and_original_start_month_survive_reopening(tmp_path):
    path = tmp_path / "fixture.sqlite"
    ledger = Store(path, month="2026-10")
    try:
        ledger.import_workspace("personal", "fixture", "same", file_import())
    finally:
        ledger.close()
    reopened = Store(path, month="2026-11")
    try:
        result = reopened.import_workspace("personal", "fixture", "same", file_import())
        assert result["alreadyImported"] is True
        assert reopened.state("personal")["budgetStartMonth"] == "2026-09"
    finally:
        reopened.close()
