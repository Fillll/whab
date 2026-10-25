import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_devkit import BackendAdapter, Capability, public_snapshot, run_conformance
from whab_plugin_example_storage import create_storage

from whab.plugins import StorageContext, StorageOperation, StoreError
from whab.plugins.storage_transfer import canonical_storage_json


def operation(storage, identifier="fictional-native-request"):
    before = storage.commit_evidence()
    return StorageOperation(
        identifier,
        before.authority_epoch,
        before.generation,
        "personal",
        "add_account",
        canonical_storage_json({"name": "Fictional wallet", "type": "depository", "balance": 125}),
    )


def test_public_conformance_and_cleanup():
    capabilities = frozenset(Capability) - {Capability.NATIVE_REPLICA}
    run_conformance(BackendAdapter(create_storage, capabilities)).assert_success()


def test_new_public_candidate_helpers_use_only_declared_storage_and_readiness():
    from whab_plugin_devkit.local_storage import assert_storage_readiness, storage_conformance
    from whab_plugin_example_storage import plugin

    assert storage_conformance(plugin)["declaration"]["native"] == "supported"
    assert assert_storage_readiness(plugin) == {
        "status": "passed",
        "read_only": "unchanged",
        "reopen": "preserved",
        "retry": "exact-receipt",
    }


@pytest.mark.parametrize("after", [False, True])
def test_native_boundary_failure_preserves_old_or_recoverable_commit(tmp_path, after):
    context = StorageContext(tmp_path, "sandbox", {})
    store = create_storage(context)
    command = operation(store)
    before = public_snapshot(store)

    def boundary(stage):
        if stage == ("after-commit" if after else "before-commit"):
            raise RuntimeError("fictional commit-response failure")

    store._boundary = boundary
    with pytest.raises(RuntimeError):
        store.execute_operation(command)
    if after:
        with pytest.raises(StoreError):
            store.accounts("personal")
    else:
        assert public_snapshot(store) == before
    store.close()
    reopened = create_storage(context)
    try:
        receipt = reopened.operation_receipt(command.operation_id)
        assert (receipt is not None) is after
        result = reopened.execute_operation(command)
        assert result.replayed is after
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()


def test_read_only_proof_leaves_bytes_and_directories_unchanged(tmp_path):
    context = StorageContext(tmp_path, "sandbox", {})
    with pytest.raises(StoreError):
        create_storage(replace(context, read_only=True))
    assert list(tmp_path.iterdir()) == []
    writer = create_storage(context)
    writer.execute_operation(operation(writer))
    expected = writer.commit_evidence()
    files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    reader = create_storage(
        replace(context, read_only=True, expected_instance_id=expected.instance_id)
    )
    try:
        assert reader.commit_evidence() == writer.commit_evidence()
        assert public_snapshot(reader) == public_snapshot(writer)
        with pytest.raises(StoreError):
            reader.execute_operation(operation(reader))
    finally:
        reader.close()
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == files
    with pytest.raises(StoreError):
        create_storage(context)
    writer.close()
    with pytest.raises(StoreError):
        create_storage(replace(context, expected_instance_id=str(uuid4())))
    create_storage(context).close()


def test_role_fence_covers_legacy_and_native_financial_paths(tmp_path):
    store = create_storage(StorageContext(tmp_path, "sandbox", {}))
    try:
        before = store.commit_evidence()
        fenced = store.set_role("fictional-control", before, "fenced")
        with pytest.raises(StoreError):
            store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 1})
        with pytest.raises(StoreError):
            store.execute_operation(operation(store))
        assert store.set_role("fictional-control", before, "fenced") == fenced
        assert store.set_role("fictional-control", fenced, "primary") == before
        assert store.execute_operation(operation(store)).replayed is False
    finally:
        store.close()


def test_external_native_write_or_missing_bound_lease_is_never_overwritten(tmp_path):
    context = StorageContext(tmp_path, "sandbox", {})
    store = create_storage(context)
    native = store.path
    try:
        external = sqlite3.connect(native)
        (original,) = external.execute("SELECT document FROM ledger").fetchone()
        external.execute("UPDATE ledger SET document=?", (original + b" ",))
        external.commit()
        external.close()
        with pytest.raises(StoreError):
            store.execute_operation(operation(store))
    finally:
        store.close()
    # Restore fictional bytes to isolate the stable writer-identity check.
    external = sqlite3.connect(native)
    external.execute("UPDATE ledger SET document=?", (original,))
    external.commit()
    external.close()
    lease = native.with_suffix(".writer")
    lease.unlink()
    with pytest.raises(StoreError):
        create_storage(context)
    assert not lease.exists()


def test_native_schema_is_explicit_and_never_implicitly_reset(tmp_path):
    context = StorageContext(tmp_path, "sandbox", {})
    store = create_storage(context)
    path = store.path
    store.close()
    external = sqlite3.connect(path)
    external.execute("PRAGMA user_version=999")
    external.close()
    original = path.read_bytes()
    for read_only in (True, False):
        with pytest.raises(StoreError):
            create_storage(replace(context, read_only=read_only))
        assert path.read_bytes() == original


@pytest.mark.parametrize("stage", ["before-commit", "after-commit"])
def test_real_process_loss_has_native_receipt_proof_before_any_explicit_retry(tmp_path, stage):
    context = StorageContext(tmp_path, "sandbox", {})
    store = create_storage(context)
    before = store.commit_evidence()
    store.close()
    program = """
import pathlib, sys, threading
from whab_plugin_example_storage.backend import SnapshotStorage
from whab.plugins import StorageContext, StorageOperation
from whab.plugins.storage_transfer import canonical_storage_json
def boundary(stage):
    if stage == sys.argv[2]:
        print('ready', flush=True)
        threading.Event().wait()
store = SnapshotStorage(StorageContext(pathlib.Path(sys.argv[1]), 'sandbox', {}), boundary=boundary)
before = store.commit_evidence()
command = StorageOperation('fictional-native-request', before.authority_epoch, before.generation,
                           'personal', 'add_account', canonical_storage_json({
                               'name':'Fictional wallet', 'type':'depository', 'balance':125}))
store.execute_operation(command)
"""
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG"}
    environment = {
        key: value for key, value in os.environ.items() if key in allowed or key.startswith("LC_")
    }
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", program, str(tmp_path), stage],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(process.stdout.readline)
            try:
                assert ready.result(timeout=30) == b"ready\n"
            finally:
                process.kill()
                process.wait(timeout=5)
        readonly = create_storage(
            replace(context, read_only=True, expected_instance_id=before.instance_id)
        )
        try:
            receipt = readonly.operation_receipt("fictional-native-request")
            assert (receipt is not None) is (stage == "after-commit")
            assert readonly.commit_evidence().generation == before.generation + (
                stage == "after-commit"
            )
        finally:
            readonly.close()
        recovered = create_storage(replace(context, expected_instance_id=before.instance_id))
        try:
            result = recovered.execute_operation(
                StorageOperation(
                    "fictional-native-request",
                    before.authority_epoch,
                    before.generation,
                    "personal",
                    "add_account",
                    canonical_storage_json(
                        {"name": "Fictional wallet", "type": "depository", "balance": 125}
                    ),
                )
            )
            assert result.replayed is (stage == "after-commit")
            assert len(recovered.accounts("personal")) == 1
        finally:
            recovered.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_unsupported_platform_refuses_before_creating_any_files(tmp_path, monkeypatch):
    from whab_plugin_example_storage import backend

    monkeypatch.setattr(backend, "_DIRECTORY_SYNC", False)
    home = tmp_path / "absent" / "budget"
    with pytest.raises(StoreError, match="requires POSIX"):
        create_storage(StorageContext(home, "sandbox", {}))
    assert not home.parent.exists()


def test_fresh_directories_are_durable_before_native_state_can_be_acknowledged(
    tmp_path, monkeypatch
):
    from whab_plugin_example_storage import backend

    home = tmp_path / "new-parent" / "new-budget"
    synchronized = []
    original = backend._sync_directory

    def observe(path):
        original(path)
        synchronized.append(path)

    monkeypatch.setattr(backend, "_sync_directory", observe)
    reached = []

    def boundary(stage):
        if stage == "created-native":
            reached.append(stage)
            assert {home, home.parent, home / "data"} <= set(synchronized)

    store = backend.SnapshotStorage(StorageContext(home, "sandbox", {}), boundary=boundary)
    try:
        assert reached == ["created-native"]
        result = store.execute_operation(operation(store))
        assert result.receipt.evidence.generation == 1
    finally:
        store.close()


def test_initial_native_creation_crash_has_no_acknowledged_state_and_never_auto_resets(tmp_path):
    home = tmp_path / "fictional-budget"
    program = """
import pathlib,sys,threading
from whab_plugin_example_storage.backend import SnapshotStorage
from whab.plugins import StorageContext
def boundary(stage):
 if stage=='created-native':
  print('created',flush=True)
  threading.Event().wait()
SnapshotStorage(StorageContext(pathlib.Path(sys.argv[1]),'sandbox',{}),boundary=boundary)
"""
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG"}
    environment = {
        key: value for key, value in os.environ.items() if key in allowed or key.startswith("LC_")
    }
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", program, str(home)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(process.stdout.readline)
            try:
                assert ready.result(timeout=30) == b"created\n"
            finally:
                process.kill()
                process.wait(timeout=5)
        before = {
            path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()
        }
        assert any(name.suffix == ".sqlite3" and raw == b"" for name, raw in before.items())
        for read_only in (True, False):
            with pytest.raises(StoreError):
                create_storage(StorageContext(home, "sandbox", {}, read_only=read_only))
            assert {
                path.relative_to(home): path.read_bytes()
                for path in home.rglob("*")
                if path.is_file()
            } == before
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_existing_corrupted_scope_and_symlink_home_refuse_without_writable_open(tmp_path):
    from dataclasses import replace
    from types import MappingProxyType

    from whab.plugins import BudgetContext

    home = tmp_path / "budget"
    context = StorageContext(home, "sandbox", {})
    store = create_storage(context)
    store.execute_operation(operation(store))
    store.close()
    before = {
        path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()
    }
    wrong_scope = BudgetContext(
        "another-budget",
        MappingProxyType({}),
        MappingProxyType({}),
        lambda _: None,
        lambda _: None,
        lambda *_: None,
        lambda _: None,
    )
    with pytest.raises(StoreError):
        create_storage(replace(context, budget=wrong_scope))
    assert {
        path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()
    } == before
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    with pytest.raises(StoreError):
        create_storage(replace(context, home=alias))
    assert {
        path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()
    } == before


def test_preparation_restores_a_pristine_cache_retries_exactly_and_preserves_live_data(tmp_path):
    source = create_storage(StorageContext(tmp_path / "source", "sandbox", {}))
    destination = create_storage(StorageContext(tmp_path / "destination", "sandbox", {}))
    try:
        source.add_account(
            "personal", {"name": "Fictional portable wallet", "type": "depository", "balance": 125}
        )
        payload = source.export_budget_files("personal")
        destination.prepare_budget_files("personal", payload, existing=False)
        assert public_snapshot(destination) == public_snapshot(source)
        destination.prepare_budget_files("personal", payload, existing=False)
        assert public_snapshot(destination) == public_snapshot(source)
        destination.add_account(
            "personal", {"name": "Later acknowledged wallet", "type": "depository", "balance": 250}
        )
        current = public_snapshot(destination)
        destination.prepare_budget_files("personal", payload, existing=True)
        assert public_snapshot(destination) == current
        with pytest.raises(StoreError):
            destination.prepare_budget_files("personal", payload, existing=False)
        assert public_snapshot(destination) == current
        with pytest.raises(StoreError):
            destination.prepare_budget_files("demo", payload, existing=True)
    finally:
        destination.close()
        source.close()


def png_receipt():
    import struct
    import zlib

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )


def test_provider_pending_posted_receipt_checkpoint_and_private_records_survive_reopen(tmp_path):
    from whab.plugins import ImportedAccount, ImportedTransaction, SyncChanges

    context = StorageContext(tmp_path, "sandbox", {})
    store = create_storage(context)
    image = png_receipt()
    try:
        store.save_item(
            "fictional-item",
            "fictional-host-encrypted-record",
            "Fictional bank",
            provider_id="fictional-bank",
        )
        account = ImportedAccount(
            id="fictional-account",
            itemId="fictional-item",
            name="Fictional bank account",
            institution="Fictional bank",
            type="depository",
            balance=10000,
            currency="USD",
            source="fictional-bank",
            mask="4821",
        )
        pending = ImportedTransaction(
            id="pending",
            accountId="fictional-account",
            date="2026-10-03",
            merchant="Fictional market",
            amount=125,
            suggestedCategoryId=None,
            pending=True,
            source="fictional-bank",
            inferredTransfer=False,
            pendingTransactionId=None,
        )
        store.apply_sync(
            "fictional-item",
            SyncChanges(added=[pending], modified=[], removed=[]),
            "cursor-one",
            [account],
            expected_cursor=None,
        )
        store.edit_transaction(
            "personal", "pending", "groceries", reviewed=True, note="Fictional retained note"
        )
        store.save_receipt("personal", "pending", image)
        store.update_account("personal", "fictional-account", {"name": "Fictional edited account"})
        posted = ImportedTransaction(
            **{**pending, "id": "posted", "pending": False, "pendingTransactionId": "pending"}
        )
        store.apply_sync(
            "fictional-item",
            SyncChanges(added=[posted], modified=[], removed=["pending"]),
            "cursor-two",
            [account],
            expected_cursor="cursor-one",
        )
        rows = store.transactions("personal", "2026-10")
        assert len(rows) == 1 and rows[0]["id"] == "posted"
        assert rows[0]["reviewed"] is True and rows[0]["note"] == "Fictional retained note"
        assert rows[0]["categoryId"] == "groceries"
        assert store.accounts("personal")[0]["name"] == "Fictional edited account"
        assert (
            store.receipt_ids("personal") == ["posted"]
            and store.get_receipt("personal", "posted") == image
        )
        before = store.commit_evidence()
        with pytest.raises(StoreError):
            store.apply_sync(
                "fictional-item",
                SyncChanges(added=[], modified=[], removed=[]),
                "wrong",
                [],
                expected_cursor="old-cursor",
            )
        with pytest.raises(StoreError):
            store.save_item(
                "fictional-item",
                "another-encrypted-record",
                "Fictional second provider",
                provider_id="another-bank",
            )
        assert (
            store.commit_evidence() == before
            and store.get_item("fictional-item")["cursor"] == "cursor-two"
        )
        store.set_item_status("fictional-item", "error", "fictional attention code")
        assert store.items()[0]["error"] == "fictional attention code"
        assert "token" not in store.items()[0] and "cursor" not in store.items()[0]
        portable = store.export_budget_files("personal")
        assert all(
            b"fictional-host-encrypted-record" not in raw and b"fictional-item" not in raw
            for raw in portable.values()
        )
        detached = store.get_item("fictional-item")
        detached["cursor"] = "caller-mutated"
        assert store.get_item("fictional-item")["cursor"] == "cursor-two"
    finally:
        store.close()
    reopened = create_storage(context)
    try:
        assert reopened.get_receipt("personal", "posted") == image
        assert reopened.get_item("fictional-item")["cursor"] == "cursor-two"
        assert reopened.get_item("fictional-item")["token"] == "fictional-host-encrypted-record"
        restored = create_storage(StorageContext(tmp_path / "portable-destination", "sandbox", {}))
        try:
            restored.restore_budget_files("personal", portable)
            assert restored.get_receipt("personal", "posted") == image
            assert restored.items() == [] and restored.accounts("personal")[0]["stale"] is True
            restored.delete_receipt("personal", "posted")
            assert restored.receipt_ids("personal") == []
            with pytest.raises(StoreError):
                restored.save_receipt("personal", "absent", image)
        finally:
            restored.close()
        reopened.disconnect_item("fictional-item")
        assert reopened.items() == [] and reopened.get_item("fictional-item")["token"] is None
        assert reopened.accounts("personal")[0]["stale"] is True
        with pytest.raises(StoreError):
            reopened.disconnect_item("fictional-item")
        reopened.set_item_status("absent", "error", "fictional no-op")
    finally:
        reopened.close()


def test_manual_review_funding_periods_and_negative_commands_publish_one_complete_candidate(
    tmp_path, monkeypatch
):
    from whab_plugin_example_storage import backend

    monkeypatch.setattr(backend, "current_month", lambda: "2026-10")
    store = create_storage(StorageContext(tmp_path, "sandbox", {}))
    try:
        wallet = store.add_account(
            "personal", {"name": "Fictional wallet", "type": "depository", "balance": 10000}
        )
        cad = store.add_account(
            "personal",
            {
                "name": "Fictional Canadian wallet",
                "type": "depository",
                "balance": 20000,
                "currency": "CAD",
            },
        )
        parent = store.add_category("personal", {"name": "Fictional parent", "group": "Goals"})
        group = next(row["parentId"] for row in store.categories("personal") if row["id"] == parent)
        category = store.add_category(
            "personal", {"name": "Fictional child", "parentId": group, "target": 0}
        )
        store.assign("personal", category, "2026-10", 1000)
        transaction = store.add_transaction(
            "personal",
            {
                "accountId": wallet,
                "date": "2026-10-03",
                "merchant": "Fictional market",
                "amount": 125,
                "categoryId": category,
            },
        )
        store.review_transactions("personal", [transaction], False)
        assert store.transactions("personal")[0]["reviewed"] is False
        store.review_transactions("personal", [transaction], True)
        store.assign_next_month("personal", "2026-10", "USD", 1000)
        store.set_rollover_mode("personal", "2026-10", "USD", "balance")
        snapshot = public_snapshot(store, month="2026-10")
        assert (
            store.workspace("personal", "2026-10")["summariesByCurrency"]["CAD"]["readyToAssign"]
            == 20000
        )
        periods = store.workspace_periods("personal", "2026-10", 2)
        assert len(periods) == 2
        before = store.commit_evidence()
        for rejected in (
            lambda: store.add_category("personal", {"name": "Too deep", "parentId": category}),
            lambda: store.add_category("personal", {"name": "Unknown field", "extra": True}),
            lambda: store.add_transaction(
                "personal",
                {
                    "accountId": cad,
                    "date": "2026-10-03",
                    "merchant": "Wrong currency",
                    "amount": 1,
                    "categoryId": category,
                },
            ),
            lambda: store.assign_next_month("personal", "2026-10", "USD", 999999),
            lambda: store.set_rollover_mode("personal", "2026-10", "USD", "unknown"),
            lambda: store.review_transactions("personal", [transaction, transaction], True),
        ):
            with pytest.raises(StoreError):
                rejected()
            assert (
                store.commit_evidence() == before
                and public_snapshot(store, month="2026-10") == snapshot
            )
        with pytest.raises(StoreError):
            store.accounts("unknown-workspace")
    finally:
        store.close()
    with pytest.raises(StoreError):
        store.accounts("personal")


def test_import_retry_receipt_and_deleted_history_are_retained_without_overwriting_later_writes(
    tmp_path,
):
    from whab.plugins import WorkspaceImport

    store = create_storage(StorageContext(tmp_path, "sandbox", {}))
    data = WorkspaceImport(
        accounts=(
            {
                "id": "imported-wallet",
                "name": "Fictional imported wallet",
                "institution": "",
                "type": "depository",
                "balance": 125,
                "source": "fictional-import",
                "stale": False,
                "balanceUnknown": False,
            },
        ),
        categories=(),
        transactions=(),
        assignments=(),
        budget_start_month="2026-10",
    )
    try:
        assert (
            store.preview_workspace_import("personal", "fictional-import", "file-one")["canImport"]
            is True
        )
        first = store.import_workspace("personal", "fictional-import", "file-one", data)
        assert first["alreadyImported"] is False
        store.add_account(
            "personal", {"name": "Later acknowledged wallet", "type": "depository", "balance": 250}
        )
        before = public_snapshot(store)
        second = store.import_workspace("personal", "fictional-import", "file-one", data)
        assert second["alreadyImported"] is True and second["counts"] == first["counts"]
        assert public_snapshot(store) == before
        assert (
            store.preview_workspace_import("personal", "fictional-import", "file-two")["canImport"]
            is False
        )
        with pytest.raises(StoreError):
            store.import_workspace("personal", "fictional-import", "file-two", data)
        assert public_snapshot(store) == before
        assert (
            store.preview_workspace_import("demo", "fictional-import", "file-one")["canImport"]
            is False
        )
    finally:
        store.close()


@pytest.mark.parametrize("preparation", [False, True])
def test_saved_file_fresh_cache_preserves_import_identity_and_later_acknowledgments(
    tmp_path, preparation
):
    from whab.plugins import WorkspaceImport
    from whab.plugins.storage_transfer import read_storage_json

    source = create_storage(StorageContext(tmp_path / "source", "sandbox", {}))
    imported = WorkspaceImport(
        accounts=(
            {
                "id": "imported-wallet",
                "name": "Fictional imported wallet",
                "institution": "",
                "type": "depository",
                "balance": 125,
                "source": "fictional-import",
                "stale": False,
                "balanceUnknown": False,
            },
        ),
        categories=(),
        transactions=(),
        assignments=(),
        budget_start_month="2026-10",
    )
    try:
        first = source.import_workspace("personal", "fictional-import", "file-one", imported)
        source.add_account(
            "personal", {"name": "Later acknowledged wallet", "type": "depository", "balance": 250}
        )
        expected = public_snapshot(source)
        # Neutral restoration deliberately marks imported external accounts stale.
        next(row for row in expected["accounts"] if row["id"] == "imported-wallet")["stale"] = True
        payload = source.export_budget_files("personal")
    finally:
        source.close()
    restored_context = StorageContext(tmp_path / "restored", "sandbox", {})
    restored = create_storage(restored_context)
    try:
        if preparation:
            restored.prepare_budget_files("personal", payload, existing=True)
        else:
            restored.restore_budget_files("personal", payload)
        assert restored.preview_workspace_import("personal", "fictional-import", "file-one") == {
            "canImport": True,
            "reason": "",
            "alreadyImported": True,
        }
        assert public_snapshot(restored) == expected
        assert restored.import_workspace("personal", "fictional-import", "file-one", imported) == {
            "counts": first["counts"],
            "alreadyImported": True,
        }
        assert public_snapshot(restored) == expected
        value = read_storage_json(payload["snapshot-budget.json"])
        assert value["importReceipt"] == {
            "pluginId": "fictional-import",
            "fingerprint": "file-one",
            "counts": first["counts"],
        }
        next(row for row in value["budget"]["accounts"] if row["id"] == "imported-wallet")[
            "stale"
        ] = True
        assert restored.export_budget_files("personal") == {
            "snapshot-budget.json": canonical_storage_json(value)
        }
        restored.add_account(
            "personal", {"name": "Acknowledged after restore", "type": "depository", "balance": 350}
        )
        later = public_snapshot(restored)
        restored.prepare_budget_files("personal", payload, existing=True)
        assert public_snapshot(restored) == later
    finally:
        restored.close()
    reopened = create_storage(restored_context)
    try:
        assert (
            reopened.preview_workspace_import("personal", "fictional-import", "file-one")[
                "alreadyImported"
            ]
            is True
        )
        assert (
            reopened.import_workspace("personal", "fictional-import", "file-one", imported)[
                "alreadyImported"
            ]
            is True
        )
        assert public_snapshot(reopened) == later
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "invalid",
    [
        {
            "pluginId": "fictional-import",
            "fingerprint": "file-one",
            "counts": {"accounts": True, "categories": 0, "transactions": 0, "assignments": 0},
        },
        {
            "pluginId": "fictional-import",
            "fingerprint": "file-one",
            "counts": {"accounts": -1, "categories": 0, "transactions": 0, "assignments": 0},
        },
        {
            "pluginId": "fictional-import",
            "fingerprint": "file-one",
            "counts": {"accounts": 250001, "categories": 0, "transactions": 0, "assignments": 0},
        },
        {"pluginId": "fictional-import", "fingerprint": "file-one", "counts": {}},
        {
            "pluginId": "fictional-import",
            "fingerprint": "bad\x00identity",
            "counts": {"accounts": 0, "categories": 0, "transactions": 0, "assignments": 0},
        },
        {
            "pluginId": "fictional-import",
            "fingerprint": "file-one",
            "counts": {"accounts": 0, "categories": 0, "transactions": 0, "assignments": 0},
            "unknown": True,
        },
    ],
)
def test_invalid_saved_import_identity_refuses_without_native_change(tmp_path, invalid):
    import json

    from whab.plugins.storage_transfer import read_storage_json

    store = create_storage(StorageContext(tmp_path, "sandbox", {}))
    try:
        original = store.export_budget_files("personal")
        value = read_storage_json(original["snapshot-budget.json"])
        value["importReceipt"] = invalid
        files = {"snapshot-budget.json": json.dumps(value).encode()}
        before = store.commit_evidence()
        with pytest.raises(StoreError):
            store.restore_budget_files("personal", files)
        assert store.commit_evidence() == before
        store.add_account(
            "personal",
            {"name": "Existing acknowledged wallet", "type": "depository", "balance": 125},
        )
        before = store.commit_evidence()
        with pytest.raises(StoreError):
            store.prepare_budget_files("personal", files, existing=True)
        assert store.commit_evidence() == before
    finally:
        store.close()


def test_saved_payload_has_exact_versioned_readers_and_legacy_upgrade(tmp_path):
    from whab.plugins.storage_transfer import read_storage_json

    store = create_storage(StorageContext(tmp_path, "sandbox", {}))
    try:
        current = read_storage_json(store.export_budget_files("personal")["snapshot-budget.json"])
        assert current["formatVersion"] == 2
        legacy = {key: value for key, value in current.items() if key != "importReceipt"}
        legacy["formatVersion"] = 1
        store.restore_budget_files(
            "personal", {"snapshot-budget.json": canonical_storage_json(legacy)}
        )
        assert (
            read_storage_json(store.export_budget_files("personal")["snapshot-budget.json"])
            == current
        )
        before = store.commit_evidence()
        for malformed in (
            [],
            {**current, "formatVersion": True},
            {**current, "formatVersion": 3},
            {**legacy, "importReceipt": None},
            {key: value for key, value in current.items() if key != "importReceipt"},
        ):
            with pytest.raises(StoreError):
                store.prepare_budget_files(
                    "personal",
                    {"snapshot-budget.json": canonical_storage_json(malformed)},
                    existing=True,
                )
            assert store.commit_evidence() == before
    finally:
        store.close()


def test_deleted_transaction_attachment_roundtrip_preserves_history_but_hides_active_reads(
    tmp_path,
):
    from whab.plugins import ImportedAccount, ImportedTransaction, SyncChanges

    context = StorageContext(tmp_path / "source", "sandbox", {})
    source = create_storage(context)
    try:
        source.save_item(
            "fictional-item",
            "fictional-host-encrypted-record",
            "Fictional bank",
            provider_id="fictional-bank",
        )
        account = ImportedAccount(
            id="fictional-account",
            itemId="fictional-item",
            name="Fictional wallet",
            institution="Fictional bank",
            type="depository",
            balance=125,
            currency="USD",
            source="fictional-bank",
            mask="",
        )
        pending = ImportedTransaction(
            id="pending",
            accountId="fictional-account",
            date="2026-10-03",
            merchant="Fictional market",
            amount=25,
            suggestedCategoryId=None,
            pending=True,
            source="fictional-bank",
            inferredTransfer=False,
            pendingTransactionId=None,
        )
        source.apply_sync(
            "fictional-item",
            SyncChanges(added=[pending], modified=[], removed=[]),
            "first",
            [account],
        )
        source.save_receipt("personal", "pending", png_receipt())
        source.apply_sync(
            "fictional-item",
            SyncChanges(added=[], modified=[], removed=["pending"]),
            "second",
            [],
            expected_cursor="first",
        )
        assert source.transactions("personal") == [] and source.receipt_ids("personal") == []
        before = source.commit_evidence()
        for hidden in (
            lambda: source.get_receipt("personal", "pending"),
            lambda: source.delete_receipt("personal", "pending"),
        ):
            with pytest.raises(StoreError):
                hidden()
            assert source.commit_evidence() == before
        payload = source.export_budget_files("personal")
        destination = create_storage(StorageContext(tmp_path / "destination", "sandbox", {}))
        try:
            destination.restore_budget_files("personal", payload)
            assert destination.export_budget_files("personal") == payload
            assert destination.receipt_ids("personal") == []
            with pytest.raises(StoreError):
                destination.get_receipt("personal", "pending")
            destination.prepare_budget_files("personal", payload, existing=True)
            assert destination.export_budget_files("personal") == payload
        finally:
            destination.close()
        source.prepare_budget_files("personal", payload, existing=True)
        assert source.export_budget_files("personal") == payload
    finally:
        source.close()
    reopened = create_storage(context)
    try:
        assert reopened.export_budget_files("personal") == payload
        assert reopened.receipt_ids("personal") == []
    finally:
        reopened.close()


def change_native_document(path, change):
    import json

    connection = sqlite3.connect(path)
    try:
        (raw,) = connection.execute("SELECT document FROM ledger").fetchone()
        value = json.loads(raw)
        change(value)
        connection.execute(
            "UPDATE ledger SET document=?", (json.dumps(value, separators=(",", ":")).encode(),)
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "foreign-instance",
        "foreign-epoch",
        "foreign-budget",
        "future-generation",
        "zero-generation",
        "same-generation-wrong-operation",
        "same-generation-wrong-digest",
        "nonprimary-receipt",
        "duplicate-receipt",
    ],
)
def test_native_receipts_cannot_replay_foreign_or_inconsistent_financial_history(
    tmp_path, corruption
):
    context = StorageContext(tmp_path, "sandbox", {})
    store = create_storage(context)
    command = operation(store)
    store.execute_operation(command)
    path = store.path
    store.close()

    def change(value):
        receipt = value["operations"][0]
        evidence = receipt["evidence"]
        if corruption == "foreign-instance":
            evidence["instance_id"] = str(uuid4())
        elif corruption == "foreign-epoch":
            evidence["authority_epoch"] = str(uuid4())
        elif corruption == "foreign-budget":
            evidence["budget_id"] = "another-budget"
        elif corruption == "future-generation":
            evidence["generation"] += 1
        elif corruption == "zero-generation":
            evidence["generation"] = 0
        elif corruption == "same-generation-wrong-operation":
            receipt["operation_id"] = evidence["operation_id"] = "impossible-other-operation"
        elif corruption == "same-generation-wrong-digest":
            evidence["state_digest"] = "0" * 64
        elif corruption == "nonprimary-receipt":
            evidence["role"] = "fenced"
        elif corruption == "duplicate-receipt":
            value["operations"].append(receipt.copy())

    change_native_document(path, change)
    before = {
        file.relative_to(tmp_path): file.read_bytes()
        for file in tmp_path.rglob("*")
        if file.is_file()
    }
    for read_only in (True, False):
        with pytest.raises(StoreError):
            create_storage(replace(context, read_only=read_only))
        assert {
            file.relative_to(tmp_path): file.read_bytes()
            for file in tmp_path.rglob("*")
            if file.is_file()
        } == before


@pytest.mark.parametrize("stage", ["before-commit", "after-commit"])
def test_replaced_writer_lease_never_acknowledges_and_retains_exact_native_outcome(tmp_path, stage):
    context = StorageContext(tmp_path, "sandbox", {})
    store = create_storage(context)
    command = operation(store)
    original = store.commit_evidence()
    lease = store.path.with_suffix(".writer")
    retained = lease.with_suffix(".retained-original")

    def replace_lease(boundary):
        if boundary == stage:
            lease.rename(retained)
            lease.write_bytes(retained.read_bytes())

    store._boundary = replace_lease
    with pytest.raises(StoreError):
        store.execute_operation(command)
    with pytest.raises(StoreError):
        store.commit_evidence()
    store.close()
    with pytest.raises(StoreError):
        create_storage(context)
    # Explicit stopped-host restoration of the retained ORIGINAL inode, rather
    # than rebinding the native ledger to a new lease or assuming rollback.
    lease.unlink()
    retained.rename(lease)
    reopened = create_storage(context)
    try:
        receipt = reopened.operation_receipt(command.operation_id)
        assert (receipt is not None) is (stage == "after-commit")
        assert reopened.commit_evidence().generation == original.generation + (
            stage == "after-commit"
        )
        result = reopened.execute_operation(command)
        assert result.replayed is (stage == "after-commit")
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()
