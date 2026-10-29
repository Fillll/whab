"""SQLite plugin schema and additive, idempotent legacy migrations."""

import sqlite3

from whab.domain.dates import shift_month
from whab.domain.validation import validate_month


def initialize_schema(db: sqlite3.Connection, selected_month: str) -> None:
    db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS meta(workspace TEXT PRIMARY KEY,opening_cash INTEGER NOT NULL DEFAULT 0,baseline_ready INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS accounts(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,institution TEXT NOT NULL,type TEXT NOT NULL,balance INTEGER NOT NULL,source TEXT NOT NULL,item_id TEXT,mask TEXT,PRIMARY KEY(workspace,id));
        CREATE TABLE IF NOT EXISTS categories(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,group_name TEXT NOT NULL,target INTEGER NOT NULL,PRIMARY KEY(workspace,id));
        CREATE TABLE IF NOT EXISTS transactions(id TEXT NOT NULL,workspace TEXT NOT NULL,account_id TEXT NOT NULL,date TEXT NOT NULL,merchant TEXT NOT NULL,amount INTEGER NOT NULL,category_id TEXT,pending INTEGER NOT NULL DEFAULT 0,reviewed INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',is_transfer INTEGER NOT NULL DEFAULT 0,category_edited INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(workspace,id));
        CREATE TABLE IF NOT EXISTS transaction_receipts(workspace TEXT NOT NULL,transaction_id TEXT NOT NULL,image BLOB NOT NULL,PRIMARY KEY(workspace,transaction_id),FOREIGN KEY(workspace,transaction_id) REFERENCES transactions(workspace,id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED);
        CREATE TABLE IF NOT EXISTS budget(workspace TEXT NOT NULL,month TEXT NOT NULL,category_id TEXT NOT NULL,assigned INTEGER NOT NULL,PRIMARY KEY(workspace,month,category_id));
        CREATE TABLE IF NOT EXISTS future_funding(workspace TEXT NOT NULL,month TEXT NOT NULL,currency TEXT NOT NULL,assigned INTEGER NOT NULL,PRIMARY KEY(workspace,month,currency));
        CREATE TABLE IF NOT EXISTS rollover_modes(workspace TEXT NOT NULL,month TEXT NOT NULL,currency TEXT NOT NULL,mode TEXT NOT NULL,PRIMARY KEY(workspace,month,currency));
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,token TEXT,institution_name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'connected',last_synced TEXT,error TEXT,cursor TEXT,baseline_ready INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS workspace_imports(workspace TEXT PRIMARY KEY,plugin_id TEXT NOT NULL,fingerprint TEXT NOT NULL,account_count INTEGER NOT NULL,category_count INTEGER NOT NULL,transaction_count INTEGER NOT NULL,assignment_count INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS tx_month ON transactions(workspace,date);
        CREATE INDEX IF NOT EXISTS tx_register ON transactions(workspace,date DESC,id DESC) WHERE deleted=0;
        CREATE INDEX IF NOT EXISTS tx_register_account ON transactions(workspace,account_id,date DESC,id DESC) WHERE deleted=0;
        CREATE INDEX IF NOT EXISTS tx_account_history ON transactions(workspace,account_id);
    """)
    migrations = {
        "accounts": {
            "currency": "TEXT NOT NULL DEFAULT 'USD'",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "balance_unknown": "INTEGER NOT NULL DEFAULT 0",
            "on_budget": "INTEGER NOT NULL DEFAULT 1",
            "subtype": "TEXT",
            "bank_website": "TEXT",
            "name_overridden": "INTEGER NOT NULL DEFAULT 0",
        },
        "transactions": {
            "inferred_transfer": "INTEGER NOT NULL DEFAULT 0",
            "merchant_logo": "TEXT",
            "original_details": "TEXT",
        },
        "categories": {"currency": "TEXT NOT NULL DEFAULT 'USD'"},
        "meta": {"budget_start_month": "TEXT"},
        "items": {"provider": "TEXT NOT NULL DEFAULT 'plaid'"},
    }
    for table, columns in migrations.items():
        present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in present:
                db.execute("SAVEPOINT migrate_column")
                try:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                    if name == "on_budget":
                        db.execute("UPDATE accounts SET on_budget=0 WHERE type NOT IN ('depository','credit')")
                    if name == "inferred_transfer":
                        db.execute(
                            "UPDATE transactions SET inferred_transfer=is_transfer WHERE source='plaid' AND category_edited=0"
                        )
                    db.execute("RELEASE migrate_column")
                except BaseException:
                    db.execute("ROLLBACK TO migrate_column")
                    db.execute("RELEASE migrate_column")
                    raise
    validate_month(selected_month)
    db.execute(
        "UPDATE meta SET budget_start_month=CASE WHEN workspace='demo' THEN COALESCE((SELECT MIN(substr(date,1,7)) FROM transactions WHERE workspace='demo'),?) ELSE COALESCE((SELECT MIN(month) FROM budget WHERE workspace='personal'),?) END WHERE budget_start_month IS NULL",
        (shift_month(selected_month, -5), selected_month),
    )
