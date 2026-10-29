"""The public entry command saves to imported accounts on either native backend."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.plugins import WorkspaceImport


@pytest.fixture(params=["sqlite", "json"])
def imported_store(request, tmp_path):
    if request.param == "sqlite":
        from whab_plugin_sqlite import Store
        store = Store(tmp_path / "budget.sqlite", month="2026-10")
    else:
        from whab_plugin_json import Store
        store = Store(tmp_path / "budget.json", month="2026-10")
    yield store
    store.close()


@pytest.mark.parametrize("source", ["ynab", "plaid", "custom-feed"])
def test_http_manual_entry_preserves_import_origin_currency_and_saved_values(imported_store, source, tmp_path):
    store = imported_store
    store.import_workspace("personal", source, "fictional-fingerprint", WorkspaceImport(
        accounts=({"id": "imported", "name": "Fictional imported account", "institution": "Fictional institution", "type": "depository", "balance": 10000, "source": source, "stale": False, "balanceUnknown": False},),
        categories=({"id": "food", "name": "Food", "group": "Daily", "target": 0},),
        transactions=(), assignments=(), budget_start_month="2026-10",
    ))
    before = deepcopy(store.accounts("personal")[0])
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    with TestClient(create_app(store=store, settings=settings), base_url="https://127.0.0.1:5173") as client:
        payload = {"workspace": "personal", "accountId": "imported", "date": "2026-10-27", "merchant": "Fictional manual purchase", "amount": 125, "categoryId": "food", "note": "A hand-entered note"}
        assert client.post("/api/transactions", json=payload).status_code == 200
        account = store.accounts("personal")[0]
        assert account == {**before, "balance": 9875}
        row = store.transactions("personal")[0]
        assert row["source"] == "manual" and row["amount"] == 125
        assert row["accountId"] == "imported" and row["note"] == payload["note"]
        assert client.get("/api/workspace?workspace=personal&month=2026-10").json()["transactions"][0]["id"] == row["id"]
        snapshot = store.export_budget("personal")
        assert snapshot["transactions"][0]["source"] == "manual"
        assert client.post("/api/transactions", json={**payload, "source": "plaid"}).status_code == 400
        assert len(store.transactions("personal")) == 1


def test_native_bank_refresh_retains_manual_entries_and_rejects_provider_deletion(imported_store):
    from whab.domain.errors import StoreError

    store = imported_store
    store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    account = {"id": "bank", "itemId": "item", "name": "Fictional bank", "institution": "Fictional Bank", "type": "depository", "balance": 10000, "currency": "USD", "source": "plaid"}
    empty = {"added": [], "modified": [], "removed": []}
    store.apply_sync("item", empty, "one", [account])
    key = store.add_transaction("personal", {"accountId": "bank", "date": "2026-10-27", "merchant": "Fictional manual purchase", "amount": 125, "categoryId": None, "note": "Preserve this entry"})
    row = next(row for row in store.transactions("personal") if row["id"] == key)
    store.apply_sync("item", empty, "two", [{**account, "balance": 9500}])
    assert next(row for row in store.transactions("personal") if row["id"] == key) == row
    assert store.accounts("personal")[0]["balance"] == 9500
    before = store.export_budget("personal")
    with pytest.raises(StoreError, match="does not belong"):
        store.apply_sync("item", {**empty, "removed": [key]}, "three", [account])
    assert store.export_budget("personal") == before


def test_register_corrections_save_atomically_and_adjust_manual_balances(imported_store, tmp_path):
    store = imported_store
    cash = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    card = store.add_account("personal", {"name": "Card", "type": "credit", "balance": 0})
    foreign = store.add_account("personal", {"name": "Euro cash", "type": "depository", "balance": 0, "currency": "EUR"})
    key = store.add_transaction("personal", {"accountId": cash, "date": "2026-10-01", "merchant": "Original", "amount": 500, "categoryId": None})
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    with TestClient(create_app(store=store, settings=settings), base_url="https://127.0.0.1:5173") as client:
        change = {"workspace": "personal", "accountId": card, "amount": 750, "date": "2026-10-02", "merchant": "Corrected", "note": "Saved memo"}
        assert client.patch(f"/api/transactions/{key}", json=change).status_code == 200
        rows = store.transactions("personal")
        assert rows[0]["merchant"] == "Corrected" and rows[0]["amount"] == 750
        assert rows[0]["date"] == "2026-10-02" and rows[0]["note"] == "Saved memo"
        balances = {row["id"]: row["balance"] for row in store.accounts("personal")}
        assert balances[cash] == 10000 and balances[card] == 750
        before = store.export_budget()
        for invalid in ({"amount": 0}, {"amount": True}, {"amount": None}, {"date": "bad"}, {"accountId": foreign}, {"note": "x" * 1001}):
            assert client.patch(f"/api/transactions/{key}", json={**change, **invalid}).status_code == 400
            assert store.export_budget() == before


def test_imported_corrections_survive_refresh_and_portable_roundtrip(imported_store, tmp_path):
    store = imported_store
    store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    account = {"id": "bank", "itemId": "item", "name": "Bank", "institution": "Fictional Bank", "type": "depository", "balance": 10000, "currency": "USD", "source": "plaid"}
    record = {"id": "pending", "accountId": "bank", "date": "2026-10-01", "merchant": "Bank description", "amount": 125, "suggestedCategoryId": None, "pending": True, "source": "plaid", "inferredTransfer": False, "pendingTransactionId": None}
    store.apply_sync("item", {"added": [record], "modified": [], "removed": []}, "one", [account])
    moved = store.add_account("personal", {"name": "Tracking", "type": "depository", "balance": 0})
    store.edit_transaction_details("personal", "pending", {"accountId": moved, "date": "2026-10-03", "merchant": "My payee", "amount": 150, "note": "Retained note"})
    assert store.accounts("personal")[0]["balance"] == 10000
    posted = {**record, "id": "posted", "pending": False, "pendingTransactionId": "pending", "amount": 130}
    store.apply_sync("item", {"added": [posted], "modified": [], "removed": ["pending"]}, "two", [account])
    row = store.transactions("personal")[0]
    assert (row["accountId"], row["date"], row["merchant"], row["amount"]) == (moved, "2026-10-03", "My payee", 150)
    original = store.export_budget()["transactions"][-1]["original"]
    assert original["amount"] == 130 and original["accountId"] == "bank"
    # An override remains explicit even when a provider briefly matches it.
    store.apply_sync("item", {"added": [], "modified": [{**posted, "amount": 150}], "removed": []}, "three", [account])
    store.apply_sync("item", {"added": [], "modified": [{**posted, "amount": 160}], "removed": []}, "four", [account])
    store.edit_transaction_details("personal", "posted", {"merchant": "My payee again"})
    assert store.transactions("personal")[0]["amount"] == 150
    original = store.export_budget()["transactions"][-1]["original"]
    assert original["amount"] == 160
    files = store.export_budget_files()
    restored = type(store)(tmp_path / "restored-budget", month="2026-10")
    try:
        restored.restore_budget_files("personal", files)
        assert restored.transactions("personal") == store.transactions("personal")
        assert restored.export_budget()["transactions"][-1]["original"] == original
    finally:
        restored.close()


def test_new_row_request_identity_prevents_duplicate_creation(imported_store, tmp_path):
    store = imported_store
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    with TestClient(create_app(store=store, settings=settings), base_url="https://127.0.0.1:5173") as client:
        payload = {"workspace": "personal", "accountId": account, "date": "2026-10-27", "merchant": "Once", "amount": 125, "categoryId": None, "requestId": "new-row-request"}
        assert client.post("/api/transactions", json=payload).status_code == 200
        assert client.post("/api/transactions", json=payload).status_code == 200
        assert len(store.transactions("personal")) == 1
        assert client.post("/api/transactions", json={**payload, "amount": 200}).status_code == 400
        assert store.accounts("personal")[0]["balance"] == 9875


def test_detail_corrections_require_an_existing_transaction(imported_store):
    from whab.plugins import StoreError
    before = imported_store.export_budget()
    with pytest.raises(StoreError, match="not found"):
        imported_store.edit_transaction_details("personal", "missing", {"amount": 100})
    assert imported_store.export_budget() == before
