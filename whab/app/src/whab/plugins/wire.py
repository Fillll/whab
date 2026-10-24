"""Bounded data-only worker frames. This is a protocol, not a security sandbox."""

import json
import math
import struct
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_PAYLOAD_BYTES = 50 * 1024 * 1024
HEADER = struct.Struct("!4sHII")
_MAGIC = b"WHAB"


class WireError(ValueError):
    def __init__(self):
        super().__init__("The plugin worker sent an invalid or incompatible frame.")


def _value(value, depth=0):
    if depth > 16:
        raise WireError()
    if type(value) is dict:
        if len(value) > 100000:
            raise WireError()
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 200:
                raise WireError()
            _value(key, depth + 1)
            _value(item, depth + 1)
    elif type(value) is list:
        if len(value) > 100000:
            raise WireError()
        for item in value:
            _value(item, depth + 1)
    elif type(value) is str:
        if len(value) > 100000 or "\x00" in value:
            raise WireError()
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise WireError() from None
    elif type(value) is int:
        if abs(value) > 2**53 - 1:
            raise WireError()
    elif type(value) is float:
        if not math.isfinite(value) or abs(value) > 2**53 - 1:
            raise WireError()
    elif value is not None and type(value) is not bool:
        raise WireError()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WireError()
        result[key] = value
    return result


def frame_lengths(header):
    if type(header) is not bytes or len(header) != HEADER.size:
        raise WireError()
    magic, version, metadata_size, payload_size = HEADER.unpack(header)
    if magic != _MAGIC or version != PROTOCOL_VERSION or not 2 <= metadata_size <= MAX_METADATA_BYTES or payload_size > MAX_PAYLOAD_BYTES:
        raise WireError()
    return metadata_size, payload_size


@dataclass(frozen=True, slots=True)
class Frame:
    metadata: dict = field(repr=False)
    payload: bytes = field(default=b"", repr=False)

    def encode(self):
        if type(self.metadata) is not dict or type(self.payload) is not bytes:
            raise WireError()
        _value(self.metadata)
        raw = json.dumps(self.metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_METADATA_BYTES or len(self.payload) > MAX_PAYLOAD_BYTES:
            raise WireError()
        return HEADER.pack(_MAGIC, PROTOCOL_VERSION, len(raw), len(self.payload)) + raw + self.payload

    @classmethod
    def decode(cls, raw):
        if type(raw) is not bytes:
            raise WireError()
        metadata_size, payload_size = frame_lengths(raw[:HEADER.size])
        if len(raw) != HEADER.size + metadata_size + payload_size:
            raise WireError()
        try:
            metadata = json.loads(raw[HEADER.size:HEADER.size + metadata_size], object_pairs_hook=_unique)
            if type(metadata) is not dict:
                raise WireError()
            _value(metadata)
        except (ValueError, UnicodeError, RecursionError):
            raise WireError() from None
        return cls(metadata, raw[HEADER.size + metadata_size:])
