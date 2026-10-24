"""Version one storage RPC: fixed methods, public records and bounded byte bodies.

This channel contains private same-device records. It is never a portable file,
diagnostic format, remote API or executable-object transport.
"""

import hashlib
import struct
from dataclasses import fields

from whab.domain.transactions import UNSET

from .storage_replica import ReplicaWatermark
from .storage_transfer import (
    CommitEvidence,
    LocalTransferSnapshot,
    OperationReceipt,
    OperationResult,
    StorageOperation,
    TransferPreview,
    canonical_storage_json,
    read_storage_json,
)
from .wire import WireError

STORAGE_PROTOCOL = 1
HEADER = struct.Struct("!4sHII")
MAX_METADATA = 64 * 1024 * 1024
MAX_BODY = 256 * 1024 * 1024
_RECORDS = {record.__name__: record for record in (
    CommitEvidence, StorageOperation, OperationReceipt, OperationResult,
    LocalTransferSnapshot, TransferPreview, ReplicaWatermark,
)}

# (required positional names, optional names/defaults). No arbitrary attribute
# lookup, kwargs, database handle or implementation-specific state is exposed.
METHODS = {
    "accounts": (("workspace",), {}), "categories": (("workspace",), {}),
    "transactions": (("workspace",), {"month": None}),
    "workspace": (("workspace",), {"month": None}),
    "workspace_periods": (("workspace", "month", "count"), {}),
    "account_overview": (("workspace", "month", "currency"), {}),
    "account_settings": (("workspace", "account_id"), {}),
    "transaction_register": (("workspace", "month", "currency", "account_id"), {}),
    "transaction_register_filtered": (("workspace", "start_date", "end_date", "currency", "account_ids"), {}),
    "transaction_register_page": (("workspace", "start_date", "end_date", "currency", "account_ids", "search", "review", "page", "page_size"), {}),
    "receipt_transaction_exists": (("workspace", "transaction_id"), {}),
    "receipt_ids": (("workspace",), {}),
    "receipt_ids_for": (("workspace", "transaction_ids"), {}),
    "get_receipt": (("workspace", "transaction_id"), {}),
    "items": ((), {}), "get_item": (("item_id",), {}),
    "export_budget": ((), {"workspace": "personal"}),
    "export_budget_files": ((), {"workspace": "personal"}),
    "preview_workspace_import": (("workspace", "plugin_id", "fingerprint"), {}),
    "commit_evidence": ((), {}), "operation_receipt": (("operation_id",), {}),
    "execute_operation": (("operation",), {}),
    "set_role": (("transition_id", "expected", "role"), {"authority_epoch": None}),
    "export_local": ((), {}), "preview_local": (("snapshot",), {}),
    "import_local": (("transfer_id", "snapshot"), {}),
    "replica_watermark": ((), {}),
    "apply_replica": (("replica_id", "snapshot", "namespace_digest", "expected", "namespace_generation"), {}),
    "prepare_promotion": (("transition_id", "expected", "watermark"), {}),
    "abort_promotion": (("transition_id", "expected"), {}),
}
CONTROL_WRITES = frozenset({"set_role", "import_local", "apply_replica", "prepare_promotion", "abort_promotion"})


def bind_call(name, args, kwargs):
    if name not in METHODS or type(args) not in {tuple, list} or type(kwargs) is not dict:
        raise WireError()
    required, optional = METHODS[name]
    names = (*required, *optional)
    if len(args) > len(names) or any(key not in names or key in names[:len(args)] for key in kwargs):
        raise WireError()
    bound = dict(zip(names, args)) | kwargs
    if any(key not in bound for key in required):
        raise WireError()
    return bound


def encode(value):
    bodies = []
    total, nodes = 0, 0

    def pack(item, depth=0):
        nonlocal total, nodes
        nodes += 1
        if depth > 32 or nodes > 2_000_000:
            raise WireError()
        if item is UNSET:
            return ["unset"]
        if type(item) is bytes:
            offset = total
            total += len(item)
            if total > MAX_BODY:
                raise WireError()
            bodies.append(item)
            return ["bytes", offset, len(item), hashlib.sha256(item).hexdigest()]
        if type(item).__name__ in _RECORDS and type(item) is _RECORDS[type(item).__name__]:
            return ["record", type(item).__name__, {field.name: pack(getattr(item, field.name), depth + 1) for field in fields(item)}]
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise WireError()
            return ["dict", [[key, pack(row, depth + 1)] for key, row in item.items()]]
        if type(item) in {list, tuple}:
            return ["tuple" if type(item) is tuple else "list", [pack(row, depth + 1) for row in item]]
        if item is None or type(item) in {str, int, bool}:
            return ["value", item]
        raise WireError()

    metadata = canonical_storage_json(pack(value), limit=MAX_METADATA)
    return HEADER.pack(b"WHST", STORAGE_PROTOCOL, len(metadata), total) + metadata + b"".join(bodies)


def lengths(header):
    if type(header) is not bytes or len(header) != HEADER.size:
        raise WireError()
    magic, version, metadata, body = HEADER.unpack(header)
    if magic != b"WHST" or version != STORAGE_PROTOCOL or not 2 <= metadata <= MAX_METADATA or body > MAX_BODY:
        raise WireError()
    return metadata, body


def decode(raw):
    metadata_size, body_size = lengths(raw[:HEADER.size])
    if len(raw) != HEADER.size + metadata_size + body_size:
        raise WireError()
    value = read_storage_json(raw[HEADER.size:HEADER.size + metadata_size], limit=MAX_METADATA)
    body, ranges, nodes = memoryview(raw)[HEADER.size + metadata_size:], [], 0

    def unpack(item, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 2_000_000 or type(item) is not list or not item:
            raise WireError()
        kind = item[0]
        if item == ["unset"]:
            return UNSET
        if len(item) == 2 and kind == "value" and (item[1] is None or type(item[1]) in {str, int, bool}):
            return item[1]
        if kind == "bytes" and len(item) == 4:
            offset, size, digest = item[1:]
            if type(offset) is not int or type(size) is not int or not 0 <= offset <= body_size or not 0 <= size <= body_size - offset:
                raise WireError()
            result = bytes(body[offset:offset + size])
            if hashlib.sha256(result).hexdigest() != digest:
                raise WireError()
            ranges.append((offset, size))
            return result
        if len(item) == 2 and kind in {"list", "tuple"} and type(item[1]) is list:
            result = [unpack(row, depth + 1) for row in item[1]]
            return tuple(result) if kind == "tuple" else result
        if len(item) == 2 and kind == "dict" and type(item[1]) is list:
            result = {}
            for pair in item[1]:
                if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str or pair[0] in result:
                    raise WireError()
                result[pair[0]] = unpack(pair[1], depth + 1)
            return result
        if len(item) == 3 and kind == "record" and type(item[1]) is str and item[1] in _RECORDS and type(item[2]) is dict:
            record = _RECORDS[item[1]]
            if item[2].keys() != {field.name for field in fields(record)}:
                raise WireError()
            return record(**{key: unpack(row, depth + 1) for key, row in item[2].items()})
        raise WireError()

    result = unpack(value)
    consumed = 0
    for offset, size in sorted(ranges):
        if offset != consumed:
            raise WireError()
        consumed += size
    if consumed != body_size:
        raise WireError()
    return result
