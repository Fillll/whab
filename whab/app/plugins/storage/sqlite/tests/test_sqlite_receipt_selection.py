"""Visible receipt markers preserve the legacy index without reading private images."""

import random
import sqlite3
from contextlib import closing

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.portable import read_files

from tests.receipt_images import png
from whab.plugins import StoreError

SPECIAL_IDS = ("%", "_", "a'quote", "A", "a", "é", "e\u0301", "雪", "😀", "x" * 200)


def populate(store):
    account = store.add_account("personal", {
        "name": "Fictional receipt account", "type": "depository", "balance": 10000,
    })
    for key in ("posted", "pending", "historical", "deleted", "transfer", "no-receipt", *SPECIAL_IDS):
        store.add_transaction("personal", {
            "id": key, "accountId": account,
            "date": "2018-01-01" if key == "historical" else "2026-10-01",
            "merchant": "Fictional shop", "amount": 50, "categoryId": "groceries",
        })
    store.db.execute("UPDATE transactions SET pending=1 WHERE workspace='personal' AND id='pending'")
    store.db.execute("UPDATE transactions SET deleted=1 WHERE workspace='personal' AND id='deleted'")
    store.db.execute("UPDATE transactions SET is_transfer=1 WHERE workspace='personal' AND id='transfer'")
    store.db.execute("UPDATE accounts SET active=0,source='plaid' WHERE workspace='personal' AND id=?", (account,))
    store.db.executemany(
        "INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES(?,?,?)",
        (("personal", key, png()) for key in ("posted", "pending", "historical", "deleted", "transfer", *SPECIAL_IDS)),
    )
    store.db.executemany(
        "INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source) VALUES(?,?,?,?,?,?,?)",
        ((key, "demo", "disconnected", "2026-10-01", "Fictional shop", 1, "manual") for key in ("posted", "foreign-only")),
    )
    store.db.executemany(
        "INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES(?,?,?)",
        (("demo", key, png(2, 1)) for key in ("posted", "foreign-only")),
    )


@pytest.fixture
def receipt_selection_ledger(store):
    populate(store)
    return store


@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_selection_is_exact_sorted_full_index_intersection(receipt_selection_ledger, workspace):
    store = receipt_selection_ledger
    requested = ["pending", "no-receipt", "missing", "foreign-only", "deleted", "historical", "transfer", "posted", *SPECIAL_IDS]
    full = set(store.receipt_ids(workspace))
    rng = random.Random(20261018)
    for count in (1, 2, 7, len(requested)):
        for _ in range(10):
            selection = rng.sample(requested, count)
            actual = store.receipt_ids_for(workspace, selection)
            assert type(actual) is list
            assert actual == sorted(full.intersection(selection))
    assert store.receipt_ids_for(workspace, tuple(reversed(SPECIAL_IDS))) == sorted(full.intersection(SPECIAL_IDS))


def test_one_and_one_hundred_distinct_ids_are_bounded_without_normalization(receipt_selection_ledger):
    store = receipt_selection_ledger
    assert store.receipt_ids_for("personal", ("pending",)) == ["pending"]
    selected = ("posted", "pending", *[f"missing-{index}" for index in range(98)])
    assert store.receipt_ids_for("personal", selected) == ["pending", "posted"]
    assert selected[0] == "posted" and len(selected) == 100


@pytest.mark.parametrize("workspace", ["invalid", "", None, [], True, 1])
def test_workspace_validation_precedes_selection_and_database_access(store, workspace):
    store.db.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
    try:
        with pytest.raises(StoreError, match="^Invalid workspace\\.$"):
            store.receipt_ids_for(workspace, [])
    finally:
        store.db.set_authorizer(None)


@pytest.mark.parametrize("selection", [
    [], (), ["x"] * 101, ["posted", "posted"], [""], ["x" * 201],
    ["x\x00"], ["\ud800"], ["\udfff"], [1], [True], [None], "posted", None, {"posted"}, b"posted",
])
def test_shared_selection_validation_runs_before_any_database_access(store, selection):
    store.db.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
    try:
        with pytest.raises(StoreError):
            store.receipt_ids_for("personal", selection)
    finally:
        store.db.set_authorizer(None)


def test_index_does_not_depend_on_workspace_metadata_or_account_and_planning_tables(receipt_selection_ledger):
    store = receipt_selection_ledger
    selected = ("posted", "historical", "pending", "deleted", "foreign-only")
    expected = sorted(set(store.receipt_ids("personal")).intersection(selected))
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    for table in ("accounts", "categories", "budget", "future_funding", "rollover_modes", "items"):
        store.db.execute(f"DROP TABLE {table}")
    assert store.receipt_ids_for("personal", selected) == expected
    assert store.receipt_ids_for("personal", selected) == sorted(set(store.receipt_ids("personal")).intersection(selected))
    assert not store.db.in_transaction


def test_orphan_receipts_are_omitted_like_the_full_index(receipt_selection_ledger):
    store = receipt_selection_ledger
    store.db.execute("PRAGMA foreign_keys=OFF")
    store.db.execute("INSERT INTO transaction_receipts VALUES('personal','orphan',?)", (png(),))
    store.db.execute("PRAGMA foreign_keys=ON")
    assert store.receipt_ids_for("personal", ("orphan", "posted")) == ["posted"]
    assert "orphan" not in store.receipt_ids("personal")


def test_selection_is_one_locked_read_without_blobs_history_private_data_or_schema_changes(receipt_selection_ledger, monkeypatch):
    store = receipt_selection_ledger
    for method in ("state", "transactions", "accounts", "categories", "get_receipt", "receipt_ids", "_require_workspace"):
        monkeypatch.setattr(store, method, lambda *_: pytest.fail("Selection must only read visible marker identities"))
    reads, statements = [], []
    allowed = {
        ("transaction_receipts", "workspace"), ("transaction_receipts", "transaction_id"),
        ("transactions", "workspace"), ("transactions", "id"), ("transactions", "deleted"),
    }

    class ReadLock:
        entered = False

        def __enter__(self):
            self.entered = True

        def __exit__(self, *_):
            self.entered = False

    lock = ReadLock()
    monkeypatch.setattr(store, "_lock", lock)

    def authorize(action, table, column, _database, _trigger):
        assert lock.entered
        if action == sqlite3.SQLITE_READ:
            reads.append((table, column))
            return sqlite3.SQLITE_OK if (table, column) in allowed else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action == sqlite3.SQLITE_SELECT else sqlite3.SQLITE_DENY

    original, changes = store.db.serialize(), store.db.total_changes
    store.db.set_authorizer(authorize)
    store.db.set_trace_callback(statements.append)
    try:
        assert store.receipt_ids_for("personal", ("pending", "historical", "deleted", "missing")) == ["historical", "pending"]
    finally:
        store.db.set_authorizer(None)
        store.db.set_trace_callback(None)
    assert set(reads) == allowed
    assert len(statements) == 1 and statements[0].startswith("SELECT ")
    assert store.db.serialize() == original and store.db.total_changes == changes
    plan = [row["detail"] for row in store.db.execute("EXPLAIN QUERY PLAN " + statements[0])]
    assert any("SEARCH r" in detail and "workspace=? AND transaction_id=?" in detail for detail in plan)
    assert any("SEARCH t" in detail and "workspace=? AND id=?" in detail for detail in plan)


def test_selection_respects_an_outer_snapshot_and_new_commits_after_it(tmp_path):
    store = Store(tmp_path / "selection.sqlite", month="2026-10")
    try:
        populate(store)
        selected = ("posted", "pending", "no-receipt")
        with closing(sqlite3.connect(store.database_path)) as writer:
            with store._snapshot():
                assert store.receipt_ids_for("personal", selected) == ["pending", "posted"]
                with writer:
                    writer.execute("DELETE FROM transaction_receipts WHERE workspace='personal' AND transaction_id='posted'")
                    writer.execute("UPDATE transactions SET deleted=1 WHERE workspace='personal' AND id='pending'")
                    writer.execute("INSERT INTO transaction_receipts VALUES('personal','no-receipt',?)", (png(),))
                assert store.receipt_ids_for("personal", selected) == ["pending", "posted"]
                assert store.db.in_transaction
            assert store.receipt_ids_for("personal", selected) == ["no-receipt"]
        assert not store.db.in_transaction
    finally:
        store.close()


def test_marker_queries_preserve_receipt_bytes_and_exact_portable_payload(receipt_selection_ledger):
    store = receipt_selection_ledger
    files, private = store.export_budget_files(), store.db.serialize()
    assert store.receipt_ids_for("personal", ("posted", "pending", "historical", "no-receipt")) == ["historical", "pending", "posted"]
    assert store.get_receipt("personal", "pending") == png()
    assert store.get_receipt("demo", "posted") == png(2, 1)
    assert store.db.serialize() == private and store.export_budget_files() == files
    restored, receipts = read_files(files, with_receipts=True)
    assert restored == store.export_budget()
    assert [key for key, _ in receipts] == store.receipt_ids("personal")
    assert all(image == png() for _, image in receipts)
