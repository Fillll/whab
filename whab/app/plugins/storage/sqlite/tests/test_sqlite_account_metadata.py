"""User-owned account names/websites survive provider refresh and portable images."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.portable import read_files

from whab.plugins import StoreError


def test_account_metadata_normalizes_persists_and_roundtrips_without_changing_money(store, tmp_path):
    key = store.add_account("personal", {"name": "Original", "type": "depository", "balance": 1234})
    store.update_account("personal", key, {"name": "  Household savings  ", "bankWebsite": "https://BANK.example.org/login"})
    account = store.accounts("personal")[0]
    assert account["name"] == "Household savings" and account["nameOverridden"] is True
    assert account["bankWebsite"] == "https://bank.example.org" and account["balance"] == 1234
    store.update_account("personal", key, {"currency": "CAD"})
    assert store.accounts("personal")[0]["currency"] == "CAD"
    target = Store(tmp_path / "metadata.sqlite", month="2026-10")
    try:
        target.restore_budget_files("personal", store.export_budget_files())
        assert target.accounts("personal") == store.accounts("personal")
        target.update_account("personal", key, {"bankWebsite": ""})
        assert "bankWebsite" not in target.accounts("personal")[0]
    finally:
        target.close()


def test_names_and_website_annotations_survive_sync_while_provider_balances_refresh(store, imported_account, sync_changes):
    store.save_item("item", "synthetic-token", "Synthetic institution")
    store.apply_sync("item", sync_changes(), "one", [imported_account(name="Provider name")])
    store.update_account("personal", "bank", {"name": "My name", "bankWebsite": "https://bank.example.org"})
    store.apply_sync("item", sync_changes(), "two", [imported_account(name="Changed provider name", balance=1500)])
    account = store.accounts("personal")[0]
    assert account["name"] == "My name" and account["balance"] == 1500
    assert account["bankWebsite"] == "https://bank.example.org"
    assert account["nameOverridden"] is True
    store.update_account("personal", "bank", {"bankWebsite": None})
    store.apply_sync("item", sync_changes(), "three", [imported_account(name="Provider again")])
    assert "bankWebsite" not in store.accounts("personal")[0]


def test_nonboolean_saved_name_override_is_rejected_before_import(store):
    key = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 0})
    raw = store.export_budget_files()["budget.sqlite"]
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute("UPDATE accounts SET name_overridden=2 WHERE id=?", (key,))
        connection.commit()
        raw = connection.serialize()
    finally:
        connection.close()
    with pytest.raises(StoreError, match="integer booleans"):
        read_files({"budget.sqlite": raw})


def test_pre_metadata_portable_account_schema_stays_readable(store):
    store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 0})
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(store.export_budget_files()["budget.sqlite"])
        connection.execute("ALTER TABLE accounts DROP COLUMN name_overridden")
        connection.execute("ALTER TABLE accounts DROP COLUMN bank_website")
        connection.commit()
        raw = connection.serialize()
    finally:
        connection.close()
    assert read_files({"budget.sqlite": raw}) == store.export_budget()
