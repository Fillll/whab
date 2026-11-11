"""SQLite-owned portable images, rebuilt from validated ledger data only."""

import json
import sqlite3
from collections.abc import Mapping

from whab.plugins import StoreError, receipt_media_type

from .store import Store

MAX_DATABASE_BYTES = 50 * 1024 * 1024
_FILENAME = "budget.sqlite"
_FORMAT_VERSION = 1
_ADDITIVE_INDEXES = ("tx_register", "tx_register_account", "tx_account_history", "tx_uncategorized", "tx_uncategorized_recent")
_ROW_LIMITS = {"accounts": 1000, "categories": 10000, "transactions": 250000, "budget": 250000, "future_funding": 250000, "rollover_modes": 250000, "transaction_receipts": 10000, "budget_history": 1}


def _schema(connection):
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )
    ]



def _without_additive_columns(schema):
    """Recognize only the exact new additive columns without schema products."""
    return [(*row[:3], row[3].replace(", closed INTEGER NOT NULL DEFAULT 0", "")) if row[1] == 'accounts'
            else (*row[:3], row[3].replace(", closed_account_cursors TEXT", "")) if row[1] == 'items'
            else (*row[:3], row[3].replace(", category_groups TEXT", "")) if row[1] == "meta"
            else row for row in schema]


def _incremental_image(store, workspace, cached):
    stamp, raw, previous = cached
    heads = getattr(store, '_history_heads', {})
    current_stamp = (store.db.total_changes, store.db.execute("PRAGMA data_version").fetchone()[0])
    if stamp == current_stamp:
        return raw
    if getattr(store, '_history_stamp', None) != current_stamp or workspace not in heads:
        return None
    journal = heads[workspace].get('history')
    entries = journal.get('entries', []) if journal else []
    positions = [i for i, entry in enumerate(entries) if entry['hash'] == previous]
    if len(positions) != 1:
        return None
    changes = entries[positions[0] + 1:]
    if not changes or any(entry['operation'] not in {'edit_transaction', 'edit_transaction_details', 'update_account'}
                          or any(change['path'][0] not in {'accounts', 'transactions'} or change['before'] is None or change['after'] is None
                                 for change in entry['changes']) for entry in changes):
        return None
    # Deserialize only an image previously built from allowlisted, validated data.
    # The private source database is never copied into a portable image.
    clean = sqlite3.connect(':memory:')
    try:
        clean.deserialize(raw)
        for entry in changes:
            for change in entry['changes']:
                row = change['after']
                if change['path'][0] == 'accounts':
                    clean.execute("UPDATE accounts SET name=?,type=?,balance=?,currency=?,on_budget=?,subtype=?,bank_website=?,name_overridden=?,closed=? WHERE workspace=? AND id=?",
                                  (row['name'], row['type'], row['balance'], row['currency'], int(row['onBudget']), row['subtype'], row.get('bankWebsite'), int(row.get('nameOverridden', False)), int(row.get('closed', False)), 'personal', row['id']))
                else:
                    clean.execute("UPDATE transactions SET account_id=?,date=?,merchant=?,merchant_logo=?,amount=?,category_id=?,pending=?,reviewed=?,source=?,note=?,is_transfer=?,inferred_transfer=?,category_edited=?,deleted=?,original_details=?,category_agent=? WHERE workspace=? AND id=?",
                                  (row['accountId'], row['date'], row['merchant'], row['merchantLogo'], row['amount'], row['categoryId'], int(row['pending']), int(row['reviewed']), row['source'], row['note'], int(row['isTransfer']), int(row['inferredTransfer']), int(row['categoryEdited']), int(row['deleted']), json.dumps(row['original']) if 'original' in row else None, row.get('categoryAgent'), 'personal', row['id']))
        from types import SimpleNamespace

        from .history_writes import save_history
        holder = SimpleNamespace(db=clean, _history_blob_positions=getattr(store, '_portable_history_positions', {}).get(workspace, {}))
        previous = {**journal, 'entries': entries[:positions[0] + 1]}
        save_history(holder, 'personal', journal, previous, cache_valid=True)
        store._portable_history_positions[workspace] = holder._history_blob_positions
        clean.commit()
        raw = clean.serialize()
        if len(raw) > MAX_DATABASE_BYTES:
            clean.execute("VACUUM")
            raw = clean.serialize()
            hint = store._portable_history_positions[workspace]['personal']
            rowid = clean.execute("SELECT rowid FROM budget_history WHERE workspace='personal'").fetchone()[0]
            store._portable_history_positions[workspace]['personal'] = (rowid, *hint[1:])
        return raw
    finally:
        clean.close()


def export_file_changes(store, workspace, previous):
    from hashlib import sha256
    old = getattr(store, '_portable_images', {}).get(workspace)
    cached_digest = getattr(store, '_portable_file_digest', None)
    if old is not None and (cached_digest is None or cached_digest[0] is not old[1]):
        cached_digest = (old[1], sha256(old[1]).hexdigest())
    files = export_files(store, workspace)
    raw = files[_FILENAME]
    current_digest = cached_digest[1] if cached_digest is not None and cached_digest[0] is raw else sha256(raw).hexdigest()
    store._portable_file_digest = (raw, current_digest)
    if (type(previous) is not dict or previous.keys() != {_FILENAME} or old is None
            or previous[_FILENAME] != cached_digest[1]):
        return {'files': files}
    chunks = [[offset, raw[offset:offset + 4096]] for offset in range(0, len(raw), 4096)
              if raw[offset:offset + 4096] != old[1][offset:offset + 4096]]
    return {'patches': [{'name': _FILENAME, 'base': previous[_FILENAME], 'digest': current_digest,
                         'size': len(raw), 'chunks': chunks}]}


def export_files(store, workspace):
    """Serialize a fresh database that has never contained connection credentials."""
    with store._snapshot():
        stamp = (store.db.total_changes, store.db.execute("PRAGMA data_version").fetchone()[0])
        cached = getattr(store, '_portable_images', {}).get(workspace)
        if cached is not None:
            raw = _incremental_image(store, workspace, cached)
            if raw is not None and len(raw) > MAX_DATABASE_BYTES:
                raw = None
            if raw is not None:
                journal = getattr(store, '_history_heads', {}).get(workspace, {}).get('history')
                head = journal['entries'][-1]['hash'] if journal and journal['entries'] else cached[2]
                store._portable_images[workspace] = (stamp, raw, head)
                return {_FILENAME: raw}
        snapshot = store.export_budget(workspace)
        images = [(transaction_id, store.get_receipt(workspace, transaction_id)) for transaction_id in store.receipt_ids(workspace)]
    clean = Store(":memory:", month=snapshot["budgetStartMonth"], _authority=False)
    try:
        # Portable images are data containers. Rebuild runtime-only indexes when
        # restoring into a live Store rather than storing and compressing them.
        for name in _ADDITIVE_INDEXES:
            clean.db.execute(f"DROP INDEX {name}")
        # Store.export_budget already validated every record and reference. Use
        # the same private population path as public restore in this fresh DB.
        clean._mutate("personal", lambda state: clean._restore_validated_snapshot(state, snapshot))
        for transaction_id, image in images:
            receipt_media_type(image)
            clean.db.execute("INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES('personal',?,?)", (transaction_id, image))
        # The image belongs to this budget; demo seeding is supplied by the host
        # installation and must not travel as a second ledger inside its payload.
        for table in ("meta", *_ROW_LIMITS):
            clean.db.execute(f"DELETE FROM {table} WHERE workspace <> 'personal'")
        # Strip native-only metadata after populating the trusted fresh ledger.
        # New portable images retain the schema used before bank sync setup.
        clean.db.execute("ALTER TABLE items DROP COLUMN sync_settings")
        clean.db.execute("ALTER TABLE items DROP COLUMN closed_account_cursors")
        if snapshot.get('history'):
            from .history_writes import save_history
            save_history(clean, 'personal', snapshot['history'], None, cache_valid=False)
        clean.db.execute("VACUUM")
        clean.db.execute(f"PRAGMA user_version={_FORMAT_VERSION}")
        raw = clean.db.serialize()
        if len(raw) > MAX_DATABASE_BYTES and snapshot.get('history'):
            clean.db.execute("UPDATE budget_history SET record=? WHERE workspace='personal'", (json.dumps(snapshot['history'], separators=(',', ':')),))
            clean.db.execute("VACUUM")
            raw = clean.db.serialize()
            clean._history_blob_positions = {}
        if len(raw) > MAX_DATABASE_BYTES:
            raise StoreError("This SQLite budget exceeds the portable database size limit.")
        journal = snapshot.get('history')
        head = journal['entries'][-1]['hash'] if journal and journal['entries'] else None
        if not hasattr(store, '_portable_images'):
            store._portable_images = {}
        store._portable_images[workspace] = (stamp, raw, head)
        hints = getattr(clean, '_history_blob_positions', {})
        if 'personal' in hints:
            rowid = clean.db.execute("SELECT rowid FROM budget_history WHERE workspace='personal'").fetchone()[0]
            hint = hints['personal']
            hints['personal'] = (rowid, *hint[1:])
        if not hasattr(store, '_portable_history_positions'):
            store._portable_history_positions = {}
        store._portable_history_positions[workspace] = hints
        return {_FILENAME: raw}
    finally:
        clean.close()


def _validate_rows(connection, *, legacy_accounts, legacy_tables):
    meta = connection.execute("SELECT workspace FROM meta").fetchall()
    if len(meta) != 1 or meta[0][0] != "personal":
        raise StoreError("SQLite budget images must contain exactly one personal ledger.")
    for table, limit in _ROW_LIMITS.items():
        if table in legacy_tables:
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
    for column in ('name_overridden', 'closed'):
        if column in {row["name"] for row in connection.execute("PRAGMA table_info(accounts)")}:
            if any(type(row[0]) is not int or row[0] not in (0, 1) for row in connection.execute(f"SELECT {column} FROM accounts")):
                raise StoreError("SQLite budget flags must be integer booleans.")


def read_files(files, *, with_receipts=False):
    """Treat untrusted SQLite rows as data; never execute their schema or migrations."""
    if not isinstance(files, Mapping) or set(files) != {_FILENAME}:
        raise StoreError("This SQLite budget requires exactly one budget.sqlite payload.")
    raw = files[_FILENAME]
    if (
        type(raw) is not bytes or not raw or len(raw) > MAX_DATABASE_BYTES
        or not raw.startswith(b"SQLite format 3\x00")
    ):
        raise StoreError("This SQLite budget payload is invalid or exceeds the size limit.")
    reader = Store(":memory:", month="2026-10", _authority=False)
    try:
        expected_schema = _without_additive_columns(_schema(reader.db))
        optional_indexes = {row[1]: row for row in expected_schema if row[1] in _ADDITIVE_INDEXES}
        expected_schema = [row for row in expected_schema if row[1] not in optional_indexes]
        # Earlier format-1 images predate account scope. Keep their exact trusted
        # schema allowlisted and infer defaults while reading their data only.
        legacy_schema = [
            (*row[:3], row[3].replace(
                ", on_budget INTEGER NOT NULL DEFAULT 1, subtype TEXT", ""
            )) if row[1] == "accounts" else row
            for row in expected_schema
        ]
        # Each planning table is additive. Preserve the exact prior schemas for
        # every combination without executing any stored SQL or migrations.
        compatible_schemas = [
            [row for row in schema if row[2] not in omitted]
            for schema in (expected_schema, legacy_schema)
            # Bank setup metadata is native-only; older credential-free images
            # retain their exact earlier items schema, including an empty table.
            for schema in (schema, [
                (*row[:3], row[3].replace(", sync_settings TEXT", "")) if row[1] == "items" else row
                for row in schema
            ])
            for schema in (schema, [
                (*row[:3], row[3].replace(
                    ", bank_website TEXT, name_overridden INTEGER NOT NULL DEFAULT 0", ""
                )) if row[1] == "accounts" else row
                for row in schema
            ])
            for schema in (schema, [
                (*row[:3], row[3].replace(", original_details TEXT", "")) if row[1] == "transactions" else row
                for row in schema
            ])
            for schema in (schema, [
                (*row[:3], row[3].replace(", category_agent TEXT", "")) if row[1] == "transactions" else row
                for row in schema
            ])
            for omitted in ((), ("future_funding",), ("rollover_modes",), ("future_funding", "rollover_modes"))
            for omitted in (omitted, (*omitted, "transaction_receipts"))
            for omitted in (omitted, (*omitted, "budget_history"))
        ]
        reader.db.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        reader.db.execute("PRAGMA trusted_schema=OFF")
        reader.db.execute("PRAGMA query_only=ON")
        reader.db.deserialize(raw)
        actual_schema = _schema(reader.db)
        # Optional indexes may be absent. Validate every present definition once
        # instead of expanding all combinations of runtime-only indexes.
        if any(row[1] in optional_indexes and row != optional_indexes[row[1]] for row in actual_schema):
            raise StoreError("This SQLite budget has an unsupported database schema.")
        normalized_schema = [row for row in actual_schema if row[1] not in optional_indexes]
        if (
            reader.db.execute("PRAGMA user_version").fetchone()[0] != _FORMAT_VERSION
            or _without_additive_columns(normalized_schema) not in compatible_schemas
        ):
            raise StoreError("This SQLite budget has an unsupported database schema.")
        if [row[0] for row in reader.db.execute("PRAGMA quick_check")] != ["ok"]:
            raise StoreError("This SQLite budget database is damaged.")
        _validate_rows(
            reader.db,
            legacy_accounts="on_budget" not in {row["name"] for row in reader.db.execute("PRAGMA table_info(accounts)")},
            legacy_tables=set(_ROW_LIMITS) - {row[1] for row in actual_schema if row[0] == "table"},
        )
        snapshot = reader.export_budget()
        images = []
        if any(row[1] == "transaction_receipts" for row in actual_schema):
            transactions = {row["id"] for row in snapshot["transactions"] if not row["deleted"]}
            for transaction_id, image in reader.db.execute("SELECT transaction_id,image FROM transaction_receipts ORDER BY transaction_id"):
                if transaction_id not in transactions:
                    raise StoreError("Receipt images must belong to an active transaction.")
                receipt_media_type(image)
                images.append((transaction_id, image))
        return (snapshot, images) if with_receipts else snapshot
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
        "rolloverModes": sorted(snapshot["rolloverModes"], key=lambda rollover: (rollover["month"], rollover["currency"])),
    }


def prepare_files(store, files, *, existing):
    snapshot, images = read_files(files, with_receipts=True)
    if existing:
        with store._snapshot():
            state = store._read_state("personal")
            try:
                receipt = store._workspace_import_receipt(state, "portable-budget-files", "restore")
            except StoreError:
                return
        if receipt is not None:
            return
    elif _semantic(store.export_budget()) == _semantic(snapshot) and images == [
        (transaction_id, store.get_receipt("personal", transaction_id))
        for transaction_id in store.receipt_ids("personal")
    ]:
        return
    store.restore_budget("personal", snapshot, _receipts=images)
