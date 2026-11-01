"""Versioned local plugin messages: public records and scoped capability IDs.

The receiver never imports a type named by a message. Executable objects remain
in their owning process; capability IDs address explicitly exported SDK members.
"""

import hashlib
import json
import math
import struct
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import MappingProxyType

from .wire import WireError

VERSION = 1
HEADER = struct.Struct("!4sHII")
MAX_METADATA = 32 * 1024 * 1024
MAX_BODY = 256 * 1024 * 1024


def records():
    from whab.domain import file_imports, imports

    from . import bank_adapter, contracts, features, sdk, storage_replica, storage_transfer

    return {value.__module__ + "." + value.__name__: value
            for module in (sdk, contracts, features, bank_adapter, storage_replica,
                           storage_transfer, file_imports, imports)
            for value in vars(module).values()
            if isinstance(value, type) and is_dataclass(value)
            and value.__module__.startswith("whab.")}


def lengths(header):
    if type(header) is not bytes or len(header) != HEADER.size:
        raise WireError()
    magic, version, metadata, body = HEADER.unpack(header)
    if magic != b"WHPR" or version != VERSION or not 2 <= metadata <= MAX_METADATA or body > MAX_BODY:
        raise WireError()
    return metadata, body


class Codec:
    def __init__(self, export, resolve):
        self.export, self.resolve = export, resolve
        self.records = records()

    def encode(self, value):
        bodies, size, nodes = [], 0, 0

        def pack(item, depth=0):
            nonlocal size, nodes
            nodes += 1
            if depth > 40 or nodes > 2_000_000:
                raise WireError()
            if item is None or type(item) in {str, int, bool, float}:
                if type(item) is float and not math.isfinite(item):
                    raise WireError()
                return ["value", item]
            if type(item) is bytes:
                offset = size
                size += len(item)
                if size > MAX_BODY:
                    raise WireError()
                bodies.append(item)
                return ["bytes", offset, len(item), hashlib.sha256(item).hexdigest()]
            if isinstance(item, Path):
                return ["path", str(item)]
            if type(item) in {dict, MappingProxyType}:
                if any(type(key) is not str for key in item):
                    raise WireError()
                return ["dict", [[key, pack(row, depth + 1)] for key, row in item.items()]]
            if type(item) in {tuple, list}:
                return ["tuple" if type(item) is tuple else "list", [pack(row, depth + 1) for row in item]]
            name = type(item).__module__ + "." + type(item).__name__
            if name in self.records and type(item) is self.records[name]:
                prepare = getattr(self, "prepare_record", None)
                if prepare is not None:
                    item = prepare(item)
                return ["record", name, {field.name: pack(getattr(item, field.name), depth + 1)
                                         for field in fields(item) if field.name != "_process"}]
            return ["capability", self.export(item)]

        raw = json.dumps(pack(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_METADATA:
            raise WireError()
        return HEADER.pack(b"WHPR", VERSION, len(raw), size) + raw + b"".join(bodies)

    def decode(self, raw):
        metadata_size, body_size = lengths(raw[:HEADER.size])
        if len(raw) != HEADER.size + metadata_size + body_size:
            raise WireError()
        body = raw[HEADER.size + metadata_size:]
        nodes, consumed = 0, 0

        def unpack(item, depth=0):
            nonlocal nodes, consumed
            nodes += 1
            if depth > 40 or nodes > 2_000_000 or type(item) is not list or not item:
                raise WireError()
            kind = item[0]
            if kind == "value" and len(item) == 2:
                value = item[1]
                if value is None or type(value) in {str, int, bool} or type(value) is float and math.isfinite(value):
                    return value
            elif kind == "bytes" and len(item) == 4:
                offset, size, digest = item[1:]
                if (type(offset) is int and type(size) is int and offset == consumed
                        and size >= 0 and offset + size <= body_size and type(digest) is str):
                    value = body[offset:offset + size]
                    if hashlib.sha256(value).hexdigest() == digest:
                        consumed += size
                        return value
            elif kind == "path" and len(item) == 2 and type(item[1]) is str:
                return Path(item[1])
            elif kind == "dict" and len(item) == 2 and type(item[1]) is list:
                result = {}
                for pair in item[1]:
                    if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str or pair[0] in result:
                        raise WireError()
                    result[pair[0]] = unpack(pair[1], depth + 1)
                return result
            elif kind in {"tuple", "list"} and len(item) == 2 and type(item[1]) is list:
                values = [unpack(row, depth + 1) for row in item[1]]
                return tuple(values) if kind == "tuple" else values
            elif kind == "record" and len(item) == 3 and type(item[1]) is str and type(item[2]) is dict:
                record = self.records.get(item[1])
                if record is not None and set(item[2]) == {field.name for field in fields(record) if field.name != "_process"}:
                    value = record(**{key: unpack(row, depth + 1) for key, row in item[2].items()})
                    owner = getattr(self, "owner", None)
                    if record.__name__ == "Plugin" and owner is not None:
                        object.__setattr__(value, "_process", owner())
                    return value
            elif kind == "capability" and len(item) == 2:
                return self.resolve(item[1])
            raise WireError()

        def pairs(rows):
            result = {}
            for key, value in rows:
                if key in result:
                    raise WireError()
                result[key] = value
            return result

        try:
            value = unpack(json.loads(raw[HEADER.size:HEADER.size + metadata_size], object_pairs_hook=pairs))
        except (ValueError, TypeError, RecursionError, KeyError, AttributeError):
            raise WireError() from None
        if consumed != body_size:
            raise WireError()
        return value
