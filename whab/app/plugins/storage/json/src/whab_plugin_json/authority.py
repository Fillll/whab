"""JSON-owned atomic authority metadata, durable commands and local transfer."""

import copy
from dataclasses import replace
from uuid import uuid4

from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state, require_pristine
from whab.plugins.storage_replica import (
    ReplicaWatermark,
    read_replica_watermark,
    replica_record,
    validate_replica_apply,
)
from whab.plugins.storage_transfer import (
    MAX_OPERATION_RECEIPTS,
    CommitEvidence,
    LocalTransferSnapshot,
    OperationReceipt,
    OperationResult,
    StorageOperation,
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
    receipt_record,
    storage_identifier,
)


def fresh_metadata(budget_id, environment, role, *, empty):
    value = {"budgetId": budget_id, "environment": environment, "instanceId": str(uuid4()),
             "role": role, "transitionId": None, "transfer": None, "receipts": [], "empty": empty,
             "replica": None, "promotionId": None}
    return validate_metadata(value)


def validate_metadata(value, *, replica=True):
    if type(value) is not dict or value.keys() != {
        "budgetId", "environment", "instanceId", "role", "transitionId", "transfer", "receipts", "empty",
    } | ({"replica", "promotionId"} if replica else set()):
        raise StoreError("Invalid JSON storage authority metadata.")
    CommitEvidence(value["budgetId"], value["environment"], value["instanceId"],
                   "00000000-0000-0000-0000-000000000000", 0, None, value["role"], "0" * 64)
    if value["transitionId"] is not None:
        storage_identifier(value["transitionId"])
    if type(value["empty"]) is not bool:
        raise StoreError("Invalid JSON storage authority metadata.")
    if replica:
        if value["replica"] is not None:
            watermark = read_replica_watermark(value["replica"])
            if watermark.source_instance_id == value["instanceId"]:
                raise StoreError("Invalid JSON replica source identity.")
        if value["promotionId"] is not None:
            storage_identifier(value["promotionId"])
            if (value["role"] != "copy" or value["replica"] is None
                    or value["transitionId"] != value["promotionId"]):
                raise StoreError("Invalid JSON replica promotion identity.")
    transfer = value["transfer"]
    if transfer is not None:
        if type(transfer) is not dict or transfer.keys() != {"id", "digest", "source"}:
            raise StoreError("Invalid JSON local transfer identity.")
        storage_identifier(transfer["id"])
        if type(transfer["digest"]) is not str or len(transfer["digest"]) != 64 or any(c not in "0123456789abcdef" for c in transfer["digest"]):
            raise StoreError("Invalid JSON local transfer identity.")
        source = read_evidence(transfer["source"])
        if (source.budget_id != value["budgetId"] or source.environment != value["environment"]
                or source.instance_id == value["instanceId"]):
            raise StoreError("Invalid JSON local transfer identity.")
    records = value["receipts"]
    if type(records) is not list or len(records) > MAX_OPERATION_RECEIPTS:
        raise StoreError("Invalid JSON operation receipts.")
    seen = set()
    for record in records:
        receipt = read_operation_receipt(record)
        if (receipt.operation_id in seen or receipt.evidence.budget_id != value["budgetId"]
                or receipt.evidence.environment != value["environment"]):
            raise StoreError("Invalid JSON operation receipts.")
        seen.add(receipt.operation_id)
    return copy.deepcopy(value)


def pristine_document(document):
    try:
        personal, demo = (document["workspaces"][key] for key in ("personal", "demo"))
        require_pristine(personal)
        month = demo["transactions"]["demo-groceries"]["date"][:7]
        current = encode_local_state({"personal": personal, "demo": demo})
        expected = encode_local_state({"personal": personal, "demo": initial_state("demo", month)})
        return current == expected
    except (StoreError, KeyError, TypeError):
        return False


class Authority:
    def _evidence(self, document):
        metadata = document["authority"]
        financial, protected = encode_local_state(document["workspaces"], validate=False)
        return CommitEvidence(metadata["budgetId"], metadata["environment"], metadata["instanceId"],
                              document["authorityEpoch"], document["generation"], document["operationId"],
                              metadata["role"], local_state_digest(financial, protected))

    def commit_evidence(self):
        with self._lock:
            self._check_current()
            return self._evidence(self._data)

    def set_role(self, transition_id, expected, role, *, authority_epoch=None):
        from whab.plugins.storage_transfer import validate_role_transition

        with self._lock:
            self._check_current()
            current = self._evidence(self._data)
            metadata = self._data["authority"]
            if type(expected) is not CommitEvidence:
                raise StoreError("Role changes require complete expected evidence.")
            epoch = validate_role_transition(expected, transition_id, role, authority_epoch=authority_epoch,
                                             previous_transition_id=metadata["transitionId"])
            target = replace(expected, role=role, authority_epoch=epoch)
            if current != expected:
                if target == current and metadata["transitionId"] == transition_id:
                    return current
                raise StoreError("Storage changed before its role transition.")
            if target == current:
                return current
            if current.role == "copy" and role == "primary" and metadata["promotionId"] != transition_id:
                raise StoreError("Copy promotion requires its matching prepared transition.")
            candidate = copy.deepcopy(self._data)
            candidate["authority"].update(role=target.role, transitionId=transition_id)
            if role == "primary":
                candidate["authority"]["promotionId"] = None
            candidate["authorityEpoch"] = target.authority_epoch
            self._commit(candidate)
            return self._evidence(self._data)

    def operation_receipt(self, operation_id):
        storage_identifier(operation_id)
        with self._lock:
            self._check_current()
            for record in self._data["authority"]["receipts"]:
                if record["operation_id"] == operation_id:
                    return read_operation_receipt(record)
            return None

    def execute_operation(self, operation):
        if type(operation) is not StorageOperation:
            raise StoreError("Durable commands require a validated storage operation.")
        with self._lock:
            self._check_current()
            current = self._evidence(self._data)
            previous = self.operation_receipt(operation.operation_id)
            replay = check_storage_operation(current, operation, previous)
            if replay is not None:
                return replay
            candidate = copy.deepcopy(self._data)
            self._pending = candidate
            try:
                result = dispatch_storage_operation(self, operation)
                candidate["generation"] += 1
                candidate["operationId"] = operation.operation_id
                candidate["authority"]["empty"] = False
                receipt = OperationReceipt(operation.operation_id, operation.fingerprint,
                                           canonical_storage_json(result), self._evidence(candidate))
                records = candidate["authority"]["receipts"]
                records.append(receipt_record(receipt))
                del records[:-MAX_OPERATION_RECEIPTS]
                self._commit(candidate)
                return OperationResult(receipt)
            finally:
                self._pending = None

    def export_local(self):
        with self._lock:
            self._check_current()
            financial, protected = encode_local_state(self._data["workspaces"])
            return LocalTransferSnapshot(self._evidence(self._data), financial, protected,
                                         tuple(read_operation_receipt(row) for row in self._data["authority"]["receipts"]))

    def preview_local(self, snapshot):
        from . import codec

        with self._lock:
            self._check_current()
            empty = self._data["authority"]["empty"] and pristine_document(self._data)
            if not empty:
                return TransferPreview(False, False, "destination_not_empty")
            try:
                candidate = self._import_candidate("preview", snapshot, preview=True)
                codec.encode(candidate)
            except StoreError:
                return TransferPreview(True, False, "unsupported_records")
            return TransferPreview(True, True)

    def _import_candidate(self, transfer_id, snapshot, *, preview=False):
        storage_identifier(transfer_id)
        if type(snapshot) is not LocalTransferSnapshot:
            raise StoreError("Local transfer requires a validated snapshot.")
        current, source = self._evidence(self._data), snapshot.source
        if current.budget_id != source.budget_id or current.environment != source.environment or current.instance_id == source.instance_id:
            raise StoreError("Local transfer storage identity does not match.")
        if current.role not in ({"staged", "copy"} if preview else {"staged"}) or not self._data["authority"]["empty"] or not pristine_document(self._data):
            raise StoreError("Local transfer requires an empty staged destination.")
        candidate = copy.deepcopy(self._data)
        candidate.update(generation=source.generation, operationId=source.operation_id,
                         authorityEpoch=source.authority_epoch,
                         workspaces=decode_local_state(snapshot.financial, snapshot.protected))
        candidate["authority"].update(
            empty=False, transitionId=transfer_id,
            transfer={"id": transfer_id, "digest": snapshot.digest, "source": evidence_record(source)},
            receipts=[receipt_record(row) for row in snapshot.receipts],
        )
        return candidate

    def import_local(self, transfer_id, snapshot):
        with self._lock:
            self._check_current()
            storage_identifier(transfer_id)
            if type(snapshot) is not LocalTransferSnapshot:
                raise StoreError("Local transfer requires a validated snapshot.")
            previous = self._data["authority"]["transfer"]
            if previous is not None:
                if previous["id"] == transfer_id and previous["digest"] == snapshot.digest:
                    current = self._evidence(self._data)
                    if current.state_digest == snapshot.source.state_digest and current.generation == snapshot.source.generation:
                        return current
                    raise StoreError("This destination changed after its local transfer.")
                raise StoreError("This destination already received another local transfer.")
            self._commit(self._import_candidate(transfer_id, snapshot))
            return self._evidence(self._data)

    def replica_watermark(self):
        with self._lock:
            self._check_current()
            record = self._data["authority"]["replica"]
            return None if record is None else read_replica_watermark(record)

    def apply_replica(self, replica_id, snapshot, namespace_digest, expected, *, namespace_generation):
        with self._lock:
            self._check_current()
            metadata = self._data["authority"]
            watermark, replayed = validate_replica_apply(
                self._evidence(self._data), self.replica_watermark(), replica_id,
                snapshot, namespace_digest, expected, namespace_generation=namespace_generation,
                empty=metadata["empty"] and pristine_document(self._data),
                promotion_id=metadata["promotionId"],
            )
            if replayed:
                return watermark
            candidate = copy.deepcopy(self._data)
            candidate.update(generation=snapshot.source.generation, operationId=snapshot.source.operation_id,
                             authorityEpoch=snapshot.source.authority_epoch,
                             workspaces=decode_local_state(snapshot.financial, snapshot.protected))
            candidate["authority"].update(empty=False, replica=replica_record(watermark),
                                           receipts=[receipt_record(row) for row in snapshot.receipts])
            self._commit(candidate)
            return watermark

    def prepare_promotion(self, transition_id, expected, watermark):
        storage_identifier(transition_id)
        with self._lock:
            self._check_current()
            current = self._evidence(self._data)
            metadata = self._data["authority"]
            saved = self.replica_watermark()
            if (type(expected) is not CommitEvidence or current != expected or current.role != "copy"
                    or type(watermark) is not ReplicaWatermark or saved is None or saved != watermark
                    or metadata["promotionId"] not in {None, transition_id}):
                raise StoreError("Copy changed before its promotion preparation.")
            if metadata["promotionId"] == transition_id:
                return current
            candidate = copy.deepcopy(self._data)
            candidate["authority"].update(transitionId=transition_id, promotionId=transition_id)
            self._commit(candidate)
            return self._evidence(self._data)

    def abort_promotion(self, transition_id, expected):
        storage_identifier(transition_id)
        with self._lock:
            self._check_current()
            current = self._evidence(self._data)
            metadata = self._data["authority"]
            if (type(expected) is not CommitEvidence or current != expected or current.role != "copy"
                    or metadata["promotionId"] not in {None, transition_id}):
                raise StoreError("Copy promotion does not match this abort request.")
            if metadata["promotionId"] is None:
                return current
            candidate = copy.deepcopy(self._data)
            candidate["authority"].update(transitionId=None, promotionId=None)
            self._commit(candidate)
            return self._evidence(self._data)
