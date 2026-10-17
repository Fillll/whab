"""Next-month funding stays atomic, currency scoped and portable."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store

from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport


def test_next_month_funding_survives_reopen_and_json_and_sqlite_roundtrips(tmp_path):
    path = tmp_path / "funding.sqlite"
    original = Store(path, month="2026-10")
    try:
        original.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
        original.add_account("personal", {
            "name": "Canadian cash", "type": "depository", "balance": 20000, "currency": "CAD",
        })
        original.assign_next_month("personal", "2026-10", "USD", 4000)
        original.assign_next_month("personal", "2026-10", "CAD", 7000)
        original.assign_next_month("personal", "2026-11", "USD", 1000)
        snapshot, files = original.export_budget(), original.export_budget_files()
        expected = {"2026-11": {"USD": 4000, "CAD": 7000}, "2026-12": {"USD": 1000}}
        assert original.state("personal")["futureFunding"] == expected
        assert original.state("demo")["futureFunding"] == {}
        assert snapshot["futureFunding"] == [
            {"month": "2026-11", "currency": "CAD", "assigned": 7000},
            {"month": "2026-11", "currency": "USD", "assigned": 4000},
            {"month": "2026-12", "currency": "USD", "assigned": 1000},
        ]
        assert original.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 5000
        assert original.workspace("personal", "2026-11")["summary"]["readyToAssign"] == 9000
    finally:
        original.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.export_budget() == snapshot
        assert reopened.state("personal")["futureFunding"] == expected
    finally:
        reopened.close()
    for binary in (False, True):
        target = Store(":memory:", month="2026-11")
        try:
            if binary:
                target.restore_budget_files("personal", files)
            else:
                target.restore_budget("personal", snapshot)
            assert target.export_budget() == snapshot
            assert target.state("personal")["futureFunding"] == expected
            # A retry recognizes the same records without rewriting the cache.
            changes = target.db.total_changes
            target.prepare_budget_files("personal", files)
            assert target.db.total_changes == changes
        finally:
            target.close()


def test_funding_replaces_previous_floor_and_uses_category_assignments_without_double_counting(store):
    store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.assign_next_month("personal", "2026-10", "USD", 4000)
    store.assign("personal", "rent", "2026-11", 2500)
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 6000
    store.assign_next_month("personal", "2026-10", "USD", 5000)
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 5000
    with pytest.raises(StoreError, match="category assignments"):
        store.assign_next_month("personal", "2026-10", "USD", 0)
    store.assign_next_month("personal", "2026-10", "USD", 2500)
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 7500
    store.assign("personal", "rent", "2026-11", 0)
    store.assign_next_month("personal", "2026-10", "USD", 0)
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 10000
    assert [tuple(row) for row in store.db.execute("SELECT * FROM future_funding WHERE workspace='personal'")] == [
        ("personal", "2026-11", "USD", 0),
    ]


@pytest.mark.parametrize("month,currency,amount", [
    ("2026-09", "USD", 100), ("invalid", "USD", 100), ("2026-10", "USD", -1),
    ("2026-10", "USD", True), ("2026-10", "USD", 1.5), ("2026-10", "usd", 100),
    ("2026-10", "USD", 10001), ("2026-10", "CAD", 1),
])
def test_invalid_or_unfunded_edits_preserve_all_saved_state(store, month, currency, amount):
    store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.assign_next_month("personal", "2026-10", "USD", 4000)
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.assign_next_month("personal", month, currency, amount)
    assert store.state("personal") == before
    assert not store.db.in_transaction


def test_database_failure_rolls_back_floor_replacement(store):
    store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.assign_next_month("personal", "2026-10", "USD", 4000)
    before = store.state("personal")
    store.db.execute("CREATE TRIGGER reject_funding BEFORE INSERT ON future_funding BEGIN SELECT RAISE(ABORT,'fixture funding failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture funding failure"):
        store.assign_next_month("personal", "2026-10", "USD", 5000)
    assert store.state("personal") == before
    assert not store.db.in_transaction


def test_funding_alone_prevents_empty_workspace_file_import_and_restore(store):
    store.db.execute("INSERT INTO future_funding VALUES('personal','2026-11','USD',100)")
    before = store.state("personal")
    assert store.preview_workspace_import("personal", "fixture", "digest")["canImport"] is False
    with pytest.raises(StoreError, match="empty personal workspace"):
        store.import_workspace("personal", "fixture", "digest", WorkspaceImport((), (), (), (), "2026-10"))
    with pytest.raises(StoreError, match="empty personal workspace"):
        store.restore_budget("personal", store.export_budget("demo"))
    assert store.state("personal") == before


@pytest.mark.parametrize("legacy_accounts", [False, True])
def test_portable_images_from_before_funding_remain_supported(store, legacy_accounts):
    store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    snapshot = store.export_budget()
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(store.export_budget_files()["budget.sqlite"])
        connection.execute("DROP TABLE future_funding")
        if legacy_accounts:
            connection.execute("ALTER TABLE accounts DROP COLUMN on_budget")
            connection.execute("ALTER TABLE accounts DROP COLUMN subtype")
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    target = Store(":memory:", month="2026-11")
    try:
        target.restore_budget_files("personal", files)
        assert target.export_budget() == snapshot
        assert target.state("personal")["futureFunding"] == {}
    finally:
        target.close()


def test_legacy_snapshot_without_funding_restores_with_empty_canonical_funding(store):
    snapshot = store.export_budget()
    snapshot.pop("futureFunding")
    store.restore_budget("personal", snapshot)
    assert store.export_budget() == {**snapshot, "futureFunding": []}


@pytest.mark.parametrize("statement", [
    "INSERT INTO future_funding VALUES('personal','invalid','USD',100)",
    "INSERT INTO future_funding VALUES('personal','2026-11','usd',100)",
    "INSERT INTO future_funding VALUES('personal','2026-11','USD',-1)",
    "INSERT INTO future_funding VALUES('personal','2026-11','USD',1.5)",
    "INSERT INTO future_funding VALUES('demo','2026-11','USD',100)",
])
def test_portable_funding_rows_are_revalidated_as_untrusted_data(store, statement):
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(store.export_budget_files()["budget.sqlite"])
        connection.execute(statement)
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.restore_budget_files("personal", files)
    assert store.state("personal") == before


def test_portable_funding_record_count_is_bounded_before_projection(store, monkeypatch):
    from whab_plugin_sqlite import portable

    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(store.export_budget_files()["budget.sqlite"])
        connection.execute("INSERT INTO future_funding VALUES('personal','2026-11','USD',100)")
        connection.execute("INSERT INTO future_funding VALUES('personal','2026-12','USD',100)")
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    monkeypatch.setitem(portable._ROW_LIMITS, "future_funding", 1)
    with pytest.raises(StoreError, match="supported bounds"):
        store.restore_budget_files("personal", files)
