"""SQLite-owned authority, atomic command receipts and protected local transfer."""

from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

from whab.domain.dates import shift_month
from whab.domain.errors import StoreError
from whab.domain.ledger_state import require_pristine
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
    read_storage_json,
    receipt_record,
    storage_identifier,
    validate_role_transition,
)

from .seeding import build_initial_state


class CommitUncertain(StoreError):
    def __init__(self, operation_id, generation):
        super().__init__("The SQLite commit outcome is uncertain. Reopen this budget and inspect its durable operation identity before retrying.")
        self.operation_id, self.generation = operation_id, generation


class Authority:
    def _authority_initialize(self, budget_id, environment, initial_role, *, existing):
        self._writing = None
        self._uncertain = None
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name IN ('storage_authority','storage_operations')")}
            if tables and tables != {"storage_authority", "storage_operations"}:
                raise StoreError("SQLite storage authority schema is incomplete; preserve this database for recovery.")
            # Schema publication and its first authority record form one native
            # commit. An interrupted legacy adoption leaves no partial schema.
            self.db.execute("CREATE TABLE IF NOT EXISTS storage_authority(id INTEGER PRIMARY KEY CHECK(id=1),record BLOB NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS storage_operations(operation_id TEXT PRIMARY KEY,generation INTEGER NOT NULL,record BLOB NOT NULL)")
            row = self.db.execute("SELECT record FROM storage_authority WHERE id=1").fetchone()
            if row is None:
                if tables:
                    raise StoreError("SQLite storage authority metadata is missing; preserve this database for recovery.")
                metadata = {"formatVersion": 2, "budgetId": budget_id, "environment": environment,
                            "instanceId": str(uuid4()), "authorityEpoch": str(uuid4()), "generation": 0,
                            "operationId": None, "role": "primary" if existing else initial_role,
                            "transitionId": None, "transfer": None, "empty": self._pristine(),
                            "replica": None, "promotionId": None}
                self._metadata_evidence(metadata, "0" * 64)
                self._save_authority(metadata, create=True)
            else:
                metadata = self._authority_metadata()
                if metadata["budgetId"] != budget_id or metadata["environment"] != environment:
                    raise StoreError("SQLite storage identity belongs to another budget or environment.")
                if metadata["formatVersion"] == 1:
                    metadata.update(formatVersion=2, replica=None, promotionId=None)
                    self._save_authority(metadata)
            if self.db.execute("SELECT COUNT(*) FROM storage_operations").fetchone()[0] > MAX_OPERATION_RECEIPTS:
                raise StoreError("SQLite operation receipts exceed their supported bound.")
            for receipt in self.db.execute("SELECT operation_id,generation,record FROM storage_operations"):
                self._read_stored_receipt(receipt, metadata)
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _check_authority(self):
        if self._uncertain is not None:
            raise CommitUncertain(*self._uncertain)

    def _authority_metadata(self):
        row = self.db.execute("SELECT record FROM storage_authority WHERE id=1").fetchone()
        if row is None:
            raise StoreError("SQLite storage authority metadata is missing.")
        if type(row[0]) is not bytes:
            raise StoreError("Invalid SQLite storage authority metadata.")
        metadata = read_storage_json(row[0])
        if type(metadata) is not dict or type(metadata.get("formatVersion")) is not int or metadata["formatVersion"] not in {1, 2}:
            raise StoreError("Invalid SQLite storage authority metadata.")
        if metadata.keys() != {
            "formatVersion", "budgetId", "environment", "instanceId", "authorityEpoch", "generation",
            "operationId", "role", "transitionId", "transfer", "empty",
        } | ({"replica", "promotionId"} if metadata["formatVersion"] == 2 else set()) or type(metadata["empty"]) is not bool:
            raise StoreError("Invalid SQLite storage authority metadata.")
        self._metadata_evidence(metadata, "0" * 64)
        if metadata["transitionId"] is not None:
            storage_identifier(metadata["transitionId"])
        if metadata["formatVersion"] == 2:
            record = metadata["replica"]
            if record is not None:
                watermark = read_replica_watermark(record)
                if (watermark.source_instance_id == metadata["instanceId"]
                        or (metadata["role"] == "copy" and (
                            metadata["authorityEpoch"] != watermark.source_authority_epoch
                            or metadata["generation"] != watermark.source_generation
                            or metadata["operationId"] != watermark.source_operation_id
                            or self._current_evidence(metadata).state_digest != watermark.state_digest))):
                    raise StoreError("Invalid SQLite replica data evidence.")
                if metadata["role"] == "copy":
                    financial, protected = encode_local_state(self._local_states())
                    source = replace(self._current_evidence(metadata), instance_id=watermark.source_instance_id, role="primary")
                    snapshot = LocalTransferSnapshot(source, financial, protected, tuple(
                        self._read_stored_receipt(row, metadata) for row in self.db.execute(
                            "SELECT operation_id,generation,record FROM storage_operations ORDER BY operation_id",
                        )
                    ))
                    if snapshot.digest != watermark.transfer_digest:
                        raise StoreError("Invalid SQLite replica transfer evidence.")
            if metadata["promotionId"] is not None:
                storage_identifier(metadata["promotionId"])
                if (metadata["role"] != "copy" or record is None
                        or metadata["transitionId"] != metadata["promotionId"]):
                    raise StoreError("Invalid SQLite replica promotion identity.")
        transfer = metadata["transfer"]
        if transfer is not None:
            if type(transfer) is not dict or transfer.keys() != {"id", "digest", "source"}:
                raise StoreError("Invalid SQLite local transfer identity.")
            storage_identifier(transfer["id"])
            digest = transfer["digest"]
            source = read_evidence(transfer["source"])
            if (type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                    or source.budget_id != metadata["budgetId"] or source.environment != metadata["environment"]
                    or source.instance_id == metadata["instanceId"]):
                raise StoreError("Invalid SQLite local transfer identity.")
        return metadata

    def _save_authority(self, metadata, *, create=False):
        raw = canonical_storage_json(metadata)
        if create:
            self.db.execute("INSERT INTO storage_authority(id,record) VALUES(1,?)", (raw,))
        elif self.db.execute("UPDATE storage_authority SET record=? WHERE id=1", (raw,)).rowcount != 1:
            raise StoreError("SQLite storage authority metadata is missing.")

    @staticmethod
    def _metadata_evidence(metadata, digest):
        return CommitEvidence(metadata["budgetId"], metadata["environment"], metadata["instanceId"],
                              metadata["authorityEpoch"], metadata["generation"], metadata["operationId"],
                              metadata["role"], digest)

    def _local_states(self):
        states = {name: self._read_state(name) for name in ("personal", "demo")}
        for name, state in states.items():
            for row in self.db.execute("SELECT id,item_id,mask FROM accounts WHERE workspace=?", (name,)):
                state["accounts"][row[0]].update(itemId=row[1], mask=row[2])
            state["receipts"] = {row[0]: bytes(row[1]) for row in self.db.execute(
                "SELECT transaction_id,image FROM transaction_receipts WHERE workspace=?", (name,),
            )}
            imported = self.db.execute("SELECT * FROM workspace_imports WHERE workspace=?", (name,)).fetchone()
            state["importReceipt"] = None if imported is None else {
                "pluginId": imported["plugin_id"], "fingerprint": imported["fingerprint"], "counts": {
                    key: imported[column] for key, column in (
                        ("accounts", "account_count"), ("categories", "category_count"),
                        ("transactions", "transaction_count"), ("assignments", "assignment_count"),
                    )
                },
            }
        return states

    def _pristine(self):
        states = self._local_states()
        try:
            require_pristine(states["personal"])
            demo = states["demo"]
            seed = build_initial_state("demo", shift_month(demo["budgetStartMonth"], 5),
                                       as_of_date=demo["transactions"]["pending-demo"]["date"])
            expected = {"personal": states["personal"], "demo": seed}
            return encode_local_state(states, validate=False) == encode_local_state(expected, validate=False)
        except (StoreError, KeyError, TypeError):
            return False

    def _current_evidence(self, metadata=None):
        financial, protected = encode_local_state(self._local_states(), validate=False)
        return self._metadata_evidence(metadata or self._authority_metadata(), local_state_digest(financial, protected))

    @contextmanager
    def _authority_write(self, *, financial=True):
        with self._lock:
            if not self._authority_enabled:
                own = not self.db.in_transaction
                if own:
                    self.db.execute("BEGIN IMMEDIATE")
                try:
                    yield {"dirty": False}
                    if own:
                        self.db.execute("COMMIT")
                except BaseException:
                    if own and self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise
                return
            self._check_authority()
            if self._writing is not None:
                yield self._writing
                return
            self.db.execute("BEGIN IMMEDIATE")
            attempted = False
            transaction = None
            try:
                metadata = self._authority_metadata()
                previous_metadata = canonical_storage_json(metadata)
                if financial and metadata["role"] != "primary":
                    raise StoreError("This storage instance is fenced and cannot accept financial changes.")
                transaction = {"metadata": metadata, "dirty": False, "operation": None, "result": None, "receipt": None}
                self._writing = transaction
                yield transaction
                if transaction["dirty"]:
                    metadata.update(generation=metadata["generation"] + 1,
                                    operationId=transaction["operation"].operation_id if transaction["operation"] is not None else str(uuid4()),
                                    empty=False)
                    if transaction["operation"] is not None:
                        operation = transaction["operation"]
                        receipt = OperationReceipt(operation.operation_id, operation.fingerprint,
                                                   canonical_storage_json(transaction["result"]), self._current_evidence(metadata))
                        self.db.execute("INSERT INTO storage_operations(operation_id,generation,record) VALUES(?,?,?)",
                                        (receipt.operation_id, metadata["generation"], canonical_storage_json(receipt_record(receipt))))
                        self.db.execute("DELETE FROM storage_operations WHERE operation_id IN (SELECT operation_id FROM storage_operations ORDER BY generation DESC,operation_id DESC LIMIT -1 OFFSET ?)", (MAX_OPERATION_RECEIPTS,))
                        transaction["receipt"] = receipt
                if canonical_storage_json(metadata) != previous_metadata:
                    self._save_authority(metadata)
                attempted = True
                self.db.execute("COMMIT")
            except BaseException as error:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                elif attempted and transaction is not None:
                    metadata = transaction["metadata"]
                    self._uncertain = metadata["operationId"], metadata["generation"]
                    if isinstance(error, Exception):
                        raise CommitUncertain(*self._uncertain) from None
                raise
            finally:
                self._writing = None

    def commit_evidence(self):
        with self._snapshot():
            self._check_authority()
            return self._current_evidence()

    def operation_receipt(self, operation_id):
        storage_identifier(operation_id)
        with self._snapshot():
            self._check_authority()
            row = self.db.execute("SELECT operation_id,generation,record FROM storage_operations WHERE operation_id=?", (operation_id,)).fetchone()
            return None if row is None else self._read_stored_receipt(row, self._authority_metadata())

    def _read_stored_receipt(self, row, metadata):
        if type(row[2]) is not bytes:
            raise StoreError("Invalid SQLite operation receipt.")
        receipt = read_operation_receipt(read_storage_json(row[2]))
        evidence = receipt.evidence
        if (row[0] != receipt.operation_id or type(row[1]) is not int or row[1] != evidence.generation
                or evidence.budget_id != metadata["budgetId"] or evidence.environment != metadata["environment"]
                or evidence.generation > metadata["generation"]
                or (evidence.generation == metadata["generation"]
                    and evidence.state_digest != self._current_evidence(metadata).state_digest)):
            raise StoreError("Invalid SQLite operation receipt evidence.")
        return receipt

    def execute_operation(self, operation):
        if type(operation) is not StorageOperation:
            raise StoreError("Durable commands require a validated storage operation.")
        with self._authority_write() as transaction:
            current = self._current_evidence(transaction["metadata"])
            replay = check_storage_operation(current, operation, self.operation_receipt(operation.operation_id))
            if replay is not None:
                return replay
            transaction["operation"] = operation
            transaction["result"] = dispatch_storage_operation(self, operation)
            transaction["dirty"] = True
        return OperationResult(transaction["receipt"])

    def set_role(self, transition_id, expected, role, *, authority_epoch=None):
        with self._authority_write(financial=False) as transaction:
            metadata = transaction["metadata"]
            current = self._current_evidence(metadata)
            if type(expected) is not CommitEvidence:
                raise StoreError("Role changes require complete expected evidence.")
            epoch = validate_role_transition(expected, transition_id, role, authority_epoch,
                                             metadata["transitionId"])
            target = replace(expected, role=role, authority_epoch=epoch)
            if current != expected:
                if target == current and metadata["transitionId"] == transition_id:
                    return current
                raise StoreError("Storage changed before its role transition.")
            if current.role == "copy" and role == "primary" and metadata["promotionId"] != transition_id:
                raise StoreError("Copy promotion requires its matching prepared transition.")
            metadata.update(role=role, authorityEpoch=epoch, transitionId=transition_id)
            if role == "primary":
                metadata["promotionId"] = None
        return target

    def export_local(self):
        with self._snapshot():
            self._check_authority()
            financial, protected = encode_local_state(self._local_states())
            metadata = self._authority_metadata()
            receipts = tuple(self._read_stored_receipt(row, metadata) for row in self.db.execute(
                "SELECT operation_id,generation,record FROM storage_operations ORDER BY operation_id",
            ))
            return LocalTransferSnapshot(self._current_evidence(), financial, protected, receipts)

    def preview_local(self, snapshot):
        with self._snapshot():
            self._check_authority()
            metadata = self._authority_metadata()
            empty = metadata["empty"] and self._pristine()
            if not empty:
                return TransferPreview(False, False, "destination_not_empty")
            try:
                self._check_local_target(metadata, snapshot, preview=True)
            except StoreError:
                return TransferPreview(True, False, "unsupported_records")
            return TransferPreview(True, True)

    def _check_local_target(self, metadata, snapshot, *, preview=False):
        if type(snapshot) is not LocalTransferSnapshot:
            raise StoreError("Local transfer requires a validated snapshot.")
        source = snapshot.source
        if (metadata["budgetId"] != source.budget_id or metadata["environment"] != source.environment
                or metadata["instanceId"] == source.instance_id):
            raise StoreError("Local transfer storage identity does not match.")
        if metadata["role"] not in ({"staged", "copy"} if preview else {"staged"}) or not metadata["empty"] or not self._pristine():
            raise StoreError("Local transfer requires an empty staged destination.")
        return decode_local_state(snapshot.financial, snapshot.protected)

    def import_local(self, transfer_id, snapshot):
        storage_identifier(transfer_id)
        with self._authority_write(financial=False) as transaction:
            metadata = transaction["metadata"]
            if type(snapshot) is not LocalTransferSnapshot:
                raise StoreError("Local transfer requires a validated snapshot.")
            previous = metadata["transfer"]
            if previous is not None:
                current = self._current_evidence(metadata)
                if (previous["id"] == transfer_id and previous["digest"] == snapshot.digest
                        and current.generation == snapshot.source.generation
                        and current.state_digest == snapshot.source.state_digest):
                    return current
                raise StoreError("This destination already received another local transfer or changed afterward.")
            states = self._check_local_target(metadata, snapshot)
            self._replace_local_states(states, snapshot.receipts)
            source = snapshot.source
            metadata.update(generation=source.generation, operationId=source.operation_id,
                            authorityEpoch=source.authority_epoch, empty=False, transitionId=transfer_id,
                            transfer={"id": transfer_id, "digest": snapshot.digest, "source": evidence_record(source)})
            result = self._current_evidence(metadata)
        return result

    def _replace_local_states(self, states, receipts):
        for table in ("transaction_receipts", "transactions", "accounts", "categories", "budget", "future_funding", "rollover_modes", "items", "workspace_imports"):
            self.db.execute(f"DELETE FROM {table}")
        for workspace, state in states.items():
            self._write_state(state)
            for identifier, image in state["receipts"].items():
                self.db.execute("INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES(?,?,?)", (workspace, identifier, image))
            imported = state["importReceipt"]
            if imported is not None:
                self.db.execute("INSERT INTO workspace_imports(workspace,plugin_id,fingerprint,account_count,category_count,transaction_count,assignment_count) VALUES(?,?,?,?,?,?,?)",
                                (workspace, imported["pluginId"], imported["fingerprint"], *(imported["counts"][name] for name in ("accounts", "categories", "transactions", "assignments"))))
        self.db.execute("DELETE FROM storage_operations")
        for receipt in receipts:
            self.db.execute("INSERT INTO storage_operations(operation_id,generation,record) VALUES(?,?,?)",
                            (receipt.operation_id, receipt.evidence.generation, canonical_storage_json(receipt_record(receipt))))

    def replica_watermark(self):
        with self._snapshot():
            self._check_authority()
            record = self._authority_metadata()["replica"]
            return None if record is None else read_replica_watermark(record)

    def apply_replica(self, replica_id, snapshot, namespace_digest, expected, *, namespace_generation):
        with self._authority_write(financial=False) as transaction:
            metadata = transaction["metadata"]
            current = self._current_evidence(metadata)
            record = metadata["replica"]
            watermark, replayed = validate_replica_apply(
                current, None if record is None else read_replica_watermark(record), replica_id,
                snapshot, namespace_digest, expected, namespace_generation=namespace_generation,
                empty=metadata["empty"] and self._pristine(), promotion_id=metadata["promotionId"],
            )
            if replayed:
                return watermark
            self._replace_local_states(decode_local_state(snapshot.financial, snapshot.protected), snapshot.receipts)
            metadata.update(generation=snapshot.source.generation, operationId=snapshot.source.operation_id,
                            authorityEpoch=snapshot.source.authority_epoch, empty=False,
                            replica=replica_record(watermark))
        return watermark

    def prepare_promotion(self, transition_id, expected, watermark):
        storage_identifier(transition_id)
        with self._authority_write(financial=False) as transaction:
            metadata = transaction["metadata"]
            current = self._current_evidence(metadata)
            record = metadata["replica"]
            if (type(expected) is not CommitEvidence or current != expected or current.role != "copy"
                    or type(watermark) is not ReplicaWatermark or record is None
                    or read_replica_watermark(record) != watermark
                    or metadata["promotionId"] not in {None, transition_id}):
                raise StoreError("Copy changed before its promotion preparation.")
            metadata.update(transitionId=transition_id, promotionId=transition_id)
        return current

    def abort_promotion(self, transition_id, expected):
        storage_identifier(transition_id)
        with self._authority_write(financial=False) as transaction:
            metadata = transaction["metadata"]
            current = self._current_evidence(metadata)
            if (type(expected) is not CommitEvidence or current != expected or current.role != "copy"
                    or metadata["promotionId"] not in {None, transition_id}):
                raise StoreError("Copy promotion does not match this abort request.")
            if metadata["promotionId"] is None:
                return current
            metadata.update(transitionId=None, promotionId=None)
        return current
