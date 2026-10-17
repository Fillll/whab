"""SQLite-owned portable images, rebuilt from validated ledger data only."""

import sqlite3
from collections.abc import Mapping

from whab.domain.errors import StoreError

from .store import Store

MAX_DATABASE_BYTES = 50 * 1024 * 1024
_FILENAME = "budget.sqlite"
_FORMAT_VERSION = 1
_ROW_LIMITS = {"accounts": 1000, "categories": 10000, "transactions": 250000, "budget": 250000, "future_funding": 250000}


def _schema(connection):
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )
    ]


def export_files(store, workspace):
    """Serialize a fresh database that has never contained connection credentials."""
    snapshot = store.export_budget(workspace)
    clean = Store(":memory:", month=snapshot["budgetStartMonth"])
    try:
        clean.restore_budget("personal", snapshot)
        # The image belongs to this budget; demo seeding is supplied by the host
        # installation and must not travel as a second ledger inside its payload.
        for table in ("meta", *_ROW_LIMITS):
            clean.db.execute(f"DELETE FROM {table} WHERE workspace <> 'personal'")
        clean.db.execute("VACUUM")
        clean.db.execute(f"PRAGMA user_version={_FORMAT_VERSION}")
        raw = clean.db.serialize()
        if len(raw) > MAX_DATABASE_BYTES:
            raise StoreError("This SQLite budget exceeds the portable database size limit.")
        return {_FILENAME: raw}
    finally:
        clean.close()


def _validate_rows(connection, *, legacy_accounts, legacy_funding):
    meta = connection.execute("SELECT workspace FROM meta").fetchall()
    if len(meta) != 1 or meta[0][0] != "personal":
        raise StoreError("SQLite budget images must contain exactly one personal ledger.")
    for table, limit in _ROW_LIMITS.items():
        if legacy_funding and table == "future_funding":
            continue
        count, foreign = connection.execute(
            f"SELECT COUNT(*),COALESCE(SUM(workspace <> 'personal'),0) FROM {table}"
        ).fetchone()
        if count > limit or foreign:
            raise StoreError("SQLite budget records exceed the supported bounds.")
    if (
        connection.execute("SELECT 1 FROM items LIMIT 1").fetchone()
        or connection.execute("SELECT 1 FROM workspace_imports LIMIT 1").fetchone()
        or connection.execute(
            "SELECT 1 FROM accounts WHERE item_id IS NOT NULL OR mask IS NOT NULL LIMIT 1"
        ).fetchone()
    ):
        raise StoreError("SQLite budget images cannot contain banking connections or credentials.")
    for table, columns in (
        ("accounts", "active,balance_unknown" if legacy_accounts else "active,balance_unknown,on_budget"),
        ("transactions", "pending,reviewed,is_transfer,category_edited,deleted,inferred_transfer"),
    ):
        for row in connection.execute(f"SELECT {columns} FROM {table}"):
            if any(type(value) is not int or value not in (0, 1) for value in row):
                raise StoreError("SQLite budget flags must be integer booleans.")


def read_files(files):
    """Treat untrusted SQLite rows as data; never execute their schema or migrations."""
    if not isinstance(files, Mapping) or set(files) != {_FILENAME}:
        raise StoreError("This SQLite budget requires exactly one budget.sqlite payload.")
    raw = files[_FILENAME]
    if (
        type(raw) is not bytes or not raw or len(raw) > MAX_DATABASE_BYTES
        or not raw.startswith(b"SQLite format 3\x00")
    ):
        raise StoreError("This SQLite budget payload is invalid or exceeds the size limit.")
    reader = Store(":memory:", month="2026-10")
    try:
        expected_schema = _schema(reader.db)
        # Earlier format-1 images predate account scope. Keep their exact trusted
        # schema allowlisted and infer defaults while reading their data only.
        legacy_schema = [
            (*row[:3], row[3].replace(
                ", on_budget INTEGER NOT NULL DEFAULT 1, subtype TEXT", ""
            )) if row[1] == "accounts" else row
            for row in expected_schema
        ]
        # Funding floors were added independently of account scope, so accept
        # both older exact schemas without executing their stored SQL.
        legacy_funding_schema = [row for row in expected_schema if row[2] != "future_funding"]
        legacy_both_schema = [row for row in legacy_schema if row[2] != "future_funding"]
        reader.db.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        reader.db.execute("PRAGMA trusted_schema=OFF")
        reader.db.execute("PRAGMA query_only=ON")
        reader.db.deserialize(raw)
        actual_schema = _schema(reader.db)
        if (
            reader.db.execute("PRAGMA user_version").fetchone()[0] != _FORMAT_VERSION
            or actual_schema not in (expected_schema, legacy_schema, legacy_funding_schema, legacy_both_schema)
        ):
            raise StoreError("This SQLite budget has an unsupported database schema.")
        if [row[0] for row in reader.db.execute("PRAGMA quick_check")] != ["ok"]:
            raise StoreError("This SQLite budget database is damaged.")
        _validate_rows(
            reader.db,
            legacy_accounts=actual_schema in (legacy_schema, legacy_both_schema),
            legacy_funding=actual_schema in (legacy_funding_schema, legacy_both_schema),
        )
        return reader.export_budget()
    except sqlite3.Error:
        raise StoreError("This SQLite budget database is invalid or damaged.") from None
    finally:
        reader.close()


def _semantic(snapshot):
    """Compare portable records independently of SQLite row order/connections."""
    return {
        **snapshot,
        "accounts": sorted([
            {**account, "stale": account["stale"] or account["source"] not in {"manual", "demo"}}
            for account in snapshot["accounts"]
        ], key=lambda account: account["id"]),
        "categories": sorted(snapshot["categories"], key=lambda category: category["id"]),
        "transactions": sorted(snapshot["transactions"], key=lambda transaction: transaction["id"]),
        "assignments": sorted(snapshot["assignments"], key=lambda assignment: (assignment["month"], assignment["categoryId"])),
        "futureFunding": sorted(snapshot["futureFunding"], key=lambda funding: (funding["month"], funding["currency"])),
    }


def prepare_files(store, files, *, existing):
    snapshot = read_files(files)
    if existing:
        with store._snapshot():
            state = store._read_state("personal")
            try:
                receipt = store._workspace_import_receipt(state, "portable-budget-files", "restore")
            except StoreError:
                return
        if receipt is not None:
            return
    elif _semantic(store.export_budget()) == _semantic(snapshot):
        return
    store.restore_budget("personal", snapshot)
