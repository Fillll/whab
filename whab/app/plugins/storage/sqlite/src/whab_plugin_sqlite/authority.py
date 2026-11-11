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
    operation_limit,
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

    def _local_states(self, selected=None):
        states = {name: self._read_state(name, transaction_ids=selected[1] if selected is not None and selected[0] == name else None,
                                         include_history=selected is None or selected[0] != name)
                  for name in ("personal", "demo")}
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
        stamp = (self.db.total_changes, self.db.execute("PRAGMA data_version").fetchone()[0])
        if getattr(self, '_evidence_stamp', None) != stamp:
            from .evidence import SnapshotEncoder
            transaction = self._writing
            selected = transaction.get('selected') if transaction is not None else None
            reusable = (selected is not None and selected[0] in transaction.get('history_after', {})
                        and getattr(self, '_native_state_stamp', None) == transaction['before_stamp'])
            states = self._local_states(selected if reusable else None)
            if reusable:
                name, identifiers = selected
                states[name]['transactions'] = {**self._native_states[name]['transactions'], **states[name]['transactions']}
                # The journal was assembled from immutable snapshots in this transaction.
                states[name].update({key: value for key, value in transaction['history_after'][name].items() if key == 'history'})
            if not reusable or not hasattr(self, '_snapshot_encoder'):
                self._snapshot_encoder = SnapshotEncoder()
            self._snapshot_encoder.begin(transaction_ids=selected[1] if reusable else None)
            financial, protected = encode_local_state(states, validate=False, encoder=self._snapshot_encoder.encode)
            self._snapshot_encoder.finish()
            self._native_states = states
            self._native_state_stamp = stamp
            self._evidence_digest = local_state_digest(financial, protected)
            self._evidence_stamp = stamp
        return self._metadata_evidence(metadata or self._authority_metadata(), self._evidence_digest)

    @contextmanager
    def _authority_write(self, *, financial=True, workspace=None, transaction_ids=None):
        if self._read_only:
            raise StoreError("Read-only storage cannot accept mutations.")
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
                if workspace in {'personal', 'demo'} and transaction_ids is not None and not self.db.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' LIMIT 1").fetchone():
                    self._writing['selected'] = (workspace, tuple(transaction_ids))
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
                from .export_reads import snapshot
                stamp = (self.db.total_changes, self.db.execute("PRAGMA data_version").fetchone()[0])
                transaction['before_stamp'] = stamp
                cached = getattr(self, '_history_heads', {}) if getattr(self, '_history_stamp', None) == stamp else {}
                names = (workspace,) if type(workspace) is str and workspace in {'personal', 'demo'} else ('personal', 'demo')
                history_before = {name: cached[name] if name in cached else snapshot(self, name) for name in names if self.db.execute("SELECT 1 FROM meta WHERE workspace=?", (name,)).fetchone()} if financial else {}
                history_after = dict(cached) if financial else {}
                selected = (tuple(transaction_ids) if type(transaction_ids) in {tuple, list} and 0 <= len(transaction_ids) <= 100 and all(type(key) is str for key in transaction_ids) else None)
                # User/legacy SQL triggers can touch additional rows; do not use
                # bounded reads in their presence. Data-version/total-change
                # checks invalidate cached heads after writes outside this scope.
                if self.db.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' LIMIT 1").fetchone():
                    selected = None
                yield transaction
                if transaction["dirty"]:
                    if transaction.get('selected') is not None:
                        workspace, selected = transaction['selected']
                    from whab.domain.history import record_financial
                    operation = transaction["operation"]
                    info = getattr(self, '_history_context', None)
                    if info is None:
                        info = {'actor': 'System', 'actorKind': 'system', 'operation': operation.name if operation is not None else transaction.get('history_operation', 'change')}
                    info = {**info, **transaction.get('history_action', {})}
                    for name, old in history_before.items():
                        if name != (operation.workspace if operation is not None else transaction.get("workspace", name)):
                            continue
                        if selected is not None:
                            new = snapshot(self, name, transaction_ids=selected, include_history=False)
                            changed = {row['id']: row for row in new['transactions']}
                            new['transactions'] = [changed.get(row['id'], row) for row in old['transactions']]
                            if 'history' in old:
                                new['history'] = old['history']
                        else:
                            new = snapshot(self, name)
                        if not transaction.get("history_restore"):
                            original_journal = new.get("history") or old.get("history")
                            from hashlib import sha256

                            from .evidence import SnapshotEncoder
                            if not hasattr(self, '_history_encoder') or name not in cached:
                                self._history_encoder = SnapshotEncoder(ensure_ascii=True)
                            self._history_encoder.begin(transaction_ids=selected if name in cached else None, scope=name)
                            previous_digest = getattr(self, '_history_digest_heads', {}).get(name) if name in cached else None
                            def digest_snapshot(value):
                                from whab.plugins.storage_transfer import MAX_LOCAL_TRANSFER_BYTES
                                if previous_digest is not None and value.keys() == previous_digest[0].keys() and all(
                                    value[key] is previous_digest[0][key] if type(value[key]) in {list, dict} else value[key] == previous_digest[0][key]
                                    for key in value
                                ):
                                    return previous_digest[1]
                                result = sha256(self._history_encoder.encode(value, limit=MAX_LOCAL_TRANSFER_BYTES)).hexdigest()
                                transaction.setdefault('history_digests', {})[name] = (value, result)
                                return result
                            bounded_changes = None
                            if selected is not None:
                                from whab.domain.history import difference
                                selected_ids = set(selected)
                                old_rows = [row for row in old['transactions'] if row['id'] in selected_ids] if selected_ids else []
                                bounded_changes = difference({**old, 'transactions': old_rows}, {**new, 'transactions': list(changed.values())})
                            journal = record_financial(original_journal, old, new, info, digest_snapshot=digest_snapshot, changes=bounded_changes)
                            self._history_encoder.finish()
                            if journal is not None:
                                new["history"] = journal
                                if journal is not original_journal:
                                    from .history_writes import save_history
                                    save_history(self, name, journal, original_journal, cache_valid=name in cached and selected is not None and info['operation'] in {'edit_transaction', 'edit_transaction_details', 'update_account'})
                        history_after[name] = new
                    transaction['history_after'] = history_after
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
                next_history_stamp = (self.db.total_changes, self.db.execute("PRAGMA data_version").fetchone()[0])
                digest_current = getattr(self, '_evidence_stamp', None) == (self.db.total_changes, self.db.execute("PRAGMA data_version").fetchone()[0])
                # Authority metadata and receipts are outside the logical state digest.
                if transaction["dirty"] and transaction["receipt"] is not None and not self.db.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' LIMIT 1").fetchone():
                    digest_current = True
                self.db.execute("COMMIT")
                if digest_current:
                    self._evidence_stamp = (self.db.total_changes, self.db.execute("PRAGMA data_version").fetchone()[0])
                    self._native_state_stamp = self._evidence_stamp
                self._history_heads = history_after
                self._history_stamp = next_history_stamp
                if not hasattr(self, '_history_digest_heads'):
                    self._history_digest_heads = {}
                self._history_digest_heads.update(transaction.get('history_digests', {}))
            except BaseException as error:
                self._evidence_stamp = None
                self._native_state_stamp = None
                self._history_blob_positions = {}
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
            raise StoreError('Durable commands require a validated storage operation.')
        if operation.name == 'recorded_command':
            value = read_storage_json(operation.payload, limit=operation_limit(operation.name))
            with self.history_scope(value['context']):
                return self._execute_operation(operation)
        return self._execute_operation(operation)

    def _execute_operation(self, operation):
        payload = read_storage_json(operation.payload, limit=operation_limit(operation.name))
        name = operation.name
        if name == 'recorded_command':
            name, payload = payload['operation'], payload['parameters']
        selected = ([payload.get('transactionId')] if name in {'edit_transaction', 'edit_transaction_details'} else payload.get('transactionIds') if name == 'review_transactions' else None)
        with self._authority_write(workspace=operation.workspace, transaction_ids=selected) as transaction:
            current = self._current_evidence(transaction["metadata"])
            replay = check_storage_operation(current, operation, self.operation_receipt(operation.operation_id))
            if replay is not None:
                return replay
            transaction["operation"] = operation
            if name == "history_apply":
                transaction["history_action"] = {"operation": "history", "action": payload["action"], "target": payload.get("target")}
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
