"""An instructional SQLite snapshot adapter, independent of bundled backends.

Financial rules come from the public pure model. This module teaches the native
transaction boundary: a detached candidate, generation, operation receipt and
encrypted provider checkpoint are committed together. It intentionally provides
no same-device transfer/copy implementation and makes no such declaration.
"""

import base64
import hashlib
import os
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from urllib.parse import quote
from uuid import uuid4

from whab.plugins import (
    UNSET,
    CommitEvidence,
    OperationReceipt,
    OperationResult,
    StorageOperation,
    StoreError,
    receipt_media_type,
    validate_review_batch,
)
from whab.plugins.storage_model import (
    bank_identifier,
    calculate_workspace,
    calculate_workspace_periods,
    commands,
    create_category,
    current_month,
    initial_state,
    merge_sync,
    portable_snapshot,
    public_categories,
    require_pristine,
    restore_snapshot,
    transaction_rows,
    validate_label,
    validate_month,
    validate_workspace_import,
)
from whab.plugins.storage_transfer import (
    MAX_LOCAL_TRANSFER_BYTES,
    MAX_OPERATION_RECEIPTS,
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
    storage_identifier,
    validate_role_transition,
)

from . import PLUGIN_ID

_LIMIT = MAX_LOCAL_TRANSFER_BYTES * 2
_FILE = f"{PLUGIN_ID}-ledger.sqlite3"
_DIRECTORY_SYNC = os.name == "posix" and hasattr(os, "O_DIRECTORY")


def _invalid():
    raise StoreError(
        "The teaching storage file is invalid or unavailable; preserve it before recovery."
    )


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(text):
    if type(text) is not str or len(text) > _LIMIT:
        _invalid()
    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError, UnicodeError:
        _invalid()
    if _b64(raw) != text:
        _invalid()
    return raw


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_directory(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir() or current.is_symlink():
        _invalid()
    for current in reversed(missing):
        current.mkdir(mode=0o700)
    # Retry after an interrupted mkdir must also publish ancestors which now
    # exist in the kernel cache but have never reached a successful sync.
    for current in (path, *path.parents):
        _sync_directory(current)


class SnapshotStorage:
    """One owned writer, independently readable candidates, bounded native state.

    An auxiliary SQLite lease contains no financial data. Its exclusive native
    transaction lives for the writer's lifetime; its inode is bound in the ledger
    so deleting/replacing it cannot silently create a competing writer. Read-only
    readiness opens neither a lease nor a writable/schema-upgrading connection.
    """

    def __init__(self, context, *, month=None, boundary=None):
        self.context = context
        self._lock, self._connection, self._lease = RLock(), None, None
        self._closed, self._uncertain, self._pending = False, False, None
        self._known, self._identity, self._lease_identity = None, None, None
        self._immutable_identity = None
        self._boundary = boundary or (lambda _: None)
        self._scope = (
            context.budget.budget_id if context.budget else "default",
            context.environment,
        )
        self._read_only = context.read_only
        try:
            if not _DIRECTORY_SYNC:
                raise StoreError(
                    "This teaching adapter requires POSIX directory synchronization; choose another backend on this platform."
                )
            self._home = Path(context.home).absolute()
            if self._home.is_symlink():
                _invalid()
            self.path = self._home.resolve() / "data" / f"{context.environment}-{_FILE}"
            self._check_paths()
            exists = self.path.exists()
            if (context.read_only or context.expected_instance_id is not None) and not exists:
                _invalid()
            if exists:
                self._check_native_format()
            if exists and not context.read_only:
                # Validate complete existing native schema/scope/identity before
                # any writable SQLite open or synchronization sidecar creation.
                reader = SnapshotStorage(replace(context, read_only=True))
                reader.close()
            lease_path = self.path.with_suffix(".writer")
            if not context.read_only:
                if exists and not lease_path.is_file():
                    _invalid()
                _durable_directory(self.path.parent)
                if not lease_path.exists():
                    descriptor = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    _sync_directory(lease_path.parent)
                self._lease = sqlite3.connect(
                    lease_path, timeout=0, isolation_level=None, check_same_thread=False
                )
                self._lease.execute("BEGIN EXCLUSIVE")
                self._lease_identity = self._file_identity(lease_path)
                if not exists:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    _sync_directory(self.path.parent)
                    self._boundary("created-native")
            mode = "?mode=ro" if context.read_only else "?mode=rwc"
            if context.read_only:
                wal = self.path.with_name(self.path.name + "-wal")
                shm = self.path.with_name(self.path.name + "-shm")
                if wal.exists():
                    if not shm.is_file() or shm.is_symlink():
                        _invalid()
                else:
                    # A cleanly checkpointed WAL database needs no sidecars.
                    # Immutable RO avoids creating them merely to inspect it.
                    mode += "&immutable=1"
                    self._immutable_identity = self._file_information(self.path)
            uri = "file:" + quote(str(self.path.absolute()), safe="/") + mode
            self._connection = sqlite3.connect(
                uri, uri=True, timeout=0, isolation_level=None, check_same_thread=False
            )
            self._connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _LIMIT)
            (version,) = self._connection.execute("PRAGMA user_version").fetchone()
            if exists and version != 1:
                _invalid()
            if not context.read_only:
                self._connection.execute("PRAGMA synchronous=FULL")
                self._connection.execute("PRAGMA journal_mode=WAL")
            if not exists:
                selected_month = month or current_month()
                validate_month(selected_month)
                states = {
                    name: initial_state(name, selected_month) for name in ("personal", "demo")
                }
                document = {
                    "formatVersion": 1,
                    "evidence": None,
                    "financial": None,
                    "protected": None,
                    "transition": None,
                    "operations": [],
                    "writerIdentity": list(self._lease_identity),
                }
                financial, protected = encode_local_state(states)
                evidence = CommitEvidence(
                    *self._scope,
                    str(uuid4()),
                    str(uuid4()),
                    0,
                    None,
                    context.initial_role,
                    local_state_digest(financial, protected),
                )
                document.update(
                    evidence=evidence_record(evidence),
                    financial=_b64(financial),
                    protected=_b64(protected),
                )
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "CREATE TABLE ledger (slot INTEGER PRIMARY KEY CHECK(slot=1), document BLOB NOT NULL)"
                )
                self._connection.execute("PRAGMA user_version=1")
                self._connection.execute(
                    "INSERT INTO ledger VALUES (1, ?)", (self._serialize(document),)
                )
                self._connection.execute("COMMIT")
                self._connection.execute("PRAGMA wal_checkpoint(FULL)")
                _sync_directory(self.path.parent)
            self._identity = self._file_identity(self.path)
            raw, document, states = self._load()
            if (
                context.expected_instance_id is not None
                and read_evidence(document["evidence"]).instance_id != context.expected_instance_id
            ):
                _invalid()
            if not context.read_only and tuple(document["writerIdentity"]) != self._lease_identity:
                _invalid()
            self._known = hashlib.sha256(raw).digest()
        except OSError, sqlite3.Error:
            self.close()
            raise StoreError(
                "The teaching storage could not acquire or validate its native file; preserve existing data."
            ) from None
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _file_identity(path):
        value = path.stat()
        return value.st_dev, value.st_ino

    @staticmethod
    def _file_information(path):
        value = path.stat()
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

    def _check_immutable(self):
        if self._immutable_identity is not None and (
            self._file_information(self.path) != self._immutable_identity
            or self.path.with_name(self.path.name + "-wal").exists()
        ):
            self._uncertain = True
            raise StoreError(
                "The immutable readiness snapshot changed; prepare a new read-only candidate."
            )

    def _check_paths(self):
        for path in (
            self._home,
            self.path,
            self.path.with_suffix(".writer"),
            self.path.with_name(self.path.name + "-wal"),
            self.path.with_name(self.path.name + "-shm"),
            *self.path.parents,
        ):
            if path.is_symlink():
                _invalid()

    def _check_native_format(self):
        # SQLite's documented file header: https://sqlite.org/fileformat.html
        # Reject unknown schemas/modes before SQLite can adopt a schema or
        # materialize WAL/shared-memory sidecars during an otherwise RO probe.
        with self.path.open("rb") as stream:
            header = stream.read(100)
        if (
            len(header) != 100
            or header[:16] != b"SQLite format 3\x00"
            or header[18:20] != b"\x02\x02"
            or int.from_bytes(header[60:64], "big") != 1
        ):
            _invalid()
        if self._connection is not None and self._connection.execute(
            "PRAGMA user_version"
        ).fetchone() != (1,):
            _invalid()

    def _check_ownership(self):
        try:
            self._check_paths()
        except StoreError:
            self._uncertain = True
            raise
        if (
            self._identity is not None
            and self._file_identity(self.path) != self._identity
            or self._lease is not None
            and self._file_identity(self.path.with_suffix(".writer")) != self._lease_identity
        ):
            self._uncertain = True
            _invalid()

    @staticmethod
    def _serialize(document):
        return canonical_storage_json(document, limit=_LIMIT)

    def _load(self):
        try:
            return self._load_native()
        except StoreError:
            self._uncertain = True
            raise
        except OSError, sqlite3.Error:
            self._uncertain = True
            raise StoreError(
                "The teaching storage could not read its native evidence; preserve the runtime."
            ) from None

    def _load_native(self):
        if self._closed or self._uncertain:
            raise StoreError(
                "The teaching storage outcome requires close and explicit native-evidence recovery."
            )
        self._check_ownership()
        self._check_immutable()
        self._check_native_format()
        rows = self._connection.execute("SELECT slot, document FROM ledger").fetchall()
        if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not bytes:
            _invalid()
        raw = rows[0][1]
        if self._known is not None and hashlib.sha256(raw).digest() != self._known:
            self._uncertain = True
            _invalid()
        document = read_storage_json(raw, limit=_LIMIT)
        if (
            type(document) is not dict
            or document.keys()
            != {
                "formatVersion",
                "evidence",
                "financial",
                "protected",
                "transition",
                "operations",
                "writerIdentity",
            }
            or type(document["formatVersion"]) is not int
            or document["formatVersion"] != 1
        ):
            _invalid()
        evidence = read_evidence(document["evidence"])
        if (evidence.generation == 0) != (evidence.operation_id is None):
            _invalid()
        financial, protected = _unb64(document["financial"]), _unb64(document["protected"])
        states = decode_local_state(financial, protected)
        if (
            evidence.budget_id,
            evidence.environment,
        ) != self._scope or evidence.state_digest != local_state_digest(financial, protected):
            _invalid()
        if document["transition"] is not None:
            storage_identifier(document["transition"])
        identity = document["writerIdentity"]
        if (
            type(identity) is not list
            or len(identity) != 2
            or any(type(value) is not int or value < 0 for value in identity)
        ):
            _invalid()
        if self._file_identity(self.path.with_suffix(".writer")) != tuple(identity):
            _invalid()
        if (
            type(document["operations"]) is not list
            or len(document["operations"]) > MAX_OPERATION_RECEIPTS
        ):
            _invalid()
        seen = set()
        for record in document["operations"]:
            receipt = read_operation_receipt(record)
            if (
                receipt.operation_id in seen
                or (receipt.evidence.budget_id, receipt.evidence.environment) != self._scope
                or receipt.evidence.instance_id != evidence.instance_id
                or receipt.evidence.authority_epoch != evidence.authority_epoch
                or receipt.evidence.role != "primary"
                or receipt.evidence.generation == 0
                or receipt.evidence.generation > evidence.generation
                or (
                    receipt.evidence.generation == evidence.generation
                    and (
                        receipt.evidence.state_digest != evidence.state_digest
                        or receipt.operation_id != evidence.operation_id
                    )
                )
            ):
                _invalid()
            seen.add(receipt.operation_id)
        self._check_immutable()
        return raw, document, states

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if self._lease is not None:
                self._lease.close()
                self._lease = None
            self._closed = True

    @staticmethod
    def _workspace(workspace):
        if type(workspace) is not str or workspace not in {"personal", "demo"}:
            raise StoreError("Choose a supported workspace.")
        return workspace

    def _read(self, workspace):
        with self._lock:
            self._workspace(workspace)
            return self._load()[2][workspace]

    def _write(self, document, states):
        financial, protected = encode_local_state(states)
        evidence = replace(
            read_evidence(document["evidence"]),
            state_digest=local_state_digest(financial, protected),
        )
        document.update(
            evidence=evidence_record(evidence), financial=_b64(financial), protected=_b64(protected)
        )
        return document

    def _transaction(self, operation, *, native_operation=None, control=False):
        with self._lock:
            if self._read_only:
                raise StoreError("Read-only readiness cannot mutate storage.")
            if self._closed or self._uncertain:
                raise StoreError(
                    "Close and explicitly recover the teaching storage before another mutation."
                )
            try:
                self._connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error:
                self._uncertain = True
                raise StoreError(
                    "The teaching storage could not acquire its native mutation boundary."
                ) from None
            committing = False
            try:
                _, document, states = self._load()
                before = read_evidence(document["evidence"])
                if not control:
                    require_primary(before)
                if native_operation is not None:
                    previous = next(
                        (
                            read_operation_receipt(row)
                            for row in document["operations"]
                            if row["operation_id"] == native_operation.operation_id
                        ),
                        None,
                    )
                    replay = check_storage_operation(before, native_operation, previous)
                    if replay is not None:
                        self._connection.execute("ROLLBACK")
                        return replay
                self._pending = states
                result = operation(document, states)
                self._pending = None
                if not control:
                    identifier = native_operation.operation_id if native_operation else str(uuid4())
                    document["evidence"] = evidence_record(
                        replace(before, generation=before.generation + 1, operation_id=identifier)
                    )
                self._write(document, states)
                if native_operation is not None:
                    receipt = OperationReceipt(
                        native_operation.operation_id,
                        native_operation.fingerprint,
                        canonical_storage_json(result),
                        read_evidence(document["evidence"]),
                    )
                    document["operations"].append(receipt_record(receipt))
                    del document["operations"][:-MAX_OPERATION_RECEIPTS]
                    result = OperationResult(receipt)
                raw = self._serialize(document)
                self._connection.execute("UPDATE ledger SET document=? WHERE slot=1", (raw,))
                self._boundary("before-commit")
                self._check_ownership()
                committing = True
                self._connection.execute("COMMIT")
                self._boundary("after-commit")
                self._check_ownership()
                self._check_native_format()
                if self._connection.execute(
                    "SELECT document FROM ledger WHERE slot=1"
                ).fetchone() != (raw,):
                    self._uncertain = True
                    _invalid()
                self._known = hashlib.sha256(raw).digest()
                return result
            except BaseException as error:
                if committing:
                    self._uncertain = True
                elif self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                if isinstance(error, (OSError, sqlite3.Error)):
                    raise StoreError(
                        "The teaching storage mutation failed; preserve its native evidence before recovery."
                    ) from None
                raise
            finally:
                self._pending = None

    def _change(self, workspace, operation):
        with self._lock:
            self._workspace(workspace)
            if self._pending is not None:
                return operation(self._pending[workspace])
            return self._transaction(lambda _, states: operation(states[workspace]))

    def accounts(self, workspace):
        return list(self._read(workspace)["accounts"].values())

    def categories(self, workspace):
        return public_categories(self._read(workspace)["categories"])

    def transactions(self, workspace, month=None):
        if month is not None:
            validate_month(month)
        return transaction_rows(self._read(workspace), month)

    def workspace(self, workspace, month=None):
        return calculate_workspace(self._read(workspace), month or current_month())

    def workspace_periods(self, workspace, month, count):
        return calculate_workspace_periods(self._read(workspace), month, count)

    def add_account(self, workspace, account):
        return self._change(
            workspace, lambda state: commands.add_account(state, str(uuid4()), account)
        )

    def update_account(self, workspace, account_id, changes):
        return self._change(
            workspace, lambda state: commands.update_account(state, account_id, changes)
        )

    def add_category(self, workspace, category):
        identifier = str(uuid4())

        def apply(state):
            if type(category) is not dict or category.keys() - {
                "name",
                "group",
                "target",
                "parentId",
                "currency",
            }:
                raise StoreError("Categories support exactly two levels.")
            state["categories"][identifier] = create_category(
                identifier,
                category["name"],
                category.get("group"),
                category.get("target", 0),
                categories=state["categories"],
                parent_id=category.get("parentId"),
                currency=category.get("currency", "USD"),
            )
            return identifier

        return self._change(workspace, apply)

    def add_transaction(self, workspace, transaction, source="manual"):
        return self._change(
            workspace,
            lambda state: commands.add_transaction(
                state, transaction.get("id") or str(uuid4()), transaction, source
            ),
        )

    def edit_transaction(
        self, workspace, transaction_id, category_id=UNSET, reviewed=None, note=UNSET
    ):
        return self._change(
            workspace,
            lambda state: commands.edit_transaction(
                state, transaction_id, category_id, reviewed, note
            ),
        )

    def review_transactions(self, workspace, transaction_ids, reviewed):
        identifiers = validate_review_batch(transaction_ids, reviewed)

        def apply(state):
            for identifier in identifiers:
                commands.edit_transaction(state, identifier, reviewed=reviewed)

        return self._change(workspace, apply)

    def assign(self, workspace, category_id, month, assigned):
        return self._change(
            workspace,
            lambda state: commands.assign(
                state, category_id, month, assigned, as_of_month=current_month()
            ),
        )

    def assign_next_month(self, workspace, month, currency, assigned):
        return self._change(
            workspace,
            lambda state: commands.assign_next_month(
                state, month, currency, assigned, as_of_month=current_month()
            ),
        )

    def set_rollover_mode(self, workspace, month, currency, mode):
        return self._change(
            workspace,
            lambda state: commands.set_rollover_mode(
                state, month, currency, mode, as_of_month=current_month()
            ),
        )

    def items(self):
        return [
            {key: item[key] for key in ("id", "institutionName", "status", "lastSynced", "error")}
            for item in self._read("personal")["items"].values()
            if item["token"] is not None
        ]

    def get_item(self, item_id):
        return self._read("personal")["items"].get(item_id)

    def save_item(self, item_id, encrypted_token, institution_name, *, provider_id="plaid"):
        bank_identifier(item_id, limit=4096)
        if (
            type(provider_id) is not str
            or len(provider_id) > 64
            or provider_id in {"manual", "demo"}
            or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", provider_id) is None
        ):
            raise StoreError("Choose a valid bank provider identity.")
        if type(encrypted_token) is not str or not encrypted_token:
            raise StoreError("Provider persistence requires a host-encrypted token record.")

        def apply(state):
            previous = state["items"].get(item_id, {})
            if previous and previous["provider"] != provider_id:
                raise StoreError("The connection belongs to another provider.")
            state["items"][item_id] = {
                "id": item_id,
                "token": encrypted_token,
                "institutionName": validate_label(institution_name),
                "status": "connected",
                "lastSynced": previous.get("lastSynced"),
                "error": None,
                "cursor": previous.get("cursor"),
                "provider": provider_id,
            }

        return self._change("personal", apply)

    def set_item_status(self, item_id, status, error):
        def apply(state):
            item = state["items"].get(item_id)
            if item is not None and item["token"] is not None:
                item.update(status=status, error=error)

        return self._change("personal", apply)

    def disconnect_item(self, item_id):
        def apply(state):
            item = state["items"].get(item_id)
            if item is None or item["token"] is None:
                raise StoreError("Connection not found.")
            item.update(token=None, cursor=None, status="disconnected", error=None)
            for account in state["accounts"].values():
                if account.get("itemId") == item_id:
                    account["stale"] = True

        return self._change("personal", apply)

    def apply_sync(
        self,
        item_id,
        changes,
        cursor,
        accounts=None,
        historical_complete=True,
        *,
        expected_cursor=UNSET,
    ):
        if type(historical_complete) is not bool:
            raise StoreError("Historical completion requires a boolean.")
        return self._change(
            "personal",
            lambda state: merge_sync(
                state,
                item_id,
                changes,
                cursor,
                [] if accounts is None else accounts,
                expected_cursor=expected_cursor,
                synced_at=datetime.now(timezone.utc).isoformat(),
            ),
        )

    def receipt_ids(self, workspace):
        state = self._read(workspace)
        return sorted(
            identifier
            for identifier in state["receipts"]
            if not state["transactions"][identifier]["deleted"]
        )

    @staticmethod
    def _receipt_target(state, transaction_id):
        transaction = state["transactions"].get(transaction_id)
        if transaction is None or transaction["deleted"]:
            raise StoreError("Receipt transaction not found.")

    def get_receipt(self, workspace, transaction_id):
        state = self._read(workspace)
        self._receipt_target(state, transaction_id)
        return state["receipts"].get(transaction_id)

    def save_receipt(self, workspace, transaction_id, raw):
        receipt_media_type(raw)

        def apply(state):
            self._receipt_target(state, transaction_id)
            state["receipts"][transaction_id] = raw

        return self._change(workspace, apply)

    def delete_receipt(self, workspace, transaction_id):
        def apply(state):
            self._receipt_target(state, transaction_id)
            state["receipts"].pop(transaction_id, None)

        return self._change(workspace, apply)

    def export_budget(self, workspace="personal"):
        return portable_snapshot(self._read(workspace))

    def restore_budget(self, workspace, data):
        def apply(state):
            if workspace != "personal":
                raise StoreError("Neutral restoration requires the personal workspace.")
            require_pristine(state)
            restore_snapshot(state, data)

        return self._change(workspace, apply)

    def export_budget_files(self, workspace="personal"):
        state = self._read(workspace)
        return {
            "snapshot-budget.json": canonical_storage_json(
                {
                    "formatVersion": 2,
                    "budget": portable_snapshot(state),
                    "receipts": {key: _b64(raw) for key, raw in state["receipts"].items()},
                    "importReceipt": state["importReceipt"],
                },
                limit=MAX_LOCAL_TRANSFER_BYTES,
            )
        }

    def _file_candidate(self, files):
        if type(files) is not dict or files.keys() != {"snapshot-budget.json"}:
            raise StoreError("Supply the teaching adapter's declared neutral payload.")
        value = read_storage_json(files["snapshot-budget.json"], limit=MAX_LOCAL_TRANSFER_BYTES)
        if type(value) is not dict or type(value.get("formatVersion")) is not int:
            _invalid()
        version = value["formatVersion"]
        if (
            version not in {1, 2}
            or value.keys()
            != (
                {"formatVersion", "budget", "receipts"}
                if version == 1
                else {"formatVersion", "budget", "receipts", "importReceipt"}
            )
            or type(value["receipts"]) is not dict
        ):
            _invalid()
        state = initial_state("personal", current_month())
        restore_snapshot(state, value["budget"])
        state["importReceipt"] = value["importReceipt"] if version == 2 else None
        for identifier, encoded in value["receipts"].items():
            raw = _unb64(encoded)
            receipt_media_type(raw)
            if identifier not in state["transactions"]:
                _invalid()
            state["receipts"][identifier] = raw
        # Reuse the public complete-state validator for non-secret import
        # identity/counts, including when an existing native cache wins below.
        encode_local_state(
            {"personal": state, "demo": initial_state("demo", state["budgetStartMonth"])}
        )
        return state

    def prepare_budget_files(self, workspace, files, *, existing=False):
        self._workspace(workspace)
        if workspace != "personal" or type(existing) is not bool:
            raise StoreError(
                "Neutral preparation requires the personal workspace and explicit existing flag."
            )
        candidate = self._file_candidate(files)

        def apply(state):
            if state == candidate:
                return
            if existing:
                try:
                    require_pristine(state)
                except StoreError:
                    return
            else:
                require_pristine(state)
            state.clear()
            state.update(candidate)

        return self._change(workspace, apply)

    def restore_budget_files(self, workspace, files):
        candidate = self._file_candidate(files)

        def apply(state):
            if workspace != "personal":
                raise StoreError("Neutral restoration requires the personal workspace.")
            require_pristine(state)
            state.clear()
            state.update(candidate)

        return self._change(workspace, apply)

    def preview_workspace_import(self, workspace, plugin_id, fingerprint):
        try:
            if workspace != "personal":
                raise StoreError("Imports require the personal workspace.")
            storage_identifier(plugin_id, limit=64)
            storage_identifier(fingerprint)
            state = self._read(workspace)
            previous = state["importReceipt"]
            if previous is not None:
                if (previous["pluginId"], previous["fingerprint"]) != (plugin_id, fingerprint):
                    raise StoreError("This workspace already received a different import.")
            else:
                require_pristine(state)
            return {"canImport": True, "reason": "", "alreadyImported": previous is not None}
        except StoreError:
            return {
                "canImport": False,
                "reason": "The workspace cannot accept this import.",
                "alreadyImported": False,
            }

    def import_workspace(self, workspace, plugin_id, fingerprint, data):
        imported = validate_workspace_import(data)
        preview = self.preview_workspace_import(workspace, plugin_id, fingerprint)
        if not preview["canImport"]:
            raise StoreError("The workspace cannot accept this import.")

        def apply(state):
            previous = state["importReceipt"]
            if previous is not None:
                if (previous["pluginId"], previous["fingerprint"]) != (plugin_id, fingerprint):
                    raise StoreError("This workspace already received a different import.")
                return {"counts": previous["counts"].copy(), "alreadyImported": True}
            require_pristine(state)
            counts = {
                key: len(getattr(imported, key))
                for key in ("accounts", "categories", "transactions", "assignments")
            }
            restore_snapshot(
                state,
                {
                    "formatVersion": 1,
                    "budgetStartMonth": imported.budget_start_month,
                    "accounts": [
                        {**row, "currency": imported.currency} for row in imported.accounts
                    ],
                    "categories": [
                        {**row, "currency": imported.currency} for row in imported.categories
                    ],
                    "transactions": [
                        {**row, "merchantLogo": None} for row in imported.transactions
                    ],
                    "assignments": list(imported.assignments),
                },
                mark_external_stale=False,
            )
            state["importReceipt"] = {
                "pluginId": plugin_id,
                "fingerprint": fingerprint,
                "counts": counts,
            }
            return {"counts": counts.copy(), "alreadyImported": False}

        return self._change(workspace, apply)

    def commit_evidence(self):
        with self._lock:
            return read_evidence(self._load()[1]["evidence"])

    def operation_receipt(self, operation_id):
        storage_identifier(operation_id)
        with self._lock:
            return next(
                (
                    read_operation_receipt(row)
                    for row in self._load()[1]["operations"]
                    if row["operation_id"] == operation_id
                ),
                None,
            )

    def execute_operation(self, operation):
        if type(operation) is not StorageOperation:
            raise StoreError("Use a validated public storage operation.")
        return self._transaction(
            lambda *_: dispatch_storage_operation(self, operation), native_operation=operation
        )

    def set_role(self, transition_id, expected, role, *, authority_epoch=None):
        def apply(document, states):
            current = read_evidence(document["evidence"])
            epoch = validate_role_transition(
                current, transition_id, role, authority_epoch, document["transition"]
            )
            target = replace(expected, role=role, authority_epoch=epoch)
            if current != expected and (
                current != target or document["transition"] != transition_id
            ):
                raise StoreError(
                    "The teaching authority evidence changed before its role transition."
                )
            document.update(evidence=evidence_record(target), transition=transition_id)
            return target

        return self._transaction(apply, control=True)
