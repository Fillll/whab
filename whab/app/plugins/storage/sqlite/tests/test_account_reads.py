"""Metadata projections preserve account/history boundaries without loading rows."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.account_reads import account_overview, account_settings

from whab.plugins import StoreError


def test_overview_counts_selected_month_currency_without_materializing_history(store, monkeypatch):
    usd = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 1000})
    eur = store.add_account("personal", {"name": "Euro", "type": "depository", "balance": 2000, "currency": "EUR"})
    for index, (account, date, state) in enumerate((
        (usd, "2026-09-30", "old"), (usd, "2026-10-01", "first"),
        (usd, "2026-10-31", "last"), (usd, "2026-11-01", "future"),
        (usd, "2026-10-02", "pending"), (usd, "2026-10-03", "reviewed"),
        (usd, "2026-10-04", "deleted"), (eur, "2026-10-05", "euro"),
    )):
        key = store.add_transaction("personal", {
            "accountId": account, "date": date, "merchant": f"Synthetic {index}",
            "amount": 100, "categoryId": None,
        })
        if state in {"pending", "reviewed", "deleted"}:
            store.db.execute(f"UPDATE transactions SET {state}=1 WHERE workspace=? AND id=?", ("personal", key))
    accounts, categories = store.accounts("personal"), store.categories("personal")
    monkeypatch.setattr(store, "state", lambda *_: pytest.fail("Metadata must not load full ledger state"))
    result = store.account_overview("personal", "2026-10", "USD")
    assert result == {"accounts": accounts, "categories": categories, "toReview": 2}
    assert account_overview(store, "personal", "2026-10", "EUR")["toReview"] == 1
    assert account_overview(store, "personal", "2026-10", "JPY")["toReview"] == 0
    assert account_overview(store, "demo", "2026-10", "USD")["accounts"] != accounts


def test_settings_history_includes_deleted_and_earlier_transactions_and_is_workspace_scoped(store, monkeypatch):
    empty = store.add_account("personal", {"name": "Empty", "type": "depository", "balance": 0})
    historic = store.add_account("personal", {"name": "History", "type": "depository", "balance": 0})
    key = store.add_transaction("personal", {
        "accountId": historic, "date": "2020-01-01", "merchant": "Earlier imported history",
        "amount": 100, "categoryId": None,
    })
    store.db.execute("UPDATE transactions SET deleted=1 WHERE workspace=? AND id=?", ("personal", key))
    monkeypatch.setattr(store, "state", lambda *_: pytest.fail("Settings must not load full ledger state"))
    assert account_settings(store, "personal", empty)["currencyEditable"] is True
    result = store.account_settings("personal", historic)
    assert result["account"]["id"] == historic and result["currencyEditable"] is False
    for workspace, account in (("personal", "missing"), ("demo", historic)):
        with pytest.raises(StoreError, match="Account not found"):
            account_settings(store, workspace, account)


@pytest.mark.parametrize("kind", ["overview", "settings"])
def test_metadata_and_history_proof_share_a_snapshot_during_another_process_commit(tmp_path, monkeypatch, kind):
    store = Store(tmp_path / "snapshot.sqlite", month="2026-10")
    writer = sqlite3.connect(store.database_path, isolation_level=None)
    try:
        key = store.add_account("personal", {"name": "Before", "type": "depository", "balance": 1000})
        original = store._read_accounts
        changed = False

        def accounts(workspace):
            nonlocal changed
            rows = original(workspace)
            if not changed:
                changed = True
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE accounts SET balance=2000 WHERE workspace=? AND id=?", (workspace, key))
                writer.execute(
                    "INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source) VALUES(?,?,?,?,?,?,?)",
                    ("concurrent", workspace, key, "2026-10-05", "Concurrent purchase", 100, "manual"),
                )
                writer.execute("COMMIT")
            return rows

        monkeypatch.setattr(store, "_read_accounts", accounts)
        if kind == "overview":
            first = account_overview(store, "personal", "2026-10", "USD")
            assert first["accounts"][0]["balance"] == 1000 and first["toReview"] == 0
            assert account_overview(store, "personal", "2026-10", "USD")["toReview"] == 1
        else:
            first = account_settings(store, "personal", key)
            assert first["account"]["balance"] == 1000 and first["currencyEditable"] is True
            assert account_settings(store, "personal", key)["currencyEditable"] is False
    finally:
        writer.close()
        store.close()


@pytest.mark.parametrize("read", [account_overview, account_settings])
def test_optional_reads_retain_workspace_guards(store, read):
    arguments = ("2026-10", "USD") if read is account_overview else ("missing",)
    for workspace in ("invalid", None, []):
        with pytest.raises(StoreError, match="Invalid workspace"):
            read(store, workspace, *arguments)
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="Workspace not found"):
        read(store, "personal", *arguments)
