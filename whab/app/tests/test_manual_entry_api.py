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
