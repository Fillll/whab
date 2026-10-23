import pytest
from whab_plugin_devkit import receipt_png

from whab.domain.bank_sync import merge_sync
from whab.domain.imports import annotated_transaction
from whab.domain.ledger_state import initial_state
from whab.plugins import StoreError


@pytest.mark.parametrize("changes", [None, {}, {"added": (), "modified": [], "removed": []}])
def test_invalid_sync_envelope_is_atomic(json_store, json_account, changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        json_store.apply_sync("item", changes, "candidate", [json_account()])
    assert json_store.path.read_bytes() == before


def test_missing_connection_and_invalid_account_collection_are_rejected(json_store, json_changes):
    with pytest.raises(StoreError):
        json_store.apply_sync("missing", json_changes(), None)
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    with pytest.raises(StoreError):
        json_store.apply_sync("item", json_changes(), "one", accounts=())


@pytest.mark.parametrize("account_change", [
    {"itemId": "other"}, {"source": "other"}, {"currency": "EUR"},
])
def test_account_ownership_and_currency_cannot_change_during_sync(json_store, json_account, json_changes, account_change):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.apply_sync("item", json_changes(), "one", [json_account()])
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        json_store.apply_sync("item", json_changes(), "two", [json_account(**account_change)])
    assert json_store.path.read_bytes() == before


def test_connection_account_collision_is_rejected(json_store, json_account, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.save_item("other", "other-ciphertext", "Other fictional")
    json_store.apply_sync("other", json_changes(), "one", [json_account(itemId="other")])
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        json_store.apply_sync("item", json_changes(), "two", [json_account()])
    assert json_store.path.read_bytes() == before


@pytest.mark.parametrize("transaction_change", [
    {"accountId": "missing"}, {"accountId": "other-account"}, {"source": "other"},
])
def test_foreign_transaction_is_atomic(json_store, json_account, json_transaction, json_changes, transaction_change):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.save_item("other", "other-ciphertext", "Other fictional")
    json_store.apply_sync("other", json_changes(), "one", [json_account(id="other-account", itemId="other")])
    json_store.apply_sync("item", json_changes(), "one", [json_account()])
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        json_store.apply_sync("item", json_changes(added=[json_transaction(**transaction_change)]), "two")
    assert json_store.path.read_bytes() == before


def test_foreign_pending_annotation_and_tombstone_cannot_be_claimed(json_store, json_account, json_transaction, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.save_item("other", "other-ciphertext", "Other fictional")
    json_store.apply_sync("other", json_changes(added=[json_transaction()]), "one", [json_account(itemId="other")])
    json_store.apply_sync("item", json_changes(), "one", [json_account(id="own-account")])
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        json_store.apply_sync("item", json_changes(added=[json_transaction(
            "posted", accountId="own-account", pendingTransactionId="pending",
        )]), "two")
    with pytest.raises(StoreError):
        json_store.apply_sync("item", json_changes(removed=["pending"]), "two")
    assert json_store.path.read_bytes() == before


def test_invalid_provider_category_is_optional_but_saved_category_is_preserved(json_store, json_account, json_transaction, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.apply_sync("item", json_changes(added=[json_transaction(suggestedCategoryId="missing")]), "one", [json_account()])
    assert json_store.transactions("personal")[0]["categoryId"] is None
    json_store.edit_transaction("personal", "pending", "groceries", True, "Keep fictional note")
    json_store.apply_sync("item", json_changes(modified=[json_transaction(suggestedCategoryId="missing")]), "two")
    assert json_store.transactions("personal")[0]["categoryId"] == "groceries"
    assert json_store.transactions("personal")[0]["note"] == "Keep fictional note"
    euro = json_store.add_category("personal", {"name": "Euro fictional", "group": "Fictional", "target": 0, "currency": "EUR"})
    json_store.apply_sync("item", json_changes(added=[json_transaction("euro", suggestedCategoryId=euro)]), "three")
    assert next(row for row in json_store.transactions("personal") if row["id"] == "euro")["categoryId"] is None


def test_posted_receipt_wins_when_both_identities_already_have_images(json_store, json_account, json_transaction, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.apply_sync("item", json_changes(added=[json_transaction(), json_transaction("posted", pending=False)]), "one", [json_account()])
    json_store.save_receipt("personal", "pending", receipt_png())
    json_store.save_receipt("personal", "posted", receipt_png())
    json_store.apply_sync("item", json_changes(modified=[json_transaction(
        "posted", pending=False, pendingTransactionId="pending",
    )], removed=["pending"]), "two")
    assert json_store.receipt_ids("personal") == ["posted"]
    json_store.apply_sync("item", json_changes(removed=["posted"]), "three")
    assert json_store.receipt_ids("personal") == []
    assert next(row for row in json_store.export_budget()["transactions"] if row["id"] == "posted")["deleted"]
    assert "posted" in json_store.export_budget_files()["budget.json"].decode()


def test_custom_categories_and_saved_planning_block_replacement(json_store, json_import):
    json_store.add_category("personal", {"name": "Fictional custom", "group": "Fictional", "target": 0})
    assert not json_store.preview_workspace_import("personal", "fictional", "hash")["canImport"]
    with pytest.raises(StoreError):
        json_store.import_workspace("personal", "fictional", "hash", json_import)


def test_refresh_without_user_metadata_and_null_cursor(json_store, json_account, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.apply_sync("item", json_changes(), "first", [json_account()])
    json_store.apply_sync("item", json_changes(), None, [json_account(balance=125000)])
    assert json_store.accounts("personal")[0]["balance"] == 125000
    assert json_store.get_item("item")["cursor"] is None


def test_shared_merge_refuses_invalid_saved_annotation_instead_of_dropping_it(json_account, json_transaction, json_changes):
    state = initial_state("personal", "2026-10")
    state["accounts"]["bank"] = json_account()
    state["items"]["item"] = {"id": "item", "token": "fictional-ciphertext", "provider": "plaid"}
    record = json_transaction()
    state["transactions"]["pending"] = annotated_transaction(record, {
        "categoryId": "missing-category", "categoryEdited": True, "reviewed": True,
    })
    with pytest.raises(StoreError):
        merge_sync(state, "item", json_changes(modified=[record]), "next", [json_account()], synced_at="2026-10-01T00:00:00+00:00")
