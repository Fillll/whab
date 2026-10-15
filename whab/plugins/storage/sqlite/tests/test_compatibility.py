"""Synthetic saved databases must survive additive schema and runtime changes."""

import sqlite3

from whab_sqlite import Store
from whab_sqlite.plugin import plugin

from whab.config import Settings
from whab.plugins import StorageContext


def test_node_schema_and_encrypted_fixture_remain_usable(tmp_path, legacy_token):
    path = tmp_path / "legacy.sqlite"
    # Synthetic legacy Node schema intentionally lacks all additive Python flags.
    original = sqlite3.connect(path)
    original.executescript("""
        CREATE TABLE meta(workspace TEXT PRIMARY KEY,opening_cash INTEGER NOT NULL DEFAULT 0,baseline_ready INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE accounts(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,institution TEXT NOT NULL,type TEXT NOT NULL,balance INTEGER NOT NULL,source TEXT NOT NULL,item_id TEXT,mask TEXT,PRIMARY KEY(workspace,id));
        CREATE TABLE categories(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,group_name TEXT NOT NULL,target INTEGER NOT NULL,PRIMARY KEY(workspace,id));
        CREATE TABLE transactions(id TEXT NOT NULL,workspace TEXT NOT NULL,account_id TEXT NOT NULL,date TEXT NOT NULL,merchant TEXT NOT NULL,amount INTEGER NOT NULL,category_id TEXT,pending INTEGER NOT NULL DEFAULT 0,reviewed INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',is_transfer INTEGER NOT NULL DEFAULT 0,category_edited INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(workspace,id));
        CREATE TABLE budget(workspace TEXT NOT NULL,month TEXT NOT NULL,category_id TEXT NOT NULL,assigned INTEGER NOT NULL,PRIMARY KEY(workspace,month,category_id));
        CREATE TABLE items(id TEXT PRIMARY KEY,token TEXT,institution_name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'connected',last_synced TEXT,error TEXT,cursor TEXT,baseline_ready INTEGER NOT NULL DEFAULT 0);
        INSERT INTO meta VALUES('personal',0,0);
        INSERT INTO categories VALUES('fun','personal','Fun','Life',10000);
        INSERT INTO accounts VALUES('bank','personal','Checking','Fixture Bank','depository',10000,'plaid','fixture-item','1234');
        INSERT INTO transactions VALUES('legacy','personal','bank','2026-10-01','Fixture transaction',1000,'fun',0,1,'plaid','Fixture note',0,1,0);
        INSERT INTO budget VALUES('personal','2026-10','fun',2000);
    """)
    original.execute(
        "INSERT INTO items(id,token,institution_name,cursor) VALUES(?,?,?,?)",
        ("fixture-item", legacy_token, "Fixture Bank", "legacy-cursor"),
    )
    original.commit()
    original.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.get_item("fixture-item")["cursor"] == "legacy-cursor"
        assert reopened.get_item("fixture-item")["token"] == legacy_token
        assert reopened.transactions("personal")[0]["categoryId"] == "fun"
        assert reopened.transactions("personal")[0]["reviewed"] is True
        assert reopened.transactions("personal")[0]["note"] == "Fixture note"
        snapshot = reopened.workspace("personal", "2026-10")
        assert snapshot["budgetStartMonth"] == "2026-10"
        assert snapshot["accounts"][0]["balance"] == 10000
        assert snapshot["accounts"][0]["institution"] == "Fixture Bank"
        assert snapshot["accounts"][0]["mask"] == "1234"
        assert snapshot["accounts"][0]["stale"] is False
        assert snapshot["categories"] == [
            {"id": "fun", "name": "Fun", "group": "Life", "target": 10000}
        ]
        assert snapshot["budget"][0]["assigned"] == 2000
        assert snapshot["budget"][0]["spent"] == 1000
        assert snapshot["budget"][0]["available"] == 1000
        for table, expected in [
            ("accounts", {"active", "balance_unknown"}),
            ("transactions", {"inferred_transfer"}),
            ("meta", {"budget_start_month"}),
        ]:
            columns = {row["name"] for row in reopened.db.execute(f"PRAGMA table_info({table})")}
            assert expected <= columns
        assert len(reopened.transactions("demo")) == 55
    finally:
        reopened.close()
    twice = Store(path, month="2026-12")
    try:
        assert len(twice.transactions("personal")) == 1
        assert len(twice.transactions("demo")) == 55
        assert twice.get_item("fixture-item")["cursor"] == "legacy-cursor"
    finally:
        twice.close()


def test_rebrand_opens_existing_budget(monkeypatch, tmp_path):
    legacy_path = tmp_path / "data" / "ihab-sandbox.sqlite"
    existing = Store(legacy_path, month="2026-10")
    existing.add_account(
        "personal", {"name": "Saved checking", "type": "depository", "balance": 12345}
    )
    existing.close()
    monkeypatch.setenv("WHAB_HOME", str(tmp_path))
    monkeypatch.setenv("WHAB_ENV", "sandbox")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PORT", "5173")
    settings = Settings.load()
    reopened = plugin.storage(StorageContext(settings.home, settings.plaid_env, {}))
    try:
        accounts = reopened.accounts("personal")
        assert len(accounts) == 1
        assert accounts[0]["name"] == "Saved checking"
        assert accounts[0]["balance"] == 12345
        assert not (tmp_path / "data" / "whab-sandbox.sqlite").exists()
    finally:
        reopened.close()
