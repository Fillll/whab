"""Independent persistence adapters share financial rules through the public SDK."""

from copy import deepcopy

import pytest

from whab.plugins import (
    ImportedAccount,
    ImportedTransaction,
    StoreError,
    SyncChanges,
    WorkspaceImport,
)
from whab.plugins import storage_model as model
from whab.plugins.storage_transfer import decode_local_state, encode_local_state


def test_public_model_applies_currency_rules_to_a_detached_candidate_with_explicit_time():
    active = model.initial_state("personal", "2026-10")
    original = deepcopy(active)
    candidate = deepcopy(active)
    model.commands.add_account(candidate, "wallet", {"name": "Fictional wallet", "type": "depository", "balance": 10000})
    model.commands.add_account(candidate, "canadian", {
        "name": "Fictional Canadian wallet", "type": "depository", "balance": 20000, "currency": "CAD",
    })
    candidate["categories"]["travel"] = model.create_category("travel", "Travel", "Goals", currency="CAD")
    model.commands.assign(candidate, "groceries", "2026-10", 1000, as_of_month="2026-10")
    model.commands.add_transaction(candidate, "purchase", {
        "accountId": "wallet", "date": "2026-10-03", "merchant": "Fictional market",
        "amount": 125, "categoryId": "groceries",
    })
    projection = model.calculate_workspace(candidate, "2026-10")
    assert candidate["accounts"]["wallet"]["balance"] == 9875
    assert projection["summariesByCurrency"]["USD"]["readyToAssign"] == 9000
    assert projection["summariesByCurrency"]["CAD"]["readyToAssign"] == 20000
    assert next(row for row in projection["budget"] if row["categoryId"] == "groceries")["available"] == 875
    assert active == original

    before = deepcopy(candidate)
    for invalid in ({"amount": True, "categoryId": "groceries"}, {"amount": 125, "categoryId": "travel"}):
        with pytest.raises(StoreError):
            model.commands.add_transaction(candidate, "rejected", {
                "accountId": "wallet", "date": "2026-10-03", "merchant": "Fictional market", **invalid,
            })
        assert candidate == before


def test_public_provider_merge_keeps_pending_annotations_receipts_and_checkpoint_together():
    state = model.initial_state("personal", "2026-10")
    state["items"]["fictional-item"] = {
        "id": "fictional-item", "token": "fictional-encrypted-record", "institutionName": "Fictional bank",
        "provider": "fictional-bank", "cursor": None, "status": "connected", "error": None, "lastSynced": None,
    }
    account = ImportedAccount(
        id="fictional-account", itemId="fictional-item", name="Fictional account", institution="Fictional bank",
        type="depository", balance=10000, currency="USD", source="fictional-bank", mask="4821",
    )
    pending = ImportedTransaction(
        id="pending", accountId="fictional-account", date="2026-10-03", merchant="Fictional merchant",
        amount=125, suggestedCategoryId="groceries", pending=True, source="fictional-bank",
        inferredTransfer=False, pendingTransactionId=None,
    )
    model.merge_sync(state, "fictional-item", SyncChanges(added=[pending], modified=[], removed=[]),
                     "cursor-one", [account], expected_cursor=None, synced_at="2026-10-03T00:00:00Z")
    model.commands.edit_transaction(state, "pending", "groceries", reviewed=True, note="Fictional retained note")
    state["receipts"]["pending"] = b"fictional-native-receipt"
    posted = ImportedTransaction(**{**pending, "id": "posted", "pending": False, "pendingTransactionId": "pending", "suggestedCategoryId": None})
    result = model.merge_sync(state, "fictional-item", SyncChanges(added=[posted], modified=[], removed=["pending"]),
                              "cursor-two", [account], expected_cursor="cursor-one", synced_at="2026-10-04T00:00:00Z")
    assert result == {"added": 1, "modified": 0, "removed": 1}
    assert state["transactions"]["posted"]["note"] == "Fictional retained note"
    assert state["transactions"]["posted"]["reviewed"] is True
    assert state["transactions"]["posted"]["categoryId"] == "groceries"
    assert state["receipts"] == {"posted": b"fictional-native-receipt"}
    assert state["items"]["fictional-item"]["cursor"] == "cursor-two"
    snapshot = model.portable_snapshot(state)
    assert "fictional-encrypted-record" not in str(snapshot)
    assert "itemId" not in snapshot["accounts"][0]
    assert "mask" not in snapshot["accounts"][0]


def test_public_state_helpers_keep_workspace_and_native_protected_data_separate():
    states = {name: model.initial_state(name, "2026-10") for name in ("personal", "demo")}
    states["demo"]["accounts"]["demo-checking"]["mask"] = "4821"
    financial, protected = encode_local_state(states)
    restored = decode_local_state(financial, protected)
    assert restored["personal"] == states["personal"]
    assert restored["demo"]["accounts"]["demo-checking"]["mask"] == "4821"
    restored["demo"]["accounts"]["demo-checking"]["name"] = "Changed only in detached state"
    assert states["demo"]["accounts"]["demo-checking"]["name"] == "Fictional checking"
    model.require_pristine(restored["personal"])
    with pytest.raises(StoreError):
        model.require_pristine(restored["demo"])


def test_public_import_validation_and_neutral_restore_do_not_share_caller_records():
    account = {
        "id": "wallet", "name": "Fictional imported wallet", "institution": "", "type": "depository",
        "balance": 125, "source": "manual", "stale": False, "balanceUnknown": False,
    }
    imported = model.validate_workspace_import(WorkspaceImport((account,), (), (), (), "2026-10"))
    account["balance"] = 999
    assert imported.accounts[0]["balance"] == 125
    state = model.initial_state("personal", "2026-10")
    source = model.initial_state("personal", "2026-10")
    model.commands.add_account(source, "wallet", {"name": "Fictional wallet", "type": "depository", "balance": 125})
    portable = model.portable_snapshot(source)
    model.restore_snapshot(state, portable)
    portable["accounts"][0]["balance"] = 999
    assert state["accounts"]["wallet"]["balance"] == 125
