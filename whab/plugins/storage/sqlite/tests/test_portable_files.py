"""SQLite owns and validates its opaque budget image without importing connections."""

import sqlite3
from copy import deepcopy
from types import MappingProxyType

import pytest
from whab_sqlite import Store

from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport


@pytest.fixture
def payload(store):
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.add_transaction("personal", {
        "id": "fixture", "accountId": account, "date": "2026-10-01", "merchant": "Shop",
        "amount": 50, "categoryId": "groceries", "note": "Retain this annotation",
    })
    return store.export_budget_files()


def altered(payload, statements):
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(payload["budget.sqlite"])
        for sql, parameters in statements:
            connection.execute(sql, parameters)
        connection.commit()
        return {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()


def test_binary_roundtrip_preserves_currencies_history_annotations_and_demo_without_connections(
    store, imported_account, imported_transaction, sync_changes,
):
    account = store.add_account("personal", {
        "name": "Canadian cash", "type": "depository", "balance": 100000, "currency": "CAD",
    })
    category = store.add_category("personal", {"name": "Travel", "group": "Goals", "currency": "CAD"})
    store.add_transaction("personal", {
        "id": "historical", "accountId": account, "date": "2020-01-01", "merchant": "Old shop",
        "amount": 100, "categoryId": category, "note": "Historical note",
    })
    store.assign("personal", category, "2026-10", 200)
    store.db.execute("INSERT INTO budget VALUES('personal','2026-09',?,-10)", (category,))
    store.save_item("item", "binary-export-must-not-contain-this-token", "Fixture")
    store.apply_sync("item", sync_changes(added=[imported_transaction("purchase")]),
                     "binary-export-must-not-contain-this-cursor", [imported_account()])
    store.edit_transaction("personal", "purchase", "groceries", True)
    store.db.execute("UPDATE transactions SET note='User bank annotation' WHERE id='purchase'")
    store.apply_sync("item", sync_changes(removed=["purchase"]), "private-final-cursor")
    before = deepcopy(store.state("personal"))
    files = store.export_budget_files()
    assert files == store.export_budget_files()
    assert set(files) == {"budget.sqlite"}
    raw = files["budget.sqlite"]
    assert raw.startswith(b"SQLite format 3\x00")
    for forbidden in (b"binary-export-must-not-contain-this-token", b"private-final-cursor"):
        assert forbidden not in raw
    image = sqlite3.connect(":memory:")
    try:
        image.deserialize(raw)
        assert image.execute("SELECT workspace FROM meta").fetchall() == [("personal",)]
        assert image.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert image.execute("SELECT COUNT(*) FROM workspace_imports").fetchone()[0] == 0
        assert image.execute("SELECT item_id,mask FROM accounts").fetchall() == [(None, None), (None, None)]
    finally:
        image.close()
    target = Store(":memory:", month="2026-11")
    try:
        demo = target.state("demo")
        target.restore_budget_files("personal", MappingProxyType(files))
        expected = store.export_budget()
        next(row for row in expected["accounts"] if row["id"] == "bank")["stale"] = True
        assert target.export_budget() == expected
        assert target.state("demo") == demo
        assert target.state("personal")["items"] == {}
        assert target.state("personal")["transactions"]["purchase"]["deleted"] is True
        assert target.state("personal")["transactions"]["purchase"]["note"] == "User bank annotation"
        assert store.state("personal") == before
    finally:
        target.close()


def test_empty_and_explicit_demo_images_use_the_same_owned_contract(store):
    empty = store.export_budget_files()
    store.restore_budget_files("personal", empty)
    demo = store.export_budget_files("demo")
    target = Store(":memory:", month="2026-10")
    try:
        target.restore_budget_files("personal", demo)
        assert target.export_budget() == store.export_budget("demo")
    finally:
        target.close()


@pytest.mark.parametrize("files", [
    None, [], {}, {"other.sqlite": b""}, {"../budget.sqlite": b""},
    {"budget.sqlite": b"", "extra": b""}, {"budget.sqlite": b""},
    {"budget.sqlite": "text"}, {"budget.sqlite": b"not SQLite"},
    {"budget.sqlite": bytearray(b"SQLite format 3\x00")},
    {"budget.sqlite": b"SQLite format 3\x00" + b"\x00" * 100},
])
def test_wrong_payload_keys_types_headers_and_damaged_images_are_rejected(store, files):
    before = store.state("personal")
    with pytest.raises(StoreError):
        store.restore_budget_files("personal", files)
    assert store.state("personal") == before


def test_database_image_size_is_bounded_on_export_and_read(store, monkeypatch):
    from whab_sqlite import portable

    files = store.export_budget_files()
    monkeypatch.setattr(portable, "MAX_DATABASE_BYTES", 100)
    with pytest.raises(StoreError, match="size limit"):
        store.export_budget_files()
    with pytest.raises(StoreError, match="size limit"):
        store.restore_budget_files("personal", files)


@pytest.mark.parametrize("statement", [
    "CREATE TABLE unexpected(data TEXT)",
    "CREATE VIEW unexpected AS SELECT * FROM accounts",
    "CREATE TRIGGER unexpected AFTER INSERT ON accounts BEGIN DELETE FROM categories; END",
    "ALTER TABLE accounts ADD COLUMN unexpected TEXT",
    "DROP INDEX tx_month",
    "PRAGMA user_version=2",
])
def test_untrusted_schema_objects_migrations_and_versions_are_rejected_before_ledger_restore(store, payload, statement):
    files = altered(payload, [(statement, ())])
    target = Store(":memory:", month="2026-10")
    try:
        before = target.state("personal")
        with pytest.raises(StoreError, match="unsupported database schema"):
            target.restore_budget_files("personal", files)
        assert target.state("personal") == before
        assert not target.db.execute("SELECT 1 FROM sqlite_schema WHERE name='unexpected'").fetchone()
    finally:
        target.close()


@pytest.mark.parametrize("statement,parameters", [
    ("INSERT INTO items(id,token,institution_name,cursor) VALUES('item','synthetic','Fixture','cursor')", ()),
    ("INSERT INTO workspace_imports VALUES('personal','file','digest',0,0,0,0)", ()),
    ("UPDATE accounts SET item_id='private-item'", ()),
    ("UPDATE accounts SET mask='1234'", ()),
])
def test_connection_records_and_import_receipts_cannot_be_hidden_in_images(store, payload, statement, parameters):
    files = altered(payload, [(statement, parameters)])
    before = store.state("personal")
    with pytest.raises(StoreError, match="banking connections"):
        store.restore_budget_files("personal", files)
    assert store.state("personal") == before


@pytest.mark.parametrize("statement", [
    "DELETE FROM meta", "UPDATE meta SET workspace='other'",
    "INSERT INTO meta(workspace,budget_start_month) VALUES('demo','2026-10')",
    "UPDATE accounts SET workspace='demo'",
])
def test_images_cannot_smuggle_additional_or_foreign_workspaces(store, payload, statement):
    with pytest.raises(StoreError):
        store.restore_budget_files("personal", altered(payload, [(statement, ())]))


def test_row_counts_are_bounded_before_loading_ledger(store, payload):
    statement = """WITH RECURSIVE numbers(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM numbers WHERE n<1000)
        INSERT INTO accounts(id,workspace,name,institution,type,balance,source)
        SELECT 'extra-'||n,'personal','Cash','Manual','depository',1,'manual' FROM numbers"""
    files = altered(payload, [(statement, ())])
    with pytest.raises(StoreError, match="supported bounds"):
        store.restore_budget_files("personal", files)


@pytest.mark.parametrize("statement,parameters", [
    ("UPDATE accounts SET active=?", (2,)),
    ("UPDATE accounts SET balance_unknown=?", ("yes",)),
    ("UPDATE transactions SET pending=?", (2,)),
    ("UPDATE transactions SET reviewed=?", ("yes",)),
])
def test_database_flags_are_validated_before_bool_projection(store, payload, statement, parameters):
    with pytest.raises(StoreError, match="integer booleans"):
        store.restore_budget_files("personal", altered(payload, [(statement, parameters)]))


def test_money_and_references_are_revalidated_after_reading_database_rows(store, payload):
    for statement in ("UPDATE transactions SET amount=1.5", "UPDATE transactions SET account_id='absent'"):
        with pytest.raises(StoreError):
            store.restore_budget_files("personal", altered(payload, [(statement, ())]))


def test_constraint_damage_is_rejected_by_database_integrity_check(store, payload):
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(payload["budget.sqlite"])
        original = connection.execute("SELECT sql FROM sqlite_schema WHERE name='accounts'").fetchone()[0]
        version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute("UPDATE sqlite_schema SET sql=? WHERE name='accounts'", (original.replace("name TEXT NOT NULL", "name TEXT"),))
        connection.execute(f"PRAGMA schema_version={version + 1}")
        connection.execute("UPDATE accounts SET name=NULL")
        connection.execute("UPDATE sqlite_schema SET sql=? WHERE name='accounts'", (original,))
        connection.execute(f"PRAGMA schema_version={version + 2}")
        connection.commit()
        files = {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()
    with pytest.raises(StoreError, match="database is damaged"):
        store.restore_budget_files("personal", files)


@pytest.mark.parametrize("workspace", ["demo", "unknown", None, {}])
def test_only_personal_workspace_can_receive_binary_payload(store, payload, workspace):
    before = store.state("demo")
    with pytest.raises(StoreError, match="personal workspace"):
        store.restore_budget_files(workspace, payload)
    with pytest.raises(StoreError, match="personal workspace"):
        store.prepare_budget_files(workspace, payload)
    assert store.state("demo") == before


def test_restore_image_refuses_existing_data_and_rolls_back_database_failures(store, payload):
    before = store.state("personal")
    with pytest.raises(StoreError, match="empty personal workspace"):
        store.restore_budget_files("personal", payload)
    assert store.state("personal") == before
    target = Store(":memory:", month="2026-10")
    try:
        pristine = target.state("personal")
        target.db.execute("CREATE TRIGGER reject_restore BEFORE INSERT ON transactions BEGIN SELECT RAISE(ABORT,'fixture restore failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="fixture restore failure"):
            target.restore_budget_files("personal", payload)
        assert target.state("personal") == pristine
        assert not target.db.in_transaction
    finally:
        target.close()


def test_prepare_first_open_is_idempotent_after_restore_commit_and_does_not_rewrite_live_tokens(store, payload):
    target = Store(":memory:", month="2026-11")
    try:
        target.prepare_budget_files("personal", payload)
        first = target.state("personal")
        changes = target.db.total_changes
        target.prepare_budget_files("personal", payload)
        assert target.db.total_changes == changes
        assert target.state("personal") == first
        target.save_item("local", "synthetic-machine-only-token", "Fixture")
        private = target.get_item("local")
        target.prepare_budget_files("personal", payload)
        assert target.get_item("local") == private
        target.add_account("personal", {"name": "Different", "type": "depository", "balance": 1})
        before = target.state("personal")
        with pytest.raises(StoreError):
            target.prepare_budget_files("personal", payload)
        assert target.state("personal") == before
    finally:
        target.close()


def test_preparing_existing_cache_keeps_newer_ledger_and_connections_but_always_checks_image(store, payload):
    store.add_account("personal", {"name": "Newer local account", "type": "depository", "balance": 2})
    store.save_item("local", "synthetic-machine-only-token", "Fixture")
    before = store.state("personal")
    store.prepare_budget_files("personal", payload, existing=True)
    assert store.state("personal") == before
    with pytest.raises(StoreError):
        store.prepare_budget_files("personal", {"budget.sqlite": b"not sqlite"}, existing=True)
    assert store.state("personal") == before


def test_initial_retry_recognizes_detached_provider_state_without_disconnecting_live_bank(
    store, imported_account, sync_changes,
):
    store.save_item("item", "synthetic-machine-only-token", "Fixture")
    store.apply_sync("item", sync_changes(), "private-machine-cursor", [imported_account()])
    files = store.export_budget_files()
    before = store.state("personal")
    changes = store.db.total_changes
    store.prepare_budget_files("personal", files)
    assert store.state("personal") == before
    assert store.db.total_changes == changes


def test_preparing_empty_replacement_cache_rehydrates_payload_and_preserves_demo(store, payload):
    target = Store(":memory:", month="2026-11")
    try:
        demo = target.state("demo")
        target.prepare_budget_files("personal", payload, existing=True)
        assert target.export_budget() == store.export_budget()
        assert target.state("demo") == demo
    finally:
        target.close()


def test_prior_empty_migration_receipt_is_an_authoritative_existing_cache(store, payload):
    target = Store(":memory:", month="2026-10")
    try:
        target.import_workspace("personal", "portable-budget-files", "restore", WorkspaceImport((), (), (), (), "2026-10"))
        before = target.state("personal")
        target.prepare_budget_files("personal", payload, existing=True)
        assert target.state("personal") == before
    finally:
        target.close()


@pytest.mark.parametrize("existing", [None, 1, "true"])
def test_prepare_cache_flag_is_an_exact_boolean(store, payload, existing):
    with pytest.raises(StoreError, match="boolean cache flag"):
        store.prepare_budget_files("personal", payload, existing=existing)
