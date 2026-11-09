"""Targeted annotation writes retain the core command's validation and atomicity."""

import sqlite3
from copy import deepcopy

import pytest
from whab_plugin_sqlite.transaction_edits import edit_transaction

from whab.domain import commands
from whab.plugins import UNSET, StoreError


@pytest.fixture
def annotated_transaction(store):
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    transaction = store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Synthetic purchase",
        "amount": 100, "categoryId": None, "note": "Original memo",
    })
    store.db.execute(
        "UPDATE transactions SET inferred_transfer=1,is_transfer=1,merchant_logo='synthetic-logo' "
        "WHERE workspace='personal' AND id=?", (transaction,),
    )
    return transaction


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("changes", [
    {}, {"reviewed": True}, {"reviewed": False}, {"note": "Updated memo"}, {"note": ""},
    {"category_id": "groceries"}, {"category_id": None},
    {"category_id": "fun", "reviewed": True, "note": "Combined edit"},
])
def test_targeted_edit_matches_core_without_changing_other_records(store, annotated_transaction, changes, pending):
    store.db.execute("UPDATE transactions SET pending=? WHERE workspace='personal' AND id=?",
                     (int(pending), annotated_transaction))
    before = store.state("personal")
    receipt = b"Synthetic receipt bytes retained by annotation edits"
    store.db.execute("INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES(?,?,?)",
                     ("personal", annotated_transaction, receipt))
    expected = deepcopy(before)
    commands.edit_transaction(expected, annotated_transaction, **changes)
    count = store.db.total_changes
    assert edit_transaction(store, "personal", annotated_transaction, **changes) is None
    # The addressed annotation and its durable authority generation commit together.
    actual = store.state('personal')
    changed_history = actual.get('history') != before.get('history')
    assert store.db.total_changes == count + 2 + int(changed_history)
    assert actual['history']['entries'][:len(before['history']['entries'])] == before['history']['entries']
    expected['history'] = actual['history']
    assert actual == expected
    assert store.db.execute(
        "SELECT image FROM transaction_receipts WHERE workspace='personal' AND transaction_id=?",
        (annotated_transaction,),
    ).fetchone()[0] == receipt
    assert not store.db.in_transaction
    assert store.state("demo")["transactions"] != expected["transactions"]


@pytest.mark.parametrize("changes, message", [
    ({"category_id": "missing", "reviewed": True, "note": "Not saved"}, "Category not found"),
    ({"category_id": "category-group:parent"}, "Category not found"),
    ({"category_id": []}, "Category not found"),
    ({"category_id": "fun", "reviewed": "true"}, "Invalid review status"),
    ({"category_id": "fun", "reviewed": True, "note": 123}, "Invalid note"),
    ({"reviewed": True, "note": "x" * 1001}, "Invalid note"),
])
def test_rejected_edits_roll_back_all_annotations(store, annotated_transaction, changes, message):
    before, count = store.state("personal"), store.db.total_changes
    with pytest.raises(StoreError, match=message):
        edit_transaction(store, "personal", annotated_transaction, **changes)
    assert store.state("personal") == before
    assert store.db.total_changes == count
    assert not store.db.in_transaction


def test_currency_validation_and_inferred_transfer_restoration_remain_core_owned(store, annotated_transaction):
    category = store.add_category("personal", {"name": "Euro food", "group": "Euro", "currency": "EUR"})
    before = store.state("personal")
    with pytest.raises(StoreError, match="Category currency must match"):
        edit_transaction(store, "personal", annotated_transaction, category, True, "Not saved")
    assert store.state("personal") == before
    edit_transaction(store, "personal", annotated_transaction, "groceries", True, "User note")
    categorized = store.state("personal")["transactions"][annotated_transaction]
    assert categorized["isTransfer"] is False and categorized["categoryEdited"] is True
    edit_transaction(store, "personal", annotated_transaction, None)
    restored = store.state("personal")["transactions"][annotated_transaction]
    assert restored["isTransfer"] is True and restored["categoryEdited"] is True
    assert restored["note"] == "User note" and restored["reviewed"] is True


@pytest.mark.parametrize("transaction_id", ["missing", None, 123, b"123"])
def test_missing_and_nontext_ids_preserve_core_not_found_error(store, transaction_id):
    with pytest.raises(StoreError, match="Transaction not found"):
        edit_transaction(store, "personal", transaction_id, reviewed=True)
    assert not store.db.in_transaction


def test_unhashable_id_preserves_core_error_and_rolls_back(store):
    with pytest.raises(TypeError, match="unhashable"):
        edit_transaction(store, "personal", [], reviewed=True)
    assert not store.db.in_transaction


def test_deleted_rows_and_cross_workspace_ids_cannot_be_edited(store, annotated_transaction):
    demo_id = store.transactions("demo")[0]["id"]
    with pytest.raises(StoreError, match="Transaction not found"):
        edit_transaction(store, "personal", demo_id, reviewed=True)
    store.db.execute("UPDATE transactions SET deleted=1 WHERE workspace='personal' AND id=?", (annotated_transaction,))
    before = store.state("personal")
    with pytest.raises(StoreError, match="Transaction not found"):
        edit_transaction(store, "personal", annotated_transaction, reviewed=True)
    assert store.state("personal") == before


@pytest.mark.parametrize("workspace", ["invalid", None, []])
def test_invalid_and_missing_workspaces_fail_without_writes(store, workspace):
    count = store.db.total_changes
    with pytest.raises(StoreError, match="Invalid workspace"):
        edit_transaction(store, workspace, "missing", reviewed=True)
    assert store.db.total_changes == count and not store.db.in_transaction
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="Workspace not found"):
        edit_transaction(store, "personal", "missing", reviewed=True)
    assert not store.db.in_transaction


def test_update_failure_rolls_back_and_keeps_original_transaction(store, annotated_transaction):
    before = store.state("personal")
    store.db.execute(
        "CREATE TRIGGER reject_annotation_update BEFORE UPDATE ON transactions "
        "BEGIN SELECT RAISE(ABORT, 'Synthetic write failure'); END",
    )
    with pytest.raises(sqlite3.IntegrityError, match="Synthetic write failure"):
        edit_transaction(store, "personal", annotated_transaction, "groceries", True, "Not saved")
    assert store.state("personal") == before
    assert not store.db.in_transaction


def test_edit_uses_one_scoped_update_without_loading_full_state(store, annotated_transaction, monkeypatch):
    edit_transaction(store, "personal", annotated_transaction, note="Warm history baseline")
    queries = []
    store.db.set_trace_callback(queries.append)
    monkeypatch.setattr(store, "state", lambda *_: pytest.fail("Annotations must not load ledger history"))
    monkeypatch.setattr(store, "_write_state", lambda *_: pytest.fail("Annotations must not rewrite the ledger"))
    edit_transaction(store, "personal", annotated_transaction, UNSET, True, "Memo")
    store.db.set_trace_callback(None)
    assert sum(query.startswith("UPDATE transactions") for query in queries) == 1
    assert not any(query.startswith(("INSERT", "DELETE")) and "budget_history" not in query for query in queries)
    assert sum("FROM transactions" in query for query in queries) == 2
    assert queries[0] == "BEGIN IMMEDIATE" and queries[-1] == "COMMIT"


def test_history_head_cache_invalidates_external_writes_and_accounts_for_sql_triggers(store, annotated_transaction):
    edit_transaction(store, 'personal', annotated_transaction, note='Warm baseline')
    # Direct legacy SQL is outside the commit scope; the next edit must read the
    # actual state and record the gap before its ordinary reversible entry.
    store.db.execute('UPDATE transactions SET merchant=? WHERE id=?', ('Fictional external edit', annotated_transaction))
    edit_transaction(store, 'personal', annotated_transaction, note='After external edit')
    journal = store.export_budget('personal')['history']
    assert journal['entries'][-2]['operation'] == 'import_workspace'
    assert journal['entries'][-1]['changes'][0]['before']['merchant'] == 'Fictional external edit'
    account = store.accounts('personal')[0]['id']
    second = store.add_transaction('personal', {'accountId':account,'date':'2026-10-02','merchant':'Fictional second','amount':100,'categoryId':None})
    store.db.execute("CREATE TRIGGER touch_other AFTER UPDATE OF reviewed ON transactions BEGIN UPDATE transactions SET note='Fictional trigger' WHERE id='" + second + "'; END")
    edit_transaction(store, 'personal', annotated_transaction, reviewed=True)
    assert len(store.budget_history('personal', target=store.budget_history('personal')['entries'][0]['id'])['entries'][0]['changes']) == 2


def test_history_cache_reloads_after_another_connection_commits(tmp_path):
    from whab_plugin_sqlite import Store
    path = tmp_path / 'fictional-cache.sqlite'
    store = Store(path, month='2026-10')
    try:
        account = store.add_account('personal', {'name':'Fictional','type':'depository','balance':1000})
        transaction = store.add_transaction('personal', {'accountId':account,'date':'2026-10-01','merchant':'Fictional','amount':100,'categoryId':None})
        edit_transaction(store, 'personal', transaction, note='Warm cache')
        other = sqlite3.connect(path)
        try:
            other.execute('UPDATE transactions SET merchant=? WHERE id=?', ('Fictional second connection', transaction))
            other.commit()
        finally:
            other.close()
        edit_transaction(store, 'personal', transaction, note='New change')
        assert store.budget_history('personal')['entries'][1]['operation'] == 'import_workspace'
    finally:
        store.close()
