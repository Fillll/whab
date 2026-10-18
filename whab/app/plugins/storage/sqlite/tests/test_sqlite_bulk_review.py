"""Native review batches preserve core rules, isolated fields and atomic commits."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from threading import Event

import pytest
from whab_plugin_sqlite import Store, bulk_review
from whab_plugin_sqlite.portable import read_files

from tests.receipt_images import png
from whab.domain import commands
from whab.plugins import StoreError


def populate(store):
    accounts = []
    categories = {"USD": "groceries"}
    for currency in ("USD", "EUR", "JPY"):
        accounts.append(store.add_account("personal", {
            "name": currency + " synthetic cash", "type": "depository", "balance": 10000,
            "currency": currency,
        }))
        if currency != "USD":
            categories[currency] = store.add_category("personal", {
                "name": currency + " synthetic food", "group": currency + " living", "currency": currency,
            })
    for index in range(9):
        store.add_transaction("personal", {
            "id": f"bulk-{index}", "accountId": accounts[index % 3],
            "date": "2020-01-01" if index == 6 else "2026-10-01",
            "merchant": "Synthetic café", "amount": 50,
            "categoryId": None if index == 6 else categories[("USD", "EUR", "JPY")[index % 3]],
            "note": f"Retain memo {index}",
        })
    store.db.execute("UPDATE transactions SET pending=1 WHERE workspace='personal' AND id='bulk-2'")
    store.db.execute("UPDATE transactions SET deleted=1 WHERE workspace='personal' AND id='bulk-8'")
    store.db.execute(
        "UPDATE transactions SET is_transfer=1,inferred_transfer=1,category_edited=0,merchant_logo='synthetic-logo' "
        "WHERE workspace='personal' AND id='bulk-1'",
    )
    store.save_item("private-item", "synthetic-private-ciphertext", "Synthetic institution")
    store.db.execute("UPDATE items SET cursor='synthetic-private-cursor' WHERE id='private-item'")
    store.db.execute(
        "UPDATE accounts SET source='plaid',active=0,item_id='private-item',mask='4321',"
        "bank_website='https://example.com',name_overridden=1 WHERE workspace='personal' AND id=?", (accounts[1],),
    )
    store.save_receipt("personal", "bulk-0", png())
    demo_account = store.accounts("demo")[0]["id"]
    for key in ("demo-only", "bulk-0"):
        store.add_transaction("demo", {
            "id": key, "accountId": demo_account, "date": "2026-10-01",
            "merchant": "Synthetic demo purchase", "amount": 1, "categoryId": None,
        })


@pytest.fixture
def review_ledger(store):
    populate(store)
    return store


@pytest.mark.parametrize("workspace", ["personal", "demo"])
@pytest.mark.parametrize("reviewed", [True, False])
@pytest.mark.parametrize("collection", [list, tuple])
def test_batch_matches_core_and_preserves_other_fields_scopes_and_images(review_ledger, workspace, reviewed, collection):
    store = review_ledger
    selected = ["bulk-0", "bulk-1", "bulk-2", "bulk-6"] if workspace == "personal" else [
        row["id"] for row in store.transactions("demo")[:3]
    ]
    before = store.state(workspace)
    expected = deepcopy(before)
    for key in selected:
        commands.edit_transaction(expected, key, reviewed=reviewed)
    public = store.export_budget(workspace)
    for row in public["transactions"]:
        if row["id"] in selected:
            row["reviewed"] = reviewed
    other = "demo" if workspace == "personal" else "personal"
    unrelated = store.state(other)
    images = [tuple(row) for row in store.db.execute("SELECT * FROM transaction_receipts ORDER BY workspace,transaction_id")]
    changes = store.db.total_changes
    assert store.review_transactions(workspace, collection(selected), reviewed) is None
    assert store.db.total_changes == changes + len(selected)
    assert store.state(workspace) == expected
    assert store.state(other) == unrelated
    assert store.export_budget(workspace) == public
    assert [tuple(row) for row in store.db.execute("SELECT * FROM transaction_receipts ORDER BY workspace,transaction_id")] == images
    assert not store.db.in_transaction


@pytest.mark.parametrize("last", ["missing", "bulk-8", "demo-only"])
def test_rejected_target_rolls_back_entire_batch_before_any_update(review_ledger, last):
    before, changes = review_ledger.db.serialize(), review_ledger.db.total_changes
    statements = []
    review_ledger.db.set_trace_callback(statements.append)
    try:
        with pytest.raises(StoreError, match="Transaction not found"):
            review_ledger.review_transactions("personal", ["bulk-0", "bulk-1", last], True)
    finally:
        review_ledger.db.set_trace_callback(None)
    assert not any(sql.startswith("UPDATE") for sql in statements)
    assert statements[-1] == "ROLLBACK"
    assert review_ledger.db.serialize() == before and review_ledger.db.total_changes == changes
    assert not review_ledger.db.in_transaction


@pytest.mark.parametrize("transaction_ids,reviewed", [
    ([], True), (["bulk-0"] * 101, True), (["bulk-0", "bulk-0"], True),
    ((key for key in ["bulk-0"]), True), (["bulk-0", []], True),
    (["bulk-0", 1], True), (["bulk-0", "bad\x00id"], True),
    (["bulk-0"], 1), (["bulk-0"], None),
])
def test_backend_reuses_shared_strict_batch_validation(review_ledger, transaction_ids, reviewed):
    before, changes = review_ledger.db.serialize(), review_ledger.db.total_changes
    with pytest.raises(StoreError):
        review_ledger.review_transactions("personal", transaction_ids, reviewed)
    assert review_ledger.db.serialize() == before and review_ledger.db.total_changes == changes
    assert not review_ledger.db.in_transaction


@pytest.mark.parametrize("workspace", ["invalid", None, [], True])
def test_workspace_and_metadata_errors_precede_invalid_batch_arguments(store, workspace):
    with pytest.raises(StoreError, match="Invalid workspace"):
        store.review_transactions(workspace, [], None)
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="Workspace not found"):
        store.review_transactions("personal", [], None)
    assert not store.db.in_transaction


def test_metadata_validation_keeps_annotation_path_error_precedence(review_ledger):
    review_ledger.db.execute("UPDATE accounts SET subtype='mortgage' WHERE workspace='personal'")
    before = review_ledger.db.serialize()
    with pytest.raises(StoreError, match="Invalid account subtype"):
        review_ledger.review_transactions("personal", ["missing"], True)
    assert review_ledger.db.serialize() == before and not review_ledger.db.in_transaction


@pytest.mark.parametrize("failure", [RuntimeError("Core validation failed"), KeyboardInterrupt()])
def test_core_exception_or_cancellation_rolls_back_without_writing(review_ledger, monkeypatch, failure):
    original = commands.edit_transaction
    calls = []

    def validate(state, key, **changes):
        calls.append(key)
        if len(calls) == 2:
            raise failure
        original(state, key, **changes)

    monkeypatch.setattr(commands, "edit_transaction", validate)
    before = review_ledger.db.serialize()
    with pytest.raises(type(failure)):
        review_ledger.review_transactions("personal", ["bulk-0", "bulk-1"], True)
    assert calls == ["bulk-0", "bulk-1"]
    assert review_ledger.db.serialize() == before and not review_ledger.db.in_transaction


def test_sql_update_failure_rolls_back_all_selected_flags(review_ledger):
    review_ledger.db.execute(
        "CREATE TRIGGER reject_bulk_review BEFORE UPDATE ON transactions "
        "WHEN NEW.id='bulk-1' BEGIN SELECT RAISE(ABORT,'Synthetic batch failure'); END",
    )
    before = review_ledger.db.serialize()
    with pytest.raises(sqlite3.IntegrityError, match="Synthetic batch failure"):
        review_ledger.review_transactions("personal", ["bulk-0", "bulk-1"], True)
    assert review_ledger.db.serialize() == before and not review_ledger.db.in_transaction


def test_commit_failure_rolls_back_selected_flags(review_ledger):
    before = review_ledger.db.serialize()

    def authorize(action, operation, _column, _database, _trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT" else sqlite3.SQLITE_OK

    review_ledger.db.set_authorizer(authorize)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            review_ledger.review_transactions("personal", ["bulk-0", "bulk-1"], True)
    finally:
        review_ledger.db.set_authorizer(None)
    assert review_ledger.db.serialize() == before and not review_ledger.db.in_transaction


def test_batch_reads_metadata_and_targets_once_and_updates_only_reviewed(review_ledger, monkeypatch):
    for method in ("state", "transactions", "_write_state"):
        monkeypatch.setattr(review_ledger, method, lambda *_: pytest.fail("Batch must not load or rewrite full history"))
    original = bulk_review.validate_review_batch
    calls, writes, statements = [], [], []

    def validate(ids, reviewed):
        calls.append((ids, reviewed))
        return original(ids, reviewed)

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "items":
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_UPDATE:
            writes.append((table, column))
            return sqlite3.SQLITE_OK if (table, column) == ("transactions", "reviewed") else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    monkeypatch.setattr(bulk_review, "validate_review_batch", validate)
    indexes = [tuple(row) for row in review_ledger.db.execute("SELECT name,sql FROM sqlite_schema WHERE type='index' ORDER BY name")]
    review_ledger.db.set_authorizer(authorize)
    review_ledger.db.set_trace_callback(statements.append)
    try:
        review_ledger.review_transactions("personal", ["bulk-0", "bulk-1"], True)
    finally:
        review_ledger.db.set_authorizer(None)
        review_ledger.db.set_trace_callback(None)
    assert calls == [(["bulk-0", "bulk-1"], True)]
    assert writes == [("transactions", "reviewed")]
    assert sum("FROM accounts" in sql for sql in statements) == 1
    assert sum("FROM categories" in sql for sql in statements) == 1
    assert sum("FROM transactions" in sql for sql in statements) == 1
    assert sum(sql.startswith("UPDATE transactions SET reviewed=") for sql in statements) == 1
    assert statements[0] == "BEGIN IMMEDIATE" and statements[-1] == "COMMIT"
    assert [tuple(row) for row in review_ledger.db.execute("SELECT name,sql FROM sqlite_schema WHERE type='index' ORDER BY name")] == indexes


def test_hundred_distinct_ids_are_supported_without_touching_unselected_rows(review_ledger):
    account = review_ledger.accounts("personal")[0]["id"]
    selected = [f"boundary-{index}" for index in range(100)]
    review_ledger.db.executemany(
        "INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source) "
        "VALUES(?,'personal',?,'2026-10-01','Synthetic boundary',1,'manual')",
        [(key, account) for key in selected],
    )
    before = review_ledger.state("personal")
    review_ledger.review_transactions("personal", selected, True)
    for key in selected:
        commands.edit_transaction(before, key, reviewed=True)
    assert review_ledger.state("personal") == before


def test_atomic_batch_matches_separate_core_edits_and_exact_portable_payload(review_ledger, tmp_path):
    legacy = Store(tmp_path / "legacy-reviews.sqlite", month="2026-10")
    try:
        with legacy._snapshot():
            legacy._write_state(review_ledger.state("personal"))
            legacy.db.executemany("INSERT INTO transaction_receipts VALUES(?,?,?)", [tuple(row) for row in review_ledger.db.execute("SELECT * FROM transaction_receipts")])
        selected = ["bulk-0", "bulk-1", "bulk-2", "bulk-6"]
        for key in selected:
            legacy.edit_transaction("personal", key, reviewed=True)
        review_ledger.review_transactions("personal", selected, True)
        assert review_ledger.state("personal") == legacy.state("personal")
        files = review_ledger.export_budget_files()
        assert files == legacy.export_budget_files()
        snapshot, images = read_files(files, with_receipts=True)
        assert snapshot == review_ledger.export_budget()
        assert images == [("bulk-0", png())]
        assert b"synthetic-private-ciphertext" not in files["budget.sqlite"]
        assert b"synthetic-private-cursor" not in files["budget.sqlite"]
    finally:
        legacy.close()


def test_batch_holds_write_lock_and_other_connection_sees_atomic_flags(tmp_path, monkeypatch):
    store = Store(tmp_path / "concurrent-review.sqlite", month="2026-10")
    entered, release = Event(), Event()
    original = commands.edit_transaction

    def pause(state, key, **changes):
        if key == "bulk-0":
            entered.set()
            assert release.wait(5)
        original(state, key, **changes)

    try:
        populate(store)
        monkeypatch.setattr(commands, "edit_transaction", pause)
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(store.review_transactions, "personal", ["bulk-0", "bulk-1"], True)
            try:
                assert entered.wait(5)
                with closing(sqlite3.connect(store.database_path, timeout=0, isolation_level=None)) as observer:
                    assert observer.execute("SELECT reviewed FROM transactions WHERE workspace='personal' AND id IN ('bulk-0','bulk-1') ORDER BY id").fetchall() == [(0,), (0,)]
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        observer.execute("UPDATE transactions SET note='Concurrent memo' WHERE workspace='personal' AND id='bulk-1'")
            finally:
                release.set()
            assert future.result(timeout=5) is None
        with closing(sqlite3.connect(store.database_path, isolation_level=None)) as observer:
            assert observer.execute("SELECT reviewed FROM transactions WHERE workspace='personal' AND id IN ('bulk-0','bulk-1') ORDER BY id").fetchall() == [(1,), (1,)]
            observer.execute("UPDATE transactions SET note='Concurrent memo' WHERE workspace='personal' AND id='bulk-1'")
        assert store.state("personal")["transactions"]["bulk-1"]["note"] == "Concurrent memo"
    finally:
        release.set()
        store.close()
