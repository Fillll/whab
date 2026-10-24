"""Both real local adapters use public SDK factories and fictional data only."""

import asyncio
from hashlib import sha256
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from tests.budgets.test_storage_authority import run_async
from tests.receipt_images import png
from whab.budgets.runtime import BudgetRuntime
from whab.budgets.storage_authority import (
    AuthorityJournal,
    NamespaceEvidence,
    StorageAuthorityCoordinator,
    resolve_authority,
)
from whab.plugins import (
    BudgetContext,
    StorageContext,
    StorageOperation,
    WorkspaceImport,
    load_descriptors,
)
from whab.plugins.storage import open_storage
from whab.plugins.storage_transfer import canonical_storage_json, decode_local_state


def populate(backend, ciphertext):
    imported = WorkspaceImport(
        accounts=({"id": "same", "name": "Fictional manual account", "institution": "", "type": "depository",
                   "balance": 10000, "source": "manual", "stale": False, "balanceUnknown": False},),
        categories=({"id": "food", "name": "Food", "group": "Everyday", "target": 1000},),
        transactions=({"id": "same", "accountId": "same", "date": "2026-01-02", "merchant": "Fictional market",
                       "amount": 100, "categoryId": "food", "pending": False, "reviewed": True, "source": "manual",
                       "note": "Imported annotation", "isTransfer": False, "inferredTransfer": False,
                       "categoryEdited": True, "deleted": False},),
        assignments=({"month": "2026-10", "categoryId": "food", "assigned": 1000},),
        budget_start_month="2026-01",
    )
    backend.import_workspace("personal", "fictional-import", "same-file", imported)
    demo_account = backend.accounts("demo")[0]["id"]
    backend.add_transaction("demo", {"id": "same", "accountId": demo_account, "date": "2026-10-01",
                                      "merchant": "Fictional demo", "amount": 123, "categoryId": "groceries"})
    backend.save_item("fictional-item", ciphertext, "Fictional institution", provider_id="fictional-bank")
    account = {"id": "bank-account", "name": "Fictional bank account", "institution": "Fictional institution",
               "type": "depository", "balance": 5000, "currency": "USD", "source": "fictional-bank",
               "itemId": "fictional-item", "mask": "1111", "stale": False, "balanceUnknown": False}
    pending = {"id": "pending", "accountId": "bank-account", "date": "2026-10-01", "merchant": "Fictional provider",
               "amount": 200, "suggestedCategoryId": None, "pending": True, "source": "fictional-bank",
               "inferredTransfer": False, "pendingTransactionId": None}
    backend.apply_sync("fictional-item", {"added": [pending], "modified": [], "removed": []}, "cursor-1", [account])
    backend.edit_transaction("personal", "pending", category_id="food", reviewed=True, note="Pending annotation survives")
    backend.save_receipt("personal", "pending", png())
    posted = {**pending, "id": "posted", "pending": False, "pendingTransactionId": "pending"}
    backend.apply_sync("fictional-item", {"added": [posted], "modified": [], "removed": []}, "cursor-2")
    backend.apply_sync("fictional-item", {"added": [], "modified": [], "removed": ["posted"]}, "cursor-3")
    evidence = backend.commit_evidence()
    request = StorageOperation("original-durable-command", evidence.authority_epoch, evidence.generation,
                               "personal", "assign", canonical_storage_json({
                                   "categoryId": "food", "month": "2026-10", "assigned": 1250,
                               }))
    return request, backend.execute_operation(request).receipt


@pytest.mark.parametrize(("first", "second"), [("sqlite", "json"), ("json", "sqlite")])
@run_async
async def test_real_roundtrip_reopen_and_reverse_preserve_complete_supported_state(tmp_path, first, second):
    budget_id = "1bbfcfce-42d1-41c3-a844-b58ba691c313"
    budget = BudgetContext(budget_id, {}, {}, lambda state: None)
    descriptors = {plugin.id: plugin for plugin in load_descriptors((first, second))}

    def factory(plugin_id, name, role):
        context = StorageContext(tmp_path / name, "sandbox", {}, budget, initial_role=role)
        return lambda: open_storage(descriptors[plugin_id], context)

    open_source = factory(first, "original-native", "primary")
    open_destination = factory(second, "destination-native", "staged")
    open_returned = factory(first, "returned-native", "staged")
    source = open_source()
    # A genuine synthetic ciphertext remains byte-identical; no provider service
    # or external connection factory is created anywhere in this test.
    cipher = Fernet(Fernet.generate_key())
    ciphertext = cipher.encrypt(b"fictional-provider-token").decode()
    request, original_receipt = await asyncio.to_thread(populate, source, ciphertext)
    before = await asyncio.to_thread(source.export_local)
    original_container = tmp_path / "original.whab"
    original_container.write_bytes(b"fictional-original-saved-container")
    unknown_file = tmp_path / "unavailable-plugin-model.bin"
    unknown_file.write_bytes(b"opaque-fictional-model\x00bytes")
    namespace = NamespaceEvidence(sha256(unknown_file.read_bytes()).hexdigest(),
                                  sha256(original_container.read_bytes()).hexdigest(), str(uuid4()))
    journal = AuthorityJournal(tmp_path / "control" / "authority.json", budget_id, "sandbox")
    runtime = BudgetRuntime(budget_id, {"storage": source})
    coordinator = StorageAuthorityCoordinator(runtime, journal, lambda: namespace)
    def build(active, storage):
        return {**active.services, "storage": storage}
    assert await coordinator.migrate(first, second, open_destination, open_destination, build) == 2
    async with runtime.request() as lease:
        target = lease.service("storage")
        transferred = await asyncio.to_thread(target.export_local)
        assert transferred.financial == before.financial
        assert transferred.protected == before.protected
        assert transferred.receipt_bytes == before.receipt_bytes
        replayed = await asyncio.to_thread(target.execute_operation, request)
        assert replayed.replayed is True and replayed.receipt == original_receipt
        evidence = await asyncio.to_thread(target.commit_evidence)
        later_request = StorageOperation("post-handover-command", evidence.authority_epoch, evidence.generation,
                                         "personal", "assign", canonical_storage_json({
                                             "categoryId": "food", "month": "2026-10", "assigned": 1500,
                                         }))
        later_receipt = (await asyncio.to_thread(target.execute_operation, later_request)).receipt
        after_later_write = await asyncio.to_thread(target.export_local)
    await runtime.close()

    preserved_source = await asyncio.to_thread(open_source)
    reopened_destination = await asyncio.to_thread(open_destination)
    try:
        assert resolve_authority(journal, preserved_source, reopened_destination, namespace) == "destination"
        assert preserved_source.export_local().financial == before.financial
        assert preserved_source.export_local().protected == before.protected
        assert reopened_destination.commit_evidence() == later_receipt.evidence
        restored = decode_local_state(reopened_destination.export_local().financial, reopened_destination.export_local().protected)
        assert restored["personal"]["transactions"]["posted"]["deleted"] is True
        assert restored["personal"]["transactions"]["posted"]["note"] == "Pending annotation survives"
        assert restored["personal"]["receipts"]["posted"] == png()
        assert restored["personal"]["items"]["fictional-item"]["token"] == ciphertext
        assert cipher.decrypt(restored["personal"]["items"]["fictional-item"]["token"].encode()) == b"fictional-provider-token"
        assert restored["personal"]["items"]["fictional-item"]["cursor"] == "cursor-3"
        assert restored["personal"]["transactions"]["same"]["note"] == "Imported annotation"
        assert restored["demo"]["transactions"]["same"]["merchant"] == "Fictional demo"
        assert restored["personal"]["importReceipt"]["fingerprint"] == "same-file"
        second_runtime = BudgetRuntime(budget_id, {"storage": reopened_destination})
        second_coordinator = StorageAuthorityCoordinator(second_runtime, journal, lambda: namespace)
        assert await second_coordinator.migrate(second, first, open_returned, open_returned, build) == 2
        async with second_runtime.request() as lease:
            returned = lease.service("storage")
            final = await asyncio.to_thread(returned.export_local)
            assert final.financial == after_later_write.financial
            assert final.protected == after_later_write.protected
            assert final.receipt_bytes == after_later_write.receipt_bytes
            assert (await asyncio.to_thread(returned.execute_operation, request)).receipt == original_receipt
            assert (await asyncio.to_thread(returned.execute_operation, later_request)).receipt == later_receipt
        await second_runtime.close()
    finally:
        preserved_source.close()
        reopened_destination.close()
    assert original_container.read_bytes() == b"fictional-original-saved-container"
    assert unknown_file.read_bytes() == b"opaque-fictional-model\x00bytes"
    assert ciphertext.encode() not in journal.path.read_bytes()
