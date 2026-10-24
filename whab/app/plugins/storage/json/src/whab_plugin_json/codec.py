"""Exact bounded JSON records; portable payloads never contain protected state."""

import base64
import binascii
import json
import re
from datetime import datetime
from uuid import UUID

from whab.domain.errors import StoreError
from whab.domain.imports import bank_identifier
from whab.domain.ledger_state import initial_state, portable_snapshot, restore_snapshot
from whab.domain.money import MAX_SAFE_INTEGER
from whab.domain.portable import validate_portable_budget
from whab.domain.receipts import MAX_RECEIPT_BYTES, receipt_media_type
from whab.plugins.storage_replica import read_replica_watermark
from whab.plugins.storage_transfer import (
    CommitEvidence,
    LocalTransferSnapshot,
    encode_local_state,
    local_state_digest,
    read_operation_receipt,
    storage_identifier,
)

from .authority import validate_metadata

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PORTABLE_BYTES = 50 * 1024 * 1024
MAX_DEPTH = 24
_ITEM_FIELDS = {"id", "token", "institutionName", "status", "lastSynced", "error", "cursor", "provider"}
_COUNTS = {"accounts", "categories", "transactions", "assignments"}


def _invalid():
    raise StoreError("JSON storage contains invalid or unsupported records.")


def _exact(value, keys):
    if type(value) is not dict or value.keys() != keys:
        _invalid()


def _text(value, *, limit=4096, nullable=False, empty=False):
    if nullable and value is None:
        return
    if type(value) is not str or len(value) > limit or "\x00" in value or (not empty and not value):
        _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _invalid()


def _integer(value, maximum=MAX_SAFE_INTEGER):
    if type(value) is not int or not 0 <= value <= maximum:
        _invalid()


def _uuid(value):
    if type(value) is not str:
        _invalid()
    try:
        if str(UUID(value)) != value:
            _invalid()
    except ValueError:
        _invalid()


def _records(value, limit):
    if type(value) is not list or len(value) > limit:
        _invalid()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _constant(value):
    _invalid()


def parse(raw, limit=MAX_FILE_BYTES):
    """Bound size and nesting before decoding; reject duplicate/nonfinite fields."""
    if type(raw) is not bytes or not raw or len(raw) > limit:
        raise StoreError("JSON storage exceeds its size limit or is empty.")
    depth, quoted, escaped = 0, False, False
    for value in raw:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in (91, 123):
            depth += 1
            if depth > MAX_DEPTH:
                raise StoreError("JSON storage nesting exceeds its supported limit.")
        elif value in (93, 125):
            depth -= 1
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant, parse_float=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise StoreError("JSON storage is truncated, damaged or invalid.") from None


def serialize(value, limit=MAX_FILE_BYTES):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        _invalid()
    if len(raw) > limit:
        raise StoreError("JSON storage exceeds its supported size limit.")
    return raw


def _images(records, state):
    images = {}
    for record in _records(records, 10000):
        _exact(record, {"transactionId", "image"})
        identifier, encoded = record["transactionId"], record["image"]
        _text(identifier, limit=200)
        if identifier in images or identifier not in state["transactions"]:
            _invalid()
        _text(encoded, limit=4 * ((MAX_RECEIPT_BYTES + 2) // 3))
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            _invalid()
        if base64.b64encode(image).decode("ascii") != encoded:
            _invalid()
        receipt_media_type(image)
        images[identifier] = image
    return images


def _import_receipt(value):
    if value is None:
        return None
    _exact(value, {"pluginId", "fingerprint", "counts"})
    _text(value["pluginId"], limit=64)
    _text(value["fingerprint"], limit=200)
    bank_identifier(value["pluginId"], limit=64)
    bank_identifier(value["fingerprint"])
    _exact(value["counts"], _COUNTS)
    for key in _COUNTS:
        _integer(value["counts"][key], 250000)
    return {**value, "counts": value["counts"].copy()}


def _read_ledger(value, workspace, *, portable=False, complete_associations=False):
    _exact(value, {"ledger", "items", "associations", "receipts", "importReceipt"})
    snapshot = validate_portable_budget(value["ledger"])
    state = initial_state(workspace, snapshot["budgetStartMonth"])
    restore_snapshot(state, snapshot, mark_external_stale=portable)
    state["receipts"] = _images(value["receipts"], state)
    state["importReceipt"] = _import_receipt(value["importReceipt"])
    if workspace == "demo" and (value["items"] or (value["associations"] and not complete_associations)):
        _invalid()
    for record in _records(value["items"], 1000):
        _exact(record, _ITEM_FIELDS)
        _text(record["id"], limit=4096)
        identifier = bank_identifier(record["id"], limit=4096)
        if identifier in state["items"]:
            _invalid()
        for field in ("institutionName", "status", "provider"):
            _text(record[field], limit=200)
        if len(record["provider"]) > 64 or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", record["provider"]) is None or record["provider"] in {"manual", "demo"}:
            _invalid()
        for field in ("token", "lastSynced", "error", "cursor"):
            _text(record[field], limit=16384, nullable=True, empty=True)
        if record["token"] == "":
            _invalid()
        if record["lastSynced"] is not None:
            try:
                if datetime.fromisoformat(record["lastSynced"]).tzinfo is None:
                    _invalid()
            except ValueError:
                _invalid()
        state["items"][identifier] = record.copy()
    seen = set()
    for record in _records(value["associations"], 1000):
        _exact(record, {"accountId", "itemId", "mask"})
        account_id, item_id = record["accountId"], record["itemId"]
        _text(account_id, limit=200)
        _text(item_id, limit=4096, nullable=complete_associations)
        _text(record["mask"], limit=200, empty=True, nullable=complete_associations)
        account, item = state["accounts"].get(account_id), state["items"].get(item_id)
        if (account_id in seen or account is None
                or (item_id is not None and (workspace == "demo" or item is None or account["source"] != item["provider"]))):
            _invalid()
        seen.add(account_id)
        account.update(itemId=item_id, mask=record["mask"])
    if complete_associations and seen != state["accounts"].keys():
        _invalid()
    return state


def _pack_ledger(state, *, portable=False, complete_associations=True):
    return {
        "ledger": portable_snapshot(state),
        "items": [] if portable else list(state["items"].values()),
        "associations": [] if portable else [
            {"accountId": row["id"], "itemId": row.get("itemId"),
             "mask": row.get("mask") if complete_associations else row.get("mask", "")}
            for row in state["accounts"].values() if complete_associations or row.get("itemId") is not None
        ],
        "receipts": [
            {"transactionId": identifier, "image": base64.b64encode(image).decode("ascii")}
            for identifier, image in sorted(state["receipts"].items())
        ],
        "importReceipt": state["importReceipt"],
    }


def decode(raw):
    value = parse(raw)
    if type(value) is not dict or type(value.get("formatVersion")) is not int or value["formatVersion"] not in {1, 2, 3}:
        raise StoreError("This JSON storage schema is newer or unsupported; install a compatible plugin.")
    fields = {"formatVersion", "generation", "operationId", "authorityEpoch", "workspaces"}
    _exact(value, fields | ({"authority"} if value["formatVersion"] >= 2 else set()))
    _integer(value["generation"])
    if value["formatVersion"] == 1:
        _uuid(value["operationId"])
    else:
        if value["operationId"] is not None:
            storage_identifier(value["operationId"])
        value["authority"] = validate_metadata(value["authority"], replica=value["formatVersion"] == 3)
        if any(read["evidence"]["generation"] > value["generation"] for read in value["authority"]["receipts"]):
            _invalid()
    _uuid(value["authorityEpoch"])
    _exact(value["workspaces"], {"personal", "demo"})
    document = {**value, "workspaces": {
        name: _read_ledger(value["workspaces"][name], name, complete_associations=value["formatVersion"] >= 2)
        for name in ("personal", "demo")
    }}
    if value["formatVersion"] >= 2:
        financial, protected = encode_local_state(document["workspaces"], validate=False)
        digest = local_state_digest(financial, protected)
        if any(record["evidence"]["generation"] == value["generation"] and record["evidence"]["state_digest"] != digest
               for record in value["authority"]["receipts"]):
            _invalid()
        if value["formatVersion"] == 3 and value["authority"]["replica"] is not None:
            watermark = read_replica_watermark(value["authority"]["replica"])
            if value["authority"]["role"] == "copy" and (
                value["authorityEpoch"] != watermark.source_authority_epoch
                or value["generation"] != watermark.source_generation
                or value["operationId"] != watermark.source_operation_id
                or digest != watermark.state_digest
            ):
                _invalid()
            if value["authority"]["role"] == "copy":
                source = CommitEvidence(
                    value["authority"]["budgetId"], value["authority"]["environment"], watermark.source_instance_id,
                    watermark.source_authority_epoch, watermark.source_generation, watermark.source_operation_id,
                    "primary", watermark.state_digest,
                )
                snapshot = LocalTransferSnapshot(source, financial, protected, tuple(
                    read_operation_receipt(record) for record in value["authority"]["receipts"]
                ))
                if snapshot.digest != watermark.transfer_digest:
                    _invalid()
    return document


def encode(document):
    value = {**document, "workspaces": {
        name: _pack_ledger(state, complete_associations=document["formatVersion"] >= 2)
        for name, state in document["workspaces"].items()
    }}
    raw = serialize(value)
    # Full reference/type validation is part of every detached candidate commit.
    return raw, decode(raw)


def export_files(state):
    value = {"formatVersion": 1, **_pack_ledger(state, portable=True)}
    return {"budget.json": serialize(value, MAX_PORTABLE_BYTES)}


def equivalent(first, second):
    """Compare normalized portable data independently of record/object ordering."""
    def semantic(state):
        value = _pack_ledger(state, portable=True)
        ledger = value["ledger"]
        for name, keys in (
            ("accounts", ("id",)), ("categories", ("id",)), ("transactions", ("id",)),
            ("assignments", ("month", "categoryId")),
            ("futureFunding", ("month", "currency")), ("rolloverModes", ("month", "currency")),
        ):
            ledger[name].sort(key=lambda row: tuple(row[key] for key in keys))
        for account in ledger["accounts"]:
            account["stale"] = account["stale"] or account["source"] not in {"manual", "demo"}
        return value

    return semantic(first) == semantic(second)


def read_files(files):
    if not isinstance(files, dict) or files.keys() != {"budget.json"}:
        raise StoreError("JSON budgets require exactly one budget.json payload.")
    value = parse(files["budget.json"], MAX_PORTABLE_BYTES)
    _exact(value, {"formatVersion", "ledger", "items", "associations", "receipts", "importReceipt"})
    if type(value["formatVersion"]) is not int or value["formatVersion"] != 1 or value["items"] or value["associations"]:
        raise StoreError("JSON portable budgets cannot contain protected provider records.")
    return _read_ledger({key: row for key, row in value.items() if key != "formatVersion"}, "personal", portable=True)
