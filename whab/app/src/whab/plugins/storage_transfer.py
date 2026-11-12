"""Optional, versioned same-device storage authority and transfer contracts.

These records contain data, never executable callbacks or native database files.
Protected bodies are local-runtime material: they are not portable exports and
must never be included in diagnostics. Keys remain owned by runtime configuration.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport
from whab.domain.imports import bank_identifier
from whab.domain.ledger_state import initial_state, portable_snapshot, restore_snapshot
from whab.domain.money import MAX_SAFE_INTEGER
from whab.domain.portable import validate_portable_budget
from whab.domain.receipts import MAX_RECEIPT_BYTES
from whab.receipts import receipt_media_type

MAX_LOCAL_TRANSFER_BYTES = 128 * 1024 * 1024
MAX_OPERATION_BYTES = 32 * 1024 * 1024
MAX_BULK_OPERATION_BYTES = 72 * 1024 * 1024
MAX_OPERATION_RECEIPTS = 4096
STORAGE_ROLES = frozenset({"primary", "staged", "copy", "fenced", "retired"})
STORAGE_OPERATIONS = frozenset({
    "edit_transactions", "classify_accounts",
    "delete_category", "delete_category_group", "rename_category",
    "add_category_group", "move_category", "add_account", "update_account", "add_category", "add_transaction", "edit_transaction", "edit_transaction_details", "recorded_command", "history_apply",
    "review_transactions", "assign", "assign_next_month", "set_rollover_mode", "save_item",
    "set_item_status", "disconnect_item", "configure_bank_sync", "apply_sync", "import_workspace", "restore_budget",
    "save_receipt", "delete_receipt", "restore_budget_files", "prepare_budget_files",
})
_BULK_OPERATIONS = frozenset({"recorded_command", "restore_budget", "restore_budget_files", "prepare_budget_files", "import_workspace"})
_OPERATION_FIELDS = {
    "edit_transactions": ({"edits"}, set()),
    "classify_accounts": ({"edits"}, set()),
    "recorded_command": ({"operation", "parameters", "context"}, set()),
    "history_apply": ({"action", "target", "fingerprint"}, set()),
    "add_account": ({"name", "type", "balance"}, {"currency", "onBudget", "subtype", "closed"}),
    "update_account": ({"accountId"}, {"type", "onBudget", "subtype", "name", "bankWebsite", "currency", "closed"}),
    "add_category": ({"name", "target"}, {"group", "parentId", "currency"}),
    "add_category_group": ({"name"}, set()),
    "move_category": ({"categoryId", "parentId"}, set()),
    "rename_category": ({"categoryId", "name"}, set()),
    "delete_category": ({"categoryId"}, set()),
    "delete_category_group": ({"parentId"}, set()),
    "add_transaction": ({"accountId", "date", "merchant", "amount", "categoryId"}, {"note", "source", "id"}),
    "edit_transaction_details": ({"transactionId", "changes"}, set()),
    "edit_transaction": ({"transactionId"}, {"categoryId", "reviewed", "note"}),
    "review_transactions": ({"transactionIds", "reviewed"}, set()),
    "assign": ({"categoryId", "month", "assigned"}, set()),
    "assign_next_month": ({"month", "currency", "assigned"}, set()),
    "set_rollover_mode": ({"month", "currency", "mode"}, set()),
    "save_item": ({"itemId", "encryptedToken", "institutionName"}, {"providerId"}),
    "set_item_status": ({"itemId", "status", "error"}, set()),
    "disconnect_item": ({"itemId"}, set()),
    "configure_bank_sync": ({"itemId", "accounts", "selection"}, set()),
    "apply_sync": ({"itemId", "changes", "cursor"}, {"accounts", "historicalComplete", "expectedCursor"}),
    "import_workspace": ({"pluginId", "fingerprint", "data"}, set()),
    "restore_budget": ({"snapshot"}, set()),
    "save_receipt": ({"transactionId", "image"}, set()),
    "delete_receipt": ({"transactionId"}, set()),
    "restore_budget_files": ({"files"}, set()),
    "prepare_budget_files": ({"files", "existing"}, set()),
}


def _invalid():
    raise StoreError("Invalid or unsupported local storage transfer records.")


def operation_limit(name):
    # A supported 50 MiB portable payload can require almost 67 MiB after
    # base64 encoding. Ordinary individual commands retain their 32 MiB bound.
    return MAX_BULK_OPERATION_BYTES if name in _BULK_OPERATIONS else MAX_OPERATION_BYTES


def storage_identifier(value, *, limit=200):
    """Budget and operation identities are opaque text, not plugin-name syntax."""
    if type(value) is not str or not value or len(value) > limit or "\x00" in value:
        _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _invalid()
    return value


def _uuid(value):
    storage_identifier(value, limit=36)
    try:
        if str(UUID(value)) != value:
            _invalid()
    except ValueError:
        _invalid()


def _digest(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _invalid()


def _integer(value):
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _invalid()


def _plain(value, depth=0):
    if depth > 24 or depth == 24 and type(value) in {dict, list}:
        _invalid()
    if value is None or type(value) is bool:
        return
    if type(value) is int and -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        return
    if type(value) is str:
        if "\x00" in value:
            _invalid()
        try:
            value.encode("utf-8")
        except UnicodeError:
            _invalid()
        return
    if type(value) is list:
        for row in value:
            _plain(row, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for key, row in value.items():
            _plain(key, depth + 1)
            _plain(row, depth + 1)
        return
    _invalid()


def canonical_storage_json(value, *, limit=MAX_OPERATION_BYTES):
    """Bounded canonical JSON; rejects custom objects, floats and invalid Unicode."""
    _plain(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > limit:
        _invalid()
    return raw


def read_storage_json(raw, *, limit=MAX_OPERATION_BYTES):
    """Read plain bounded JSON with duplicate-field and nesting rejection."""
    if type(raw) is not bytes or not raw or len(raw) > limit:
        _invalid()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _invalid()
            result[key] = value
        return result

    def invalid_number(value):
        _invalid()

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_float=invalid_number, parse_constant=invalid_number)
    except (ValueError, UnicodeError, RecursionError):
        _invalid()
    _plain(value)
    return value


@dataclass(frozen=True, slots=True)
class CommitEvidence:
    budget_id: str
    environment: str
    instance_id: str
    authority_epoch: str
    generation: int
    operation_id: str | None
    role: str
    state_digest: str

    def __post_init__(self):
        storage_identifier(self.budget_id)
        if type(self.environment) is not str or self.environment not in {"sandbox", "production"}:
            _invalid()
        _uuid(self.instance_id)
        _uuid(self.authority_epoch)
        _integer(self.generation)
        if self.operation_id is not None:
            storage_identifier(self.operation_id)
        if type(self.role) is not str or self.role not in STORAGE_ROLES:
            _invalid()
        _digest(self.state_digest)


def evidence_record(evidence):
    if type(evidence) is not CommitEvidence:
        _invalid()
    return {name: getattr(evidence, name) for name in CommitEvidence.__slots__}


def read_evidence(value):
    if type(value) is not dict or value.keys() != set(CommitEvidence.__slots__):
        _invalid()
    return CommitEvidence(**value)


@dataclass(frozen=True, slots=True)
class StorageOperation:
    operation_id: str
    authority_epoch: str
    expected_generation: int
    workspace: str
    name: str
    payload: bytes = field(repr=False)

    def __post_init__(self):
        storage_identifier(self.operation_id)
        _uuid(self.authority_epoch)
        _integer(self.expected_generation)
        if type(self.workspace) is not str or self.workspace not in {"personal", "demo"}:
            _invalid()
        if type(self.name) is not str or self.name not in STORAGE_OPERATIONS:
            raise StoreError("Unsupported durable storage operation.")
        limit = operation_limit(self.name)
        value = read_storage_json(self.payload, limit=limit)
        if type(value) is not dict:
            _invalid()
        required, optional = _OPERATION_FIELDS[self.name]
        if not required <= value.keys() or not value.keys() <= required | optional:
            raise StoreError("Invalid durable storage operation fields.")
        if self.name in {"save_item", "set_item_status", "disconnect_item", "configure_bank_sync", "apply_sync"} and self.workspace != "personal":
            raise StoreError("Provider operations require the personal workspace.")
        object.__setattr__(self, "payload", canonical_storage_json(value, limit=limit))

    @property
    def fingerprint(self):
        return sha256(canonical_storage_json({
            "name": self.name, "workspace": self.workspace,
            "authority_epoch": self.authority_epoch,
            "expected_generation": self.expected_generation,
            "payload": read_storage_json(self.payload, limit=operation_limit(self.name)),
        }, limit=operation_limit(self.name) + 1024)).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    operation_id: str
    fingerprint: str
    result: bytes = field(repr=False)
    evidence: CommitEvidence

    def __post_init__(self):
        storage_identifier(self.operation_id)
        _digest(self.fingerprint)
        if type(self.evidence) is not CommitEvidence or self.evidence.operation_id != self.operation_id:
            _invalid()
        object.__setattr__(self, "result", canonical_storage_json(read_storage_json(self.result)))


def receipt_record(receipt):
    if type(receipt) is not OperationReceipt:
        _invalid()
    return {"operation_id": receipt.operation_id, "fingerprint": receipt.fingerprint,
            "result": read_storage_json(receipt.result), "evidence": evidence_record(receipt.evidence)}


def read_operation_receipt(value):
    if type(value) is not dict or value.keys() != {"operation_id", "fingerprint", "result", "evidence"}:
        _invalid()
    return OperationReceipt(value["operation_id"], value["fingerprint"],
                            canonical_storage_json(value["result"]), read_evidence(value["evidence"]))


@dataclass(frozen=True, slots=True)
class OperationResult:
    receipt: OperationReceipt
    replayed: bool = False

    def __post_init__(self):
        if type(self.receipt) is not OperationReceipt or type(self.replayed) is not bool:
            _invalid()


def check_storage_operation(current, operation, previous=None):
    """Check a durable command under the backend's write lock/transaction.

    Return the original receipt on exact retry, before checking its old expected
    generation. An unknown/expired operation with stale evidence must not run.
    The adapter validates the command's domain payload before making any writes.
    """
    if type(current) is not CommitEvidence or type(operation) is not StorageOperation:
        _invalid()
    require_primary(current)
    if previous is not None:
        if type(previous) is not OperationReceipt or previous.operation_id != operation.operation_id:
            _invalid()
        if (previous.evidence.budget_id != current.budget_id
                or previous.evidence.environment != current.environment
                or previous.evidence.generation > current.generation
                or (previous.evidence.generation == current.generation
                    and previous.evidence.state_digest != current.state_digest)):
            _invalid()
        if previous.fingerprint != operation.fingerprint:
            raise StoreError("Operation identity already belongs to a different storage command.")
        return OperationResult(previous, replayed=True)
    if (operation.authority_epoch != current.authority_epoch
            or operation.expected_generation != current.generation):
        raise StoreError("Storage changed or the prior operation outcome is unknown; refresh evidence before writing.")
    return None


def require_primary(evidence):
    if type(evidence) is not CommitEvidence:
        _invalid()
    if evidence.role != "primary":
        raise StoreError("This storage instance is fenced and cannot accept financial changes.")


def validate_role_transition(current, transition_id, role, authority_epoch=None, previous_transition_id=None):
    """Pure role/epoch policy; adapters persist its result with compare-and-set.

    Return the target epoch. The adapter separately rejects a mismatched complete
    expected CommitEvidence and records transition_id in the same native commit.
    """
    if type(current) is not CommitEvidence:
        _invalid()
    storage_identifier(transition_id)
    if type(role) is not str or role not in STORAGE_ROLES:
        _invalid()
    epoch = current.authority_epoch if authority_epoch is None else authority_epoch
    _uuid(epoch)
    if role == current.role:
        if previous_transition_id == transition_id and epoch == current.authority_epoch:
            return epoch
        raise StoreError("Storage role transition does not match its durable transition identity.")
    allowed = False
    if current.role == "primary" and role == "fenced":
        allowed = epoch == current.authority_epoch
    elif current.role == "fenced" and role in {"primary", "retired"}:
        allowed = previous_transition_id == transition_id and epoch == current.authority_epoch
    elif current.role == "staged" and role == "copy":
        allowed = previous_transition_id == transition_id and epoch == current.authority_epoch
    elif current.role in {"staged", "copy"} and role == "primary":
        allowed = (previous_transition_id == transition_id and authority_epoch is not None
                   and epoch != current.authority_epoch)
    if not allowed:
        raise StoreError("Storage role transition requires validated host authority.")
    return epoch


def dispatch_storage_operation(backend, operation):
    """Fixed data-only dispatch inside an adapter's atomic operation boundary.

    Public backend methods continue to apply the core's financial validation.
    Adapters must not call this outside their evidence/receipt transaction.
    """
    if type(operation) is not StorageOperation:
        _invalid()
    name, workspace = operation.name, operation.workspace
    payload = read_storage_json(operation.payload, limit=operation_limit(operation.name))
    if name == "recorded_command":
        from whab.domain.history import context
        inner = payload['operation']
        if inner not in STORAGE_OPERATIONS - {'recorded_command'}:
            raise StoreError('Invalid recorded command.')
        context(payload['context'])
        nested = StorageOperation(operation.operation_id, operation.authority_epoch, operation.expected_generation, workspace, inner, canonical_storage_json(payload['parameters'], limit=operation_limit(inner)))
        return dispatch_storage_operation(backend, nested)
    if name == "history_apply":
        return backend.history_apply(workspace, payload['action'], payload['target'], payload['fingerprint'])
    if name == "add_category_group":
        return backend.add_category_group(workspace, payload["name"])
    if name == "rename_category":
        return backend.rename_category(workspace, payload["categoryId"], payload["name"])
    if name == "move_category":
        return backend.move_category(workspace, payload["categoryId"], payload["parentId"])
    if name in {'delete_category', 'delete_category_group'}:
        return getattr(backend, name)(workspace, payload['categoryId'] if name == 'delete_category' else payload['parentId'])
    if name in {"add_account", "add_category"}:
        return getattr(backend, name)(workspace, payload)
    if name == "add_transaction":
        source = payload.pop("source", "manual")
        return backend.add_transaction(workspace, payload, source)
    if name == "update_account":
        identifier = payload.pop("accountId")
        return backend.update_account(workspace, identifier, payload)
    if name == "edit_transaction_details":
        return backend.edit_transaction_details(workspace, payload["transactionId"], payload["changes"])
    if name == "edit_transaction":
        identifier = payload.pop("transactionId")
        fields = {"categoryId": "category_id", "reviewed": "reviewed", "note": "note"}
        return backend.edit_transaction(workspace, identifier,
                                        **{fields[key]: value for key, value in payload.items()})
    if name in {"edit_transactions", "classify_accounts"}:
        return getattr(backend, name)(workspace, payload["edits"])
    if name == "review_transactions":
        return backend.review_transactions(workspace, payload["transactionIds"], payload["reviewed"])
    if name == "assign":
        return backend.assign(workspace, payload["categoryId"], payload["month"], payload["assigned"])
    if name == "assign_next_month":
        return backend.assign_next_month(workspace, payload["month"], payload["currency"], payload["assigned"])
    if name == "set_rollover_mode":
        return backend.set_rollover_mode(workspace, payload["month"], payload["currency"], payload["mode"])
    if name == "save_item":
        return backend.save_item(payload["itemId"], payload["encryptedToken"], payload["institutionName"],
                                 provider_id=payload.get("providerId", "plaid"))
    if name == "set_item_status":
        return backend.set_item_status(payload["itemId"], payload["status"], payload["error"])
    if name == "disconnect_item":
        return backend.disconnect_item(payload["itemId"])
    if name == "configure_bank_sync":
        configure = getattr(backend, "configure_bank_sync", None)
        if not callable(configure):
            raise StoreError("This storage plugin does not support bank account setup. Upgrade it first.")
        return configure(payload["itemId"], payload["accounts"], payload["selection"])
    if name == "apply_sync":
        fields = {"accounts": "accounts", "historicalComplete": "historical_complete", "expectedCursor": "expected_cursor"}
        return backend.apply_sync(payload["itemId"], payload["changes"], payload["cursor"],
                                  **{value: payload[key] for key, value in fields.items() if key in payload})
    if name == "import_workspace":
        data = payload["data"]
        if type(data) is not dict or not {"accounts", "categories", "transactions", "assignments", "budget_start_month"} <= data.keys() or not data.keys() <= set(WorkspaceImport.__dataclass_fields__):
            _invalid()
        collections = {"accounts", "categories", "transactions", "assignments", "warnings"}
        for key in collections & data.keys():
            if type(data[key]) is not list:
                _invalid()
        imported = WorkspaceImport(**{key: tuple(value) if key in collections else value for key, value in data.items()}).validated_copy()
        return backend.import_workspace(workspace, payload["pluginId"], payload["fingerprint"], imported)
    if name == "restore_budget":
        return backend.restore_budget(workspace, validate_portable_budget(payload["snapshot"]))
    if name in {"restore_budget_files", "prepare_budget_files"}:
        if type(payload["files"]) is not dict or len(payload["files"]) > 10000:
            _invalid()
        files = {}
        for key, encoded in payload["files"].items():
            if type(key) is not str or type(encoded) is not str:
                _invalid()
            try:
                files[key] = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                _invalid()
        if name == "prepare_budget_files":
            return backend.prepare_budget_files(workspace, files, existing=payload["existing"])
        return backend.restore_budget_files(workspace, files)
    if name == "save_receipt":
        image = payload["image"]
        if type(image) is not str or len(image) > 4 * ((MAX_RECEIPT_BYTES + 2) // 3):
            _invalid()
        try:
            raw = base64.b64decode(image, validate=True)
        except (ValueError, binascii.Error):
            _invalid()
        receipt_media_type(raw)
        return backend.save_receipt(workspace, payload["transactionId"], raw)
    return backend.delete_receipt(workspace, payload["transactionId"])


def _exact(value, names):
    if type(value) is not dict or value.keys() != set(names):
        _invalid()


def _records(value, limit):
    if type(value) is not list or len(value) > limit:
        _invalid()
    return value


def _import_receipt(value):
    if value is None:
        return None
    _exact(value, {"pluginId", "fingerprint", "counts"})
    bank_identifier(value["pluginId"], limit=64)
    bank_identifier(value["fingerprint"])
    _exact(value["counts"], {"accounts", "categories", "transactions", "assignments"})
    for count in value["counts"].values():
        _integer(count)
        if count > 250000:
            _invalid()
    return {**value, "counts": value["counts"].copy()}


def _normalize_ledger(value, *, validate=True):
    ledger = validate_portable_budget(value) if validate else value
    for name in ("accounts", "categories", "transactions"):
        ledger[name].sort(key=lambda row: row["id"])
    for name, keys in (("assignments", ("month", "categoryId")),
                       ("futureFunding", ("month", "currency")),
                       ("rolloverModes", ("month", "currency"))):
        ledger[name].sort(key=lambda row: tuple(row[key] for key in keys))
    return ledger


def decode_local_state(financial, protected):
    """Validate complete local payloads and return detached plain workspace state.

    This deliberately preserves external account freshness and encrypted tokens.
    It neither decrypts credentials nor creates provider connections.
    """
    financial = read_storage_json(financial, limit=MAX_LOCAL_TRANSFER_BYTES)
    protected = read_storage_json(protected, limit=MAX_LOCAL_TRANSFER_BYTES)
    for section in (financial, protected):
        _exact(section, {"formatVersion", "workspaces"})
        if type(section["formatVersion"]) is not int or section["formatVersion"] != 1:
            _invalid()
        _exact(section["workspaces"], {"personal", "demo"})
    states = {}
    for workspace in ("personal", "demo"):
        public = financial["workspaces"][workspace]
        private = protected["workspaces"][workspace]
        _exact(public, {"ledger", "receipts", "importReceipt"})
        _exact(private, {"items", "associations"})
        ledger = _normalize_ledger(public["ledger"])
        state = initial_state(workspace, ledger["budgetStartMonth"])
        restore_snapshot(state, ledger, mark_external_stale=False)
        state["importReceipt"] = _import_receipt(public["importReceipt"])
        for record in _records(public["receipts"], 10000):
            _exact(record, {"transactionId", "image"})
            identifier, encoded = record["transactionId"], record["image"]
            storage_identifier(identifier)
            if identifier not in state["transactions"] or identifier in state["receipts"]:
                _invalid()
            if type(encoded) is not str or len(encoded) > 4 * ((MAX_RECEIPT_BYTES + 2) // 3):
                _invalid()
            try:
                image = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                _invalid()
            if base64.b64encode(image).decode("ascii") != encoded:
                _invalid()
            receipt_media_type(image)
            state["receipts"][identifier] = image
        if workspace == "demo" and private["items"]:
            _invalid()
        for item in _records(private["items"], 1000):
            _exact(item, {"id", "token", "institutionName", "status", "lastSynced", "error", "cursor", "provider"}
                   | ({"syncSettings"} if "syncSettings" in item else set())
                   | ({"closedAccountCursors"} if "closedAccountCursors" in item else set()))
            if "closedAccountCursors" in item:
                from whab.domain.bank_setup import validate_closed_account_cursors
                validate_closed_account_cursors(item["closedAccountCursors"])
            if "syncSettings" in item:
                from whab.domain.bank_setup import validate_sync_settings
                validate_sync_settings(item["syncSettings"])
            identifier = bank_identifier(item["id"], limit=4096)
            if identifier in state["items"]:
                _invalid()
            for name in ("institutionName", "status", "provider"):
                storage_identifier(item[name], limit=200)
            if (len(item["provider"]) > 64 or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", item["provider"]) is None
                    or item["provider"] in {"manual", "demo"}):
                _invalid()
            for name in ("token", "lastSynced", "error", "cursor"):
                if item[name] is not None:
                    if type(item[name]) is not str or len(item[name]) > 16384:
                        _invalid()
            if item["token"] == "":
                _invalid()
            if item["lastSynced"] is not None:
                try:
                    if datetime.fromisoformat(item["lastSynced"]).tzinfo is None:
                        _invalid()
                except ValueError:
                    _invalid()
            state["items"][identifier] = item.copy()
        seen = set()
        for record in _records(private["associations"], 1000):
            _exact(record, {"accountId", "itemId", "mask"})
            account_id, item_id = record["accountId"], record["itemId"]
            storage_identifier(account_id)
            if item_id is not None:
                bank_identifier(item_id, limit=4096)
            mask = record["mask"]
            if mask is not None and (type(mask) is not str or len(mask) > 200):
                _invalid()
            account, item = state["accounts"].get(account_id), state["items"].get(item_id)
            if account_id in seen or account is None:
                _invalid()
            if item_id is not None and (workspace == "demo" or item is None or account["source"] != item["provider"]):
                _invalid()
            seen.add(account_id)
            if item_id is not None:
                account["itemId"] = item_id
            if mask is not None:
                account["mask"] = mask
        if seen != state["accounts"].keys():
            _invalid()
        states[workspace] = state
    return states


def _local_state_records(states, *, validate):
    if type(states) is not dict or states.keys() != {"personal", "demo"}:
        _invalid()
    financial, protected = {}, {}
    for name, state in states.items():
        financial[name] = {
            "ledger": _normalize_ledger(portable_snapshot(state, validate=validate), validate=validate),
            "receipts": [{"transactionId": key, "image": base64.b64encode(image).decode("ascii")}
                         for key, image in sorted(state.get("receipts", {}).items())],
            "importReceipt": state.get("importReceipt"),
        }
        protected[name] = {
            "items": sorted(state["items"].values(), key=lambda item: item["id"]),
            "associations": sorted([
                {"accountId": account["id"], "itemId": account.get("itemId"), "mask": account.get("mask")}
                for account in state["accounts"].values()
            ], key=lambda row: row["accountId"]),
        }
    return tuple({"formatVersion": 1, "workspaces": value} for value in (financial, protected))


def encode_local_state(states, *, validate=True, encoder=canonical_storage_json):
    """Canonical complete payloads from plain adapter state.

    ``validate=False`` is exclusively for native evidence over already-trusted
    state beyond portable limits. LocalTransferSnapshot always validates fully.
    """
    bodies = tuple(encoder(value, limit=MAX_LOCAL_TRANSFER_BYTES)
                   for value in _local_state_records(states, validate=validate))
    if sum(map(len, bodies)) > MAX_LOCAL_TRANSFER_BYTES:
        _invalid()
    if validate:
        decode_local_state(*bodies)
    return bodies


def local_state_digest(financial, protected):
    """Logical content only, excluding authority/operation-receipt metadata."""
    result = sha256(len(financial).to_bytes(8, "big"))
    result.update(financial)
    result.update(protected)
    return result.hexdigest()


def native_state_digest(states, *, chunks):
    """Hash trusted native records without copying complete canonical payloads.

    Native adapters supply a canonical byte-chunk encoder for immutable records.
    Untrusted transfers continue through encode/decode_local_state validation.
    """
    return native_records_digest(_local_state_records(states, validate=False), chunks=chunks)


def native_records_digest(records, *, chunks):
    """Hash complete canonical record views retained by a trusted native adapter.

    Records use the exact two envelopes supplied to native_state_digest's chunk
    encoder. Adapters must invalidate retained views after untracked writes.
    """
    if type(records) not in {list, tuple} or len(records) != 2:
        _invalid()
    bodies = [tuple(chunks(value, limit=MAX_LOCAL_TRANSFER_BYTES)) for value in records]
    if any(type(raw) is not bytes for body in bodies for raw in body):
        _invalid()
    lengths = [sum(map(len, body)) for body in bodies]
    if sum(lengths) > MAX_LOCAL_TRANSFER_BYTES:
        _invalid()
    result = sha256(lengths[0].to_bytes(8, "big"))
    for body in bodies:
        for raw in body:
            result.update(raw)
    return result.hexdigest()


@dataclass(frozen=True, slots=True)
class LocalTransferSnapshot:
    source: CommitEvidence
    financial: bytes = field(repr=False)
    protected: bytes = field(repr=False)
    receipts: tuple[OperationReceipt, ...] = field(default=(), repr=False)

    def __post_init__(self):
        if type(self.source) is not CommitEvidence:
            _invalid()
        canonical = encode_local_state(decode_local_state(self.financial, self.protected))
        object.__setattr__(self, "financial", canonical[0])
        object.__setattr__(self, "protected", canonical[1])
        if self.source.state_digest != local_state_digest(*canonical):
            raise StoreError("Local storage snapshot does not match its commit evidence.")
        if (type(self.receipts) is not tuple or len(self.receipts) > MAX_OPERATION_RECEIPTS
                or any(type(row) is not OperationReceipt for row in self.receipts)):
            _invalid()
        ids = set()
        for row in self.receipts:
            if (row.operation_id in ids or row.evidence.budget_id != self.source.budget_id
                    or row.evidence.environment != self.source.environment
                    or row.evidence.generation > self.source.generation
                    or (row.evidence.generation == self.source.generation
                        and row.evidence.state_digest != self.source.state_digest)):
                _invalid()
            ids.add(row.operation_id)
        if sum(len(row.result) for row in self.receipts) + sum(map(len, canonical)) > MAX_LOCAL_TRANSFER_BYTES:
            _invalid()
        object.__setattr__(self, "receipts", tuple(sorted(self.receipts, key=lambda row: row.operation_id)))
        if sum(map(len, canonical)) + len(self.receipt_bytes) > MAX_LOCAL_TRANSFER_BYTES:
            _invalid()

    @property
    def receipt_bytes(self):
        return canonical_storage_json([receipt_record(row) for row in self.receipts],
                                      limit=MAX_LOCAL_TRANSFER_BYTES)

    @property
    def digest(self):
        return sha256(canonical_storage_json(evidence_record(self.source))
                      + self.financial + self.protected + self.receipt_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class TransferPreview:
    empty: bool
    supported: bool
    reason_code: str | None = None

    def __post_init__(self):
        if type(self.empty) is not bool or type(self.supported) is not bool:
            _invalid()
        if self.reason_code not in {None, "destination_not_empty", "unsupported_records", "capacity_exceeded"}:
            _invalid()


class StorageAuthority(Protocol):
    """Host-private control plane; role fences apply to EVERY mutation method.

    Persist identity/role/epoch with data. Role-only changes preserve the logical
    generation. Compare the complete expected evidence atomically. A fenced
    source can resume its original epoch only for the same transition before a
    host authority decision; retired instances cannot resume. Staged/copy
    activation requires a fresh epoch and an explicit host decision.
    """

    def commit_evidence(self) -> CommitEvidence: ...

    def set_role(self, transition_id: str, expected: CommitEvidence, role: str, *,
                 authority_epoch: str | None = None) -> CommitEvidence: ...


class LocalTransferStorage(Protocol):
    """Same-device transfer only, with exact opaque ciphertext preservation."""

    def export_local(self) -> LocalTransferSnapshot: ...

    def preview_local(self, snapshot: LocalTransferSnapshot) -> TransferPreview: ...

    def import_local(self, transfer_id: str, snapshot: LocalTransferSnapshot) -> CommitEvidence: ...


class StorageCommands(Protocol):
    """Atomic durable command results, with bounded versioned data dispatch.

    Validate a whitelisted operation with core rules. Commit its result receipt,
    logical generation and state in one native commit. Retrying an exact receipt
    returns its original result before expected-generation checks. An absent or
    expired receipt is never proof that a command did not commit.
    """

    def execute_operation(self, operation: StorageOperation) -> OperationResult: ...

    def operation_receipt(self, operation_id: str) -> OperationReceipt | None: ...
