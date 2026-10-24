"""Public same-device data contracts use fictional data and no concrete backend."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from tests.receipt_images import png
from whab.domain import commands
from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state
from whab.domain.money import MAX_CENTS
from whab.plugins import (
    CommitEvidence,
    LocalTransferSnapshot,
    OperationReceipt,
    Plugin,
    StorageOperation,
)
from whab.plugins.storage_transfer import (
    OperationResult,
    TransferPreview,
    canonical_storage_json,
    check_storage_operation,
    decode_local_state,
    dispatch_storage_operation,
    encode_local_state,
    evidence_record,
    local_state_digest,
    read_evidence,
    read_operation_receipt,
    read_storage_json,
    receipt_record,
    require_primary,
    validate_role_transition,
)


def states():
    result = {workspace: initial_state(workspace, "2026-01") for workspace in ("personal", "demo")}
    personal = result["personal"]
    commands.add_account(personal, "same", {"name": "Fictional", "type": "depository", "balance": 1000})
    commands.add_transaction(personal, "same", {"accountId": "same", "date": "2026-01-02",
                             "merchant": "Fictional market", "amount": 100, "categoryId": "groceries"}, "manual")
    personal["transactions"]["same"].update(note="Kept note", reviewed=True, categoryEdited=True, deleted=True)
    personal["receipts"]["same"] = png()
    personal["items"]["provider:item"] = {
        "id": "provider:item", "token": "encrypted-fixture", "institutionName": "Fictional bank",
        "status": "healthy", "lastSynced": "2026-01-02T00:00:00+00:00", "error": None,
        "cursor": "checkpoint-fixture", "provider": "fictional-bank",
    }
    personal["accounts"]["same"].update(source="fictional-bank", itemId="provider:item", mask="1111")
    personal["importReceipt"] = {"pluginId": "fictional-import", "fingerprint": "fictional-file",
                                  "counts": {"accounts": 1, "categories": 9, "transactions": 1, "assignments": 0}}
    return result


def evidence(data=None, **changes):
    financial, protected = encode_local_state(data or states())
    values = dict(budget_id="1bbfcfce-42d1-41c3-a844-b58ba691c313", environment="sandbox",
                  instance_id=str(uuid4()), authority_epoch=str(uuid4()), generation=3,
                  operation_id="operation-3", role="primary", state_digest=local_state_digest(financial, protected))
    values.update(changes)
    return CommitEvidence(**values)


def snapshot(data=None, **changes):
    data = data or states()
    return LocalTransferSnapshot(evidence(data, **changes), *encode_local_state(data))


def operation(current, **changes):
    values = dict(operation_id="request-4", authority_epoch=current.authority_epoch,
                  expected_generation=current.generation, workspace="personal", name="assign",
                  payload=b'{"assigned":10,"month":"2026-01","categoryId":"groceries"}')
    values.update(changes)
    return StorageOperation(**values)


@pytest.mark.parametrize("paths", [[], ("",), ("..",), ("../budget.json",), ("/budget.json",),
                                  ("a//budget.json",), ("a/./budget.json",), ("C:/budget.json",),
                                  ("a\\budget.json",), ("a\x00.json",), ("a\n.json",), ("\ud800",),
                                  ("budget.json", "budget.json"), tuple(f"{n}.json" for n in range(33))])
def test_storage_payload_paths_are_bounded_exact_portable_names(paths):
    with pytest.raises(ValueError, match="Storage payload paths"):
        Plugin("fictional", "Fictional", storage=lambda _: None, storage_payload_paths=paths)


def test_storage_payload_paths_require_storage_and_allow_explicit_generated_files():
    with pytest.raises(ValueError, match="Storage payload paths"):
        Plugin("fictional", "Fictional", storage_payload_paths=("budget.json",))
    descriptor = Plugin("fictional", "Fictional", storage=lambda _: None,
                        storage_payload_paths=("budget.json", "receipts/images.json"))
    assert descriptor.storage_payload_paths == ("budget.json", "receipts/images.json")


def test_complete_local_snapshot_preserves_tombstones_images_provider_and_both_workspaces():
    original = states()
    transfer = snapshot(original)
    restored = decode_local_state(transfer.financial, transfer.protected)
    assert encode_local_state(restored) == encode_local_state(original)
    assert restored["personal"]["transactions"]["same"]["deleted"] is True
    assert restored["personal"]["receipts"]["same"] == png()
    assert restored["personal"]["items"] == original["personal"]["items"]
    assert restored["personal"]["accounts"]["same"]["stale"] is False
    assert restored["personal"]["accounts"]["same"]["itemId"] == "provider:item"
    assert restored["personal"]["importReceipt"] == original["personal"]["importReceipt"]
    assert restored["demo"]["accounts"]["demo-checking"]["balance"] == original["demo"]["accounts"]["demo-checking"]["balance"]
    restored["personal"]["transactions"]["same"]["note"] = "Changed only detached copy"
    assert original["personal"]["transactions"]["same"]["note"] == "Kept note"
    assert "encrypted-fixture" not in repr(transfer)
    assert "Kept note" not in repr(transfer)


def test_canonical_transfer_is_independent_of_adapter_row_order():
    original = states()
    reversed_rows = deepcopy(original)
    for state in reversed_rows.values():
        for name in ("accounts", "categories", "transactions", "items"):
            state[name] = dict(reversed(tuple(state[name].items())))
    assert encode_local_state(original) == encode_local_state(reversed_rows)


def test_state_digest_excludes_receipt_metadata_but_transfer_digest_binds_it():
    transfer = snapshot()
    request = operation(transfer.source)
    previous = OperationReceipt("request-4", request.fingerprint, b'{"id":"generated-id"}',
                                replace(transfer.source, operation_id="request-4"))
    with_receipt = replace(transfer, receipts=(previous,))
    assert with_receipt.source.state_digest == transfer.source.state_digest
    assert with_receipt.digest != transfer.digest
    assert read_operation_receipt(receipt_record(previous)) == previous
    assert read_evidence(evidence_record(transfer.source)) == transfer.source


def test_exact_receipt_replays_before_expected_generation_check():
    old = evidence()
    request = operation(old)
    saved = OperationReceipt(request.operation_id, request.fingerprint, b'{"id":"saved-id"}',
                             replace(old, generation=4, operation_id=request.operation_id))
    current = replace(old, generation=99, authority_epoch=str(uuid4()))
    assert check_storage_operation(current, request, saved) == OperationResult(saved, True)
    with pytest.raises(StoreError, match="different storage command"):
        check_storage_operation(current, replace(request, payload=b'{"assigned":11,"month":"2026-01","categoryId":"groceries"}'), saved)
    with pytest.raises(StoreError, match="outcome is unknown"):
        check_storage_operation(current, request)
    assert check_storage_operation(old, request) is None
    assert "saved-id" not in repr(saved)


@pytest.mark.parametrize("role", ["staged", "copy", "fenced", "retired"])
def test_all_nonprimary_roles_fence_user_commands(role):
    current = evidence(role=role)
    with pytest.raises(StoreError, match="fenced"):
        require_primary(current)
    with pytest.raises(StoreError, match="fenced"):
        check_storage_operation(current, operation(current))


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1.5}', b'{"a":"\\ud800"}',
                                     b'[' * 25 + b']' * 25, b'{"a":true', b'\xff', b'{"a":9007199254740992}'])
def test_invalid_json_is_rejected_without_echoing_body(raw):
    with pytest.raises(StoreError) as error:
        read_storage_json(raw)
    assert "a\"" not in str(error.value)


@pytest.mark.parametrize("change", [{"budget_id": "\ud800"}, {"budget_id": "x\x00y"},
                                     {"generation": True}, {"environment": "other"}, {"state_digest": "x"},
                                     {"authority_epoch": "bad"}, {"role": "writer"}])
def test_invalid_commit_evidence_is_rejected(change):
    with pytest.raises(StoreError):
        evidence(**change)


@pytest.mark.parametrize("change", [{"name": "__getattribute__"}, {"payload": b'{}'},
                                     {"name": "disconnect_item", "workspace": "demo", "payload": b'{"itemId":"id"}'},
                                     {"payload": b'{"assigned":1,"month":"2026-01","categoryId":"groceries","secret":"no"}'}])
def test_operation_dispatch_is_closed_and_scoped(change):
    with pytest.raises(StoreError):
        operation(evidence(), **change)


def test_transfer_rejects_corrupt_references_receipts_schema_and_evidence():
    transfer = snapshot()
    financial = read_storage_json(transfer.financial, limit=128 * 1024 * 1024)
    financial["workspaces"]["personal"]["receipts"][0]["transactionId"] = "absent"
    with pytest.raises(StoreError):
        decode_local_state(canonical_storage_json(financial), transfer.protected)
    with pytest.raises(StoreError, match="commit evidence"):
        replace(transfer, source=replace(transfer.source, state_digest="0" * 64))
    with pytest.raises(StoreError):
        replace(transfer, receipts=[object()])
    private = read_storage_json(transfer.protected)
    private["workspaces"]["demo"]["items"] = private["workspaces"]["personal"]["items"]
    with pytest.raises(StoreError):
        decode_local_state(transfer.financial, canonical_storage_json(private))


def test_dispatch_preserves_absent_optional_edit_fields_and_binary_receipt():
    calls = []

    class Backend:
        def edit_transaction(self, *args, **kwargs):
            calls.append((args, kwargs))
        def save_receipt(self, *args):
            calls.append(args)

    request = operation(evidence(), name="edit_transaction", payload=b'{"transactionId":"row","reviewed":true}')
    assert dispatch_storage_operation(Backend(), request) is None
    assert calls == [(("personal", "row"), {"reviewed": True})]
    import base64
    request = operation(evidence(), name="save_receipt", payload=canonical_storage_json({"transactionId": "row", "image": base64.b64encode(png()).decode()}))
    dispatch_storage_operation(Backend(), request)
    assert calls[-1] == ("personal", "row", png())


def test_preview_reasons_are_fixed_safe_codes():
    assert TransferPreview(True, True).reason_code is None
    with pytest.raises(StoreError):
        TransferPreview(False, False, "private detail")


@pytest.mark.parametrize(("old", "new", "same_id", "new_epoch", "allowed"), [
    ("primary", "fenced", False, False, True),
    ("primary", "retired", False, False, False),
    ("primary", "staged", False, False, False),
    ("fenced", "primary", True, False, True),
    ("fenced", "retired", True, False, True),
    ("fenced", "primary", False, False, False),
    ("fenced", "primary", True, True, False),
    ("staged", "primary", True, True, True),
    ("staged", "primary", True, False, False),
    ("staged", "copy", True, False, True),
    ("copy", "primary", True, True, True),
    ("retired", "primary", True, True, False),
    ("primary", "primary", True, False, True),
    ("primary", "primary", False, False, False),
])
def test_role_transitions_require_matching_host_identity_and_fresh_activation_epoch(old, new, same_id, new_epoch, allowed):
    current = evidence(role=old)
    epoch = str(uuid4()) if new_epoch else None
    previous = "transition" if same_id else "other-transition"
    if allowed:
        assert validate_role_transition(current, "transition", new, epoch, previous) == (epoch or current.authority_epoch)
    else:
        with pytest.raises(StoreError):
            validate_role_transition(current, "transition", new, epoch, previous)


def test_native_evidence_packing_does_not_impose_new_portable_volume_limits():
    data = states()
    row = data["personal"]["transactions"]["same"]
    data["personal"]["transactions"] = {
        str(number): {**row, "id": str(number), "amount": MAX_CENTS if number % 2 else -MAX_CENTS}
        for number in range(9008)
    }
    data["personal"]["receipts"] = {}
    financial, protected = encode_local_state(data, validate=False)
    assert len(local_state_digest(financial, protected)) == 64
    with pytest.raises(StoreError, match="totals exceed"):
        encode_local_state(data)
    with pytest.raises(StoreError):
        LocalTransferSnapshot(evidence(states()), financial, protected)


def test_receipt_from_other_budget_cannot_satisfy_colliding_operation_identity():
    current = evidence()
    request = operation(current)
    receipt = OperationReceipt(request.operation_id, request.fingerprint, b'null',
                               replace(current, budget_id="other-budget", operation_id=request.operation_id))
    with pytest.raises(StoreError):
        check_storage_operation(current, request, receipt)


def test_unattached_account_masks_are_preserved_and_bound_into_digest_for_both_workspaces():
    original = states()
    original["demo"]["accounts"]["demo-checking"]["mask"] = "4821"
    commands.add_account(original["personal"], "unattached", {"name": "Fictional", "type": "depository", "balance": 0})
    original["personal"]["accounts"]["unattached"]["mask"] = "9012"
    transfer = snapshot(original)
    restored = decode_local_state(transfer.financial, transfer.protected)
    assert restored["demo"]["accounts"]["demo-checking"]["mask"] == "4821"
    assert restored["personal"]["accounts"]["unattached"]["mask"] == "9012"
    changed = deepcopy(original)
    changed["demo"]["accounts"]["demo-checking"]["mask"] = "3389"
    financial, protected = encode_local_state(changed)
    assert financial == transfer.financial
    assert local_state_digest(financial, protected) != transfer.source.state_digest
    private = read_storage_json(transfer.protected)
    private["workspaces"]["demo"]["associations"] = []
    with pytest.raises(StoreError):
        decode_local_state(transfer.financial, canonical_storage_json(private))
    private = read_storage_json(transfer.protected)
    private["workspaces"]["personal"]["associations"] = [
        record for record in private["workspaces"]["personal"]["associations"]
        if record["accountId"] != "unattached"
    ]
    with pytest.raises(StoreError):
        decode_local_state(transfer.financial, canonical_storage_json(private))
