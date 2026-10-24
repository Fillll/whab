import ast
import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from whab_plugin_devkit import BackendAdapter, Capability, receipt_png, run_conformance
from whab_plugin_json import Store
from whab_plugin_json.plugin import create_storage, plugin

from whab.plugins import StorageContext, StoreError


def test_json_public_contracts():
    run_conformance(BackendAdapter(create_storage, frozenset(Capability))).assert_success()


def test_json_implementation_has_no_database_or_other_storage_dependency():
    source = Path(__file__).parents[1] / "src"
    for path in source.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert all("sqlite" not in name for name in names)


def test_json_factory_owns_separate_environment_files_and_metadata(tmp_path):
    sandbox = create_storage(StorageContext(tmp_path, "sandbox", {}))
    production = create_storage(StorageContext(tmp_path, "production", {}))
    try:
        sandbox.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100})
        assert production.accounts("personal") == []
        assert plugin.contract.authority == "local"
        assert plugin.contract.lifecycle == "live"
        assert {path.name for path in (tmp_path / "data").glob("*.json")} == {
            "whab-sandbox.json", "whab-production.json",
        }
    finally:
        sandbox.close()
        production.close()


def test_bulk_review_and_account_changes_are_atomic(json_store):
    account = json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100000})
    first = json_store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Fictional", "amount": 100,
    })
    second = json_store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-02", "merchant": "Fictional", "amount": 100,
    })
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        json_store.review_transactions("personal", [first, "missing"], True)
    assert json_store.path.read_bytes() == before
    json_store.review_transactions("personal", [first, second], True)
    assert all(row["reviewed"] for row in json_store.transactions("personal"))
    json_store.update_account("personal", account, {"name": "Renamed fictional", "bankWebsite": "https://example.test"})
    assert json_store.accounts("personal")[0]["nameOverridden"]
    with pytest.raises(StoreError):
        json_store.update_account("personal", account, {"currency": "EUR"})
    assert len(json_store.workspace_periods("personal", "2026-10", 2)) == 2


def test_provider_receipts_status_annotations_and_reopen(json_store, json_account, json_transaction, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    json_store.apply_sync("item", json_changes(added=[json_transaction()]), "first", [json_account()])
    json_store.save_receipt("personal", "pending", receipt_png())
    json_store.edit_transaction("personal", "pending", "groceries", True, "")
    json_store.update_account("personal", "bank", {"name": "My fictional account", "bankWebsite": "https://example.test"})
    json_store.apply_sync("item", json_changes(added=[json_transaction(
        "posted", pending=False, pendingTransactionId="pending",
    )], removed=["pending", "already-absent"]), "second", [json_account(name="Provider name")], expected_cursor="first")
    assert json_store.get_receipt("personal", "posted") == receipt_png()
    assert json_store.receipt_ids_for("personal", ["pending", "posted"]) == ["posted"]
    assert not json_store.receipt_transaction_exists("personal", "pending")
    assert not json_store.receipt_transaction_exists("personal", None)
    assert json_store.accounts("personal")[0]["name"] == "My fictional account"
    assert json_store.transactions("personal")[0]["reviewed"]
    assert json_store.transactions("personal")[0]["note"] == ""
    json_store.set_item_status("item", "reconnect", "Reconnect fictional connection.")
    assert json_store.items()[0]["status"] == "reconnect"
    assert "token" not in json_store.items()[0]
    path = json_store.path
    json_store.close()
    with pytest.raises(StoreError, match="closed"):
        json_store.accounts("personal")
    reopened = Store(path)
    try:
        assert reopened.get_item("item")["token"] == "fictional-ciphertext"
        assert reopened.get_item("item")["cursor"] == "second"
        reopened.disconnect_item("item")
        assert reopened.items() == []
        assert reopened.get_item("item")["token"] is None
        assert reopened.accounts("personal")[0]["stale"]
        with pytest.raises(StoreError):
            reopened.disconnect_item("item")
        reopened.set_item_status("absent", "error", "Fictional error")
    finally:
        reopened.close()


def test_portable_payload_sanitizes_and_preserves_receipts_and_import_identity(json_store, json_import, tmp_path):
    json_store.import_workspace("personal", "fictional-import", "fingerprint", json_import)
    json_store.save_receipt("personal", "import-transaction", receipt_png())
    json_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    files = json_store.export_budget_files()
    assert b"fictional-ciphertext" not in files["budget.json"]
    assert b'"items":[]' in files["budget.json"]
    assert b'"importReceipt"' in files["budget.json"]
    target = Store(tmp_path / "destination.json", month="2026-10")
    try:
        target.prepare_budget_files("personal", files)
        target.prepare_budget_files("personal", files)
        assert target.get_receipt("personal", "import-transaction") == receipt_png()
        assert target.items() == []
        result = target.import_workspace("personal", "fictional-import", "fingerprint", json_import)
        assert result["alreadyImported"]
        target.edit_transaction("personal", "import-transaction", note="Saved locally")
        target.prepare_budget_files("personal", files, existing=True)
        assert target.transactions("personal")[0]["note"] == "Saved locally"
        with pytest.raises(StoreError):
            target.prepare_budget_files("personal", {"budget.json": json_store.path.read_bytes()}, existing=True)
    finally:
        target.close()


def test_first_empty_cache_preparation_and_legacy_transfer(json_store, tmp_path):
    original = json_store.export_budget_files()
    json_store.prepare_budget_files("personal", original)
    json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100})
    files = json_store.export_budget_files()
    target = Store(tmp_path / "cache.json", month="2026-10")
    try:
        target.prepare_budget_files("personal", files, existing=True)
        assert target.accounts("personal")[0]["balance"] == 100
        assert json.loads(files["budget.json"])["formatVersion"] == 1
        with pytest.raises(StoreError):
            target.restore_budget_files("personal", files)
        with pytest.raises(StoreError):
            target.restore_budget("personal", json_store.export_budget())
    finally:
        target.close()


def test_preparation_accepts_reordered_equivalent_payload_without_replacing_live_data(json_store, json_import):
    json_store.import_workspace("personal", "fictional", "fingerprint", json_import)
    account = json_store.add_account("personal", {"name": "Fictional manual", "type": "depository", "balance": 1000})
    json_store.add_transaction("personal", {"accountId": account, "date": "2026-10-01", "merchant": "Fictional", "amount": 5})
    document = json.loads(json_store.export_budget_files()["budget.json"])
    for name in ("accounts", "categories", "transactions", "assignments", "futureFunding", "rolloverModes"):
        document["ledger"][name].reverse()
    before = json_store.commit_status()
    raw = json_store.path.read_bytes()
    json_store.prepare_budget_files("personal", {"budget.json": json.dumps(document, sort_keys=True).encode()})
    assert json_store.commit_status() == before
    assert json_store.path.read_bytes() == raw


@pytest.mark.parametrize("operation", [
    lambda store: store.accounts("invalid"),
    lambda store: store.transactions("personal", "invalid-month"),
    lambda store: store.add_category("personal", {"name": "Invalid", "extra": True}),
    lambda store: store.add_category("personal", None),
    lambda store: store.save_item("item", "token", "Fictional", provider_id="manual"),
    lambda store: store.save_item("item", "", "Fictional"),
    lambda store: store.apply_sync("missing", {}, None, historical_complete=1),
    lambda store: store.restore_budget("demo", {}),
    lambda store: store.restore_budget_files("demo", {}),
    lambda store: store.restore_budget_files("personal", None),
    lambda store: store.prepare_budget_files("demo", {}),
    lambda store: store.prepare_budget_files("personal", {}, existing=1),
    lambda store: store.prepare_budget_files("personal", None),
    lambda store: store.save_receipt("personal", "missing", receipt_png()),
    lambda store: store.delete_receipt("personal", "missing"),
])
def test_invalid_public_requests_leave_file_untouched(json_store, operation):
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        operation(json_store)
    assert json_store.path.read_bytes() == before


def test_import_retry_eligibility_and_generation(json_store, json_import):
    assert json_store.preview_workspace_import("demo", "fictional", "hash")["canImport"] is False
    assert json_store.preview_workspace_import("personal", "fictional", "hash")["canImport"]
    before = json_store.commit_status()
    result = json_store.import_workspace("personal", "fictional", "hash", json_import)
    assert result["counts"]["transactions"] == 1
    committed = json_store.commit_status()
    assert committed["generation"] == before["generation"] + 1
    assert committed["authorityEpoch"] == before["authorityEpoch"]
    assert json_store.import_workspace("personal", "fictional", "hash", json_import)["alreadyImported"]
    assert json_store.commit_status() == committed
    assert json_store.preview_workspace_import("personal", "fictional", "hash")["alreadyImported"]
    assert not json_store.preview_workspace_import("personal", "fictional", "other-hash")["canImport"]


def test_connection_ownership_and_disconnection_do_not_retarget_manual_accounts(json_store):
    account = json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100})
    json_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    with pytest.raises(StoreError, match="another bank provider"):
        json_store.save_item("item", "other-ciphertext", "Fictional Bank", provider_id="another")
    json_store.disconnect_item("item")
    json_store.set_item_status("item", "error", "Fictional error")
    assert json_store.get_item("item")["status"] == "disconnected"
    assert next(row for row in json_store.accounts("personal") if row["id"] == account)["stale"] is False
    with pytest.raises(StoreError):
        json_store.disconnect_item("missing")


def test_encrypted_connection_continuity_keeps_runtime_key_outside_data_file(json_store, json_account, json_changes):
    key = bytes(range(32))
    nonce = bytes(range(12))
    payload = AESGCM(key).encrypt(nonce, b"fictional-access-token", b"item")
    encrypted = ".".join(base64.b64encode(value).decode() for value in (nonce, payload[-16:], payload[:-16]))
    json_store.save_item("item", encrypted, "Fictional Bank")
    json_store.apply_sync("item", json_changes(), "saved-checkpoint", [json_account()])
    assert b"fictional-access-token" not in json_store.path.read_bytes()
    assert key.hex().encode() not in json_store.path.read_bytes()
    assert encrypted.encode() not in json_store.export_budget_files()["budget.json"]
    path = json_store.path
    json_store.close()
    reopened = Store(path)
    try:
        saved = reopened.get_item("item")
        saved_nonce, saved_tag, saved_ciphertext = [base64.b64decode(value) for value in saved["token"].split(".")]
        assert AESGCM(key).decrypt(saved_nonce, saved_ciphertext + saved_tag, b"item") == b"fictional-access-token"
        assert saved["cursor"] == "saved-checkpoint"
        assert reopened.accounts("personal")[0]["itemId"] == "item"
    finally:
        reopened.close()
