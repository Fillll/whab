"""Native authority/receipt commits use fictional records and isolated files."""

import base64
import json
import os
import stat
from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_devkit import receipt_png
from whab_plugin_json import Store, authority, codec, durability

from whab.plugins import StoreError
from whab.plugins.storage_transfer import (
    CommitEvidence,
    LocalTransferSnapshot,
    StorageOperation,
    canonical_storage_json,
    decode_local_state,
    read_storage_json,
)


def operation(backend, name, payload, *, identifier="fictional-operation", workspace="personal"):
    evidence = backend.commit_evidence()
    return StorageOperation(identifier, evidence.authority_epoch, evidence.generation,
                            workspace, name, canonical_storage_json(payload))


def schema_one(value):
    value.pop("authority")
    value["formatVersion"] = 1
    for workspace in value["workspaces"].values():
        workspace["associations"] = [row for row in workspace["associations"] if row["itemId"] is not None]
    return value


def test_durable_result_identity_replay_conflict_and_unknown_stale_retry(json_store):
    original = json_store.commit_evidence()
    assert type(original) is CommitEvidence
    request = operation(json_store, "add_account", {"name": "Fictional", "type": "depository", "balance": 10000})
    result = json_store.execute_operation(request)
    account = read_storage_json(result.receipt.result)
    assert result.replayed is False
    assert result.receipt.evidence.generation == original.generation + 1
    assert result.receipt.evidence.operation_id == request.operation_id
    assert json_store.accounts("personal")[0]["id"] == account
    replay = json_store.execute_operation(request)
    assert replay.replayed and replay.receipt == result.receipt
    assert json_store.commit_evidence() == result.receipt.evidence
    assert json_store.operation_receipt(request.operation_id) == result.receipt
    assert json_store.operation_receipt("unknown") is None
    with pytest.raises(StoreError, match="different"):
        json_store.execute_operation(replace(request, payload=canonical_storage_json({"name": "Changed", "type": "depository", "balance": 10000})))
    with pytest.raises(StoreError, match="unknown"):
        json_store.execute_operation(replace(request, operation_id="unknown"))
    path = json_store.path
    json_store.close()
    reopened = Store(path)
    try:
        assert reopened.commit_evidence() == result.receipt.evidence
        assert reopened.execute_operation(request).receipt == result.receipt
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()


def test_rejected_operation_and_partial_batch_leave_no_receipt_or_generation(json_store):
    request = operation(json_store, "add_account", {"name": "Fictional", "type": "depository", "balance": True})
    before = json_store.path.read_bytes()
    with pytest.raises(StoreError):
        json_store.execute_operation(request)
    assert json_store.path.read_bytes() == before
    assert json_store.operation_receipt(request.operation_id) is None
    assert json_store._pending is None
    valid = operation(json_store, "add_account", {"name": "Fictional", "type": "depository", "balance": 10000})
    assert not json_store.execute_operation(valid).replayed


@pytest.mark.parametrize("boundary", ["temporary_flushed", "before_replace", "replaced", "directory_flushed", "published"])
def test_operation_receipt_and_data_survive_the_same_commit_outcome(json_store, boundary):
    request = operation(json_store, "add_account", {"name": "Fictional", "type": "depository", "balance": 10000})

    def fail(name):
        if name == boundary:
            raise OSError("fictional fault")

    json_store._hook = fail
    with pytest.raises(StoreError):
        json_store.execute_operation(request)
    path = json_store.path
    json_store.close()
    reopened = Store(path)
    try:
        committed = boundary in {"replaced", "directory_flushed", "published"}
        assert bool(reopened.accounts("personal")) is committed
        assert (reopened.operation_receipt(request.operation_id) is not None) is committed
        result = reopened.execute_operation(request)
        assert result.replayed is committed
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()


def test_fence_roles_compare_complete_evidence_and_persist_across_reopen(json_store):
    original = json_store.commit_evidence()
    changed = replace(original, state_digest="1" * 64)
    with pytest.raises(StoreError, match="changed"):
        json_store.set_role("transition", changed, "fenced")
    fenced = json_store.set_role("transition", original, "fenced")
    assert fenced.role == "fenced" and fenced.generation == original.generation
    assert fenced.state_digest == original.state_digest
    assert json_store.set_role("transition", original, "fenced") == fenced
    assert json_store.set_role("transition", fenced, "fenced") == fenced
    with pytest.raises(StoreError):
        json_store.set_role("transition", None, "primary")
    with pytest.raises(StoreError):
        json_store.set_role("different", fenced, "primary")
    path = json_store.path
    json_store.close()
    reopened = Store(path, initial_role="primary")
    try:
        assert reopened.commit_evidence() == fenced
        with pytest.raises(StoreError, match="fenced"):
            reopened.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 0})
        resumed = reopened.set_role("transition", fenced, "primary")
        assert resumed == original
        fenced = reopened.set_role("other-transition", resumed, "fenced")
        retired = reopened.set_role("other-transition", fenced, "retired")
        with pytest.raises(StoreError):
            reopened.set_role("other-transition", retired, "primary")
    finally:
        reopened.close()


def test_all_legacy_mutations_reject_fenced_authority(json_store):
    account = json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 10000})
    transaction = json_store.add_transaction("personal", {"accountId": account, "date": "2026-10-01", "merchant": "Fictional", "amount": 1})
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    original = json_store.commit_evidence()
    json_store.set_role("transition", original, "fenced")
    before = json_store.path.read_bytes()
    for mutate in (
        lambda: json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 0}),
        lambda: json_store.update_account("personal", account, {"name": "Changed"}),
        lambda: json_store.add_category("personal", {"name": "Fictional", "target": 0}),
        lambda: json_store.add_transaction("personal", {"accountId": account, "date": "2026-10-01", "merchant": "Fictional", "amount": 1}),
        lambda: json_store.edit_transaction("personal", transaction, reviewed=True),
        lambda: json_store.review_transactions("personal", [transaction], True),
        lambda: json_store.assign("personal", "groceries", "2026-10", 1),
        lambda: json_store.assign_next_month("personal", "2026-11", "USD", 0),
        lambda: json_store.set_rollover_mode("personal", "2026-10", "USD", "categories"),
        lambda: json_store.save_item("other", "fictional-ciphertext", "Fictional"),
        lambda: json_store.set_item_status("item", "connected", None),
        lambda: json_store.disconnect_item("item"),
        lambda: json_store.apply_sync("item", {"added": [], "modified": [], "removed": []}, "next"),
        lambda: json_store.save_receipt("personal", transaction, receipt_png()),
        lambda: json_store.delete_receipt("personal", transaction),
        lambda: json_store.restore_budget("personal", json_store.export_budget()),
        lambda: json_store.restore_budget_files("personal", json_store.export_budget_files()),
    ):
        with pytest.raises(StoreError, match="fenced"):
            mutate()
        assert json_store.path.read_bytes() == before


def test_complete_transfer_preserves_protected_records_receipts_and_durable_operation(json_store, tmp_path, json_account, json_transaction, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    json_store.apply_sync("item", json_changes(added=[json_transaction()]), "cursor", [json_account()])
    request = operation(json_store, "save_receipt", {"transactionId": "pending", "image": base64.b64encode(receipt_png()).decode()})
    result = json_store.execute_operation(request)
    snapshot = json_store.export_local()
    assert "fictional-ciphertext" not in repr(snapshot)
    destination = Store(tmp_path / "destination.json", month="2026-10", initial_role="staged")
    try:
        assert destination.preview_local(snapshot).supported
        imported = destination.import_local("transfer", snapshot)
        assert imported.role == "staged"
        assert imported.generation == snapshot.source.generation
        assert imported.state_digest == snapshot.source.state_digest
        assert imported.instance_id != snapshot.source.instance_id
        assert destination.import_local("transfer", snapshot) == imported
        assert destination.operation_receipt(request.operation_id) == result.receipt
        assert destination.get_item("item") == json_store.get_item("item")
        assert destination.accounts("personal") == json_store.accounts("personal")
        assert destination.get_receipt("personal", "pending") == receipt_png()
        assert decode_local_state(snapshot.financial, snapshot.protected)["personal"]["items"]["item"]["cursor"] == "cursor"
        with pytest.raises(StoreError):
            destination.import_local("different", snapshot)
        with pytest.raises(StoreError):
            destination.set_role("different", imported, "primary", authority_epoch=str(uuid4()))
        primary = destination.set_role("transfer", imported, "primary", authority_epoch=str(uuid4()))
        assert primary.authority_epoch != snapshot.source.authority_epoch
        assert destination.execute_operation(request).receipt == result.receipt
        with pytest.raises(StoreError):
            destination.execute_operation(replace(request, operation_id="new-old-epoch"))
        assert not destination.preview_local(snapshot).empty
    finally:
        destination.close()


def test_transfer_refuses_active_or_populated_destination_and_identity_mismatch(json_store, tmp_path):
    snapshot = json_store.export_local()
    active = Store(tmp_path / "active.json", month="2026-10")
    wrong = Store(tmp_path / "wrong.json", month="2026-10", budget_id="another", initial_role="staged")
    try:
        assert not active.preview_local(snapshot).supported
        for destination in (active, wrong):
            before = destination.path.read_bytes()
            with pytest.raises(StoreError):
                destination.import_local("transfer", snapshot)
            assert destination.path.read_bytes() == before
        assert not wrong.preview_local(snapshot).supported
    finally:
        active.close()
        wrong.close()


def test_invalid_command_and_transfer_types_are_rejected_before_changes(json_store, tmp_path):
    with pytest.raises(StoreError):
        json_store.execute_operation({})
    target = Store(tmp_path / "target.json", month="2026-10", initial_role="staged")
    try:
        assert not target.preview_local({}).supported
        before = target.path.read_bytes()
        with pytest.raises(StoreError):
            target.import_local("transfer", {})
        assert target.path.read_bytes() == before
    finally:
        target.close()


def test_transfer_of_unwritten_source_supports_null_operation_and_read_only_copy(json_store, tmp_path):
    snapshot = json_store.export_local()
    snapshot = LocalTransferSnapshot(replace(snapshot.source, operation_id=None), snapshot.financial, snapshot.protected)
    target = Store(tmp_path / "target.json", month="2026-10", initial_role="staged")
    try:
        staged = target.import_local("transfer", snapshot)
        assert staged.operation_id is None
        copied = target.set_role("transfer", staged, "copy")
        with pytest.raises(StoreError, match="fenced"):
            target.add_account("demo", {"name": "Fictional", "type": "depository", "balance": 0})
        with pytest.raises(StoreError, match="prepared"):
            target.set_role("transfer", copied, "primary", authority_epoch=str(uuid4()))
        assert target.import_local("transfer", snapshot) == copied
    finally:
        target.close()


def test_receipt_expiry_never_reexecutes_stale_unknown_operation(json_store, monkeypatch):
    monkeypatch.setattr(authority, "MAX_OPERATION_RECEIPTS", 2)
    requests = []
    for index in range(3):
        request = operation(json_store, "add_category", {"name": f"Fictional {index}", "group": "Fictional", "target": 0}, identifier=f"operation-{index}")
        requests.append(request)
        json_store.execute_operation(request)
    assert json_store.operation_receipt(requests[0].operation_id) is None
    with pytest.raises(StoreError, match="unknown"):
        json_store.execute_operation(requests[0])


def test_native_v1_adoption_persists_identity_without_changing_generation_or_epoch(json_store):
    before = json_store.commit_evidence()
    path = json_store.path
    value = json.loads(path.read_bytes())
    schema_one(value)
    json_store.close()
    path.write_bytes(codec.serialize(value))
    original = path.read_bytes()
    adopted = Store(path, initial_role="staged")
    try:
        evidence = adopted.commit_evidence()
        assert evidence.role == "primary"
        assert evidence.generation == before.generation and evidence.authority_epoch == before.authority_epoch
        assert json.loads(path.read_bytes())["formatVersion"] == 3
        recovery = path.with_name(path.name + ".schema1-recovery")
        assert recovery.read_bytes() == original
        assert stat.S_IMODE(recovery.stat().st_mode) == 0o600
        assert set(adopted.export_budget_files()) == {"budget.json"}
    finally:
        adopted.close()
    reopened = Store(path)
    try:
        assert reopened.commit_evidence() == evidence
    finally:
        reopened.close()


def test_native_v1_adoption_does_not_mark_saved_user_data_empty(json_store):
    json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 0})
    path = json_store.path
    value = json.loads(path.read_bytes())
    schema_one(value)
    json_store.close()
    path.write_bytes(codec.serialize(value))
    adopted = Store(path)
    try:
        assert not adopted._data["authority"]["empty"]
        assert len(adopted.accounts("personal")) == 1
    finally:
        adopted.close()


def test_schema_one_recovery_retains_exact_encrypted_connection_and_checkpoint(json_store, json_account, json_transaction, json_changes):
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    json_store.apply_sync("item", json_changes(added=[json_transaction()]), "checkpoint", [json_account(mask="")])
    expected_item = json_store.get_item("item")
    path, raw = legacy_file(json_store)
    reopened = Store(path)
    try:
        assert reopened.get_item("item") == expected_item
        assert reopened.accounts("personal")[0]["mask"] == ""
        assert path.with_name(path.name + ".schema1-recovery").read_bytes() == raw
    finally:
        reopened.close()


@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_schema_two_requires_complete_protected_account_metadata(json_store, workspace):
    if workspace == "personal":
        json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 0})
    document = json.loads(json_store.path.read_bytes())
    document["workspaces"][workspace]["associations"] = []
    with pytest.raises(StoreError):
        codec.decode(json.dumps(document).encode())


def legacy_file(json_store):
    path = json_store.path
    document = schema_one(json.loads(path.read_bytes()))
    # The legacy codec still supports its original exact format.
    raw = codec.serialize(document)
    assert codec.encode(codec.decode(raw))[0]
    json_store.close()
    path.write_bytes(raw)
    return path, raw


def test_existing_matching_schema_one_recovery_is_reused_after_interrupted_upgrade(json_store):
    path, raw = legacy_file(json_store)

    def fail(name):
        if name == "before_replace":
            assert path.with_name(path.name + ".schema1-recovery").read_bytes() == raw
            raise OSError("fictional commit fault")

    with pytest.raises(StoreError):
        Store(path, commit_hook=fail)
    assert path.read_bytes() == raw
    recovery = path.with_name(path.name + ".schema1-recovery")
    recovery.chmod(0o644)
    recovered = Store(path)
    try:
        assert json.loads(path.read_bytes())["formatVersion"] == 3
        assert path.with_name(path.name + ".schema1-recovery").read_bytes() == raw
        assert stat.S_IMODE(recovery.stat().st_mode) == 0o600
    finally:
        recovered.close()


@pytest.mark.parametrize("symlink", [False, True])
def test_conflicting_or_linked_schema_one_recovery_is_never_overwritten(json_store, symlink):
    path, raw = legacy_file(json_store)
    recovery = path.with_name(path.name + ".schema1-recovery")
    other = path.with_name("fictional-older-recovery")
    other.write_bytes(b"fictional prior preserved artifact")
    if symlink:
        recovery.symlink_to(other)
    else:
        recovery.write_bytes(other.read_bytes())
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == raw
    assert recovery.read_bytes() == other.read_bytes()


@pytest.mark.parametrize("boundary", ["file_flush", "directory_flush", "link"])
def test_failed_schema_one_backup_keeps_original_authoritative(json_store, monkeypatch, boundary):
    path, raw = legacy_file(json_store)
    original_fsync = os.fsync
    original_directory = durability.flush_directory

    def failed_file(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("fictional full disk")
        return original_fsync(descriptor)

    def failed_directory(directory):
        if path.with_name(path.name + ".schema1-recovery").exists():
            raise OSError("fictional directory flush fault")
        return original_directory(directory)

    def failed_link(*_, **__):
        raise FileExistsError("fictional competing recovery artifact")

    if boundary == "file_flush":
        monkeypatch.setattr(durability.os, "fsync", failed_file)
    elif boundary == "directory_flush":
        monkeypatch.setattr(durability, "flush_directory", failed_directory)
    else:
        monkeypatch.setattr(durability.os, "link", failed_link)
    with pytest.raises(StoreError, match="recovery"):
        Store(path)
    assert path.read_bytes() == raw
    assert not list(path.parent.glob(".whab-schema1-*"))


def test_failed_recovery_temp_cleanup_never_masks_authored_failure(json_store, monkeypatch):
    path, raw = legacy_file(json_store)
    original = type(path).unlink

    def deny(temporary, *args, **kwargs):
        if temporary.name.startswith(".whab-schema1-"):
            raise OSError("fictional private cleanup fault")
        return original(temporary, *args, **kwargs)

    monkeypatch.setattr(type(path), "unlink", deny)
    try:
        with pytest.raises(StoreError, match="recovery") as raised:
            Store(path)
        assert "private" not in str(raised.value)
        assert path.read_bytes() == raw
    finally:
        for temporary in path.parent.glob(".whab-schema1-*"):
            original(temporary)


@pytest.mark.parametrize("initial_role", ["unknown", "retired", "fenced", None, []])
def test_invalid_initial_role_does_not_create_runtime_files(tmp_path, initial_role):
    path = tmp_path / "data" / "budget.json"
    with pytest.raises(StoreError):
        Store(path, initial_role=initial_role)
    assert not path.parent.exists()


@pytest.mark.parametrize("identity", [{"budget_id": "another"}, {"environment": "production"}])
def test_existing_native_identity_cannot_be_rebound(json_store, identity):
    path = json_store.path
    before = path.read_bytes()
    json_store.close()
    with pytest.raises(StoreError, match="identity"):
        Store(path, **identity)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [
    lambda value: value["authority"]["transfer"].update(digest="x" * 64),
    lambda value: value["authority"]["transfer"]["source"].update(budget_id="another"),
    lambda value: value["authority"]["transfer"]["source"].update(environment="production"),
    lambda value: value["authority"]["transfer"]["source"].update(instance_id=value["authority"]["instanceId"]),
    lambda value: value["authority"]["receipts"].append(value["authority"]["receipts"][0].copy()),
    lambda value: value["authority"]["receipts"][0]["evidence"].update(budget_id="another"),
    lambda value: value["authority"]["receipts"][0]["evidence"].update(environment="production"),
    lambda value: value["authority"]["receipts"][0]["evidence"].update(generation=value["generation"] + 1),
    lambda value: value["authority"]["receipts"][0]["evidence"].update(state_digest="f" * 64),
])
def test_corrupt_transfer_and_operation_evidence_cannot_open(json_store, tmp_path, change):
    request = operation(json_store, "add_account", {"name": "Fictional", "type": "depository", "balance": 10000})
    json_store.execute_operation(request)
    target = Store(tmp_path / "target.json", month="2026-10", initial_role="staged")
    try:
        target.import_local("transfer", json_store.export_local())
        value = json.loads(target.path.read_bytes())
        path = target.path
    finally:
        target.close()
    change(value)
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("value", ["bad-uuid", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"])
def test_legacy_uuid_identity_rules_remain_strict(json_store, value):
    document = json.loads(json_store.path.read_bytes())
    document.pop("authority")
    document.update(formatVersion=1, operationId=value)
    with pytest.raises(StoreError):
        codec.decode(json.dumps(document).encode())


@pytest.mark.parametrize("change", [
    lambda value: value.update(extra=True),
    lambda value: value.update(instanceId="not-a-uuid"),
    lambda value: value.update(role="arbitrary"),
    lambda value: value.update(empty=1),
    lambda value: value.update(transitionId=""),
    lambda value: value.update(transfer={}),
    lambda value: value.update(receipts={}),
])
def test_corrupt_authority_metadata_is_refused_without_rewriting_raw_file(json_store, change):
    path = json_store.path
    value = json.loads(path.read_bytes())
    change(value["authority"])
    raw = json.dumps(value).encode()
    json_store.close()
    path.write_bytes(raw)
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == raw
