"""Tagged transport framing must not consume the logical payload depth budget."""

import json

import pytest

from whab.plugins import StoreError
from whab.plugins.storage_transfer import canonical_storage_json, read_storage_json
from whab.plugins.storage_worker_codec import (
    HEADER,
    MAX_FRAMING_DEPTH,
    MAX_VALUE_DEPTH,
    STORAGE_PROTOCOL,
    decode,
    encode,
)
from whab.plugins.wire import WireError


def test_exported_history_with_original_financial_evidence_roundtrips():
    response = {'result': {'history': {'entries': [{'changes': [{
        'path': ['transactions', 'fictional'],
        'before': {'amount': 10000},
        'after': {'amount': 8000, 'original': {'amount': 10000}},
    }]}]}}}
    assert decode(encode(response)) == response


def test_maximum_logical_dictionary_depth_roundtrips_but_extra_level_fails():
    value = 'fictional'
    for _ in range(MAX_VALUE_DEPTH):
        value = {'record': value}
    assert decode(encode(value)) == value
    with pytest.raises(WireError):
        encode({'extra': value})


def test_untrusted_framing_cannot_bypass_logical_or_json_depth_bounds():
    framed = ['value', None]
    for _ in range(MAX_VALUE_DEPTH + 1):
        framed = ['list', [framed]]
    raw = json.dumps(framed).encode()
    with pytest.raises(WireError):
        decode(HEADER.pack(b'WHST', STORAGE_PROTOCOL, len(raw), 0) + raw)
    raw = b'[' * (MAX_FRAMING_DEPTH + 1) + b']' * (MAX_FRAMING_DEPTH + 1)
    with pytest.raises(StoreError):
        decode(HEADER.pack(b'WHST', STORAGE_PROTOCOL, len(raw), 0) + raw)


def test_ordinary_operation_and_receipt_json_retains_existing_depth_limit():
    value = None
    for _ in range(25):
        value = [value]
    with pytest.raises(StoreError):
        canonical_storage_json(value)
    with pytest.raises(StoreError):
        read_storage_json(json.dumps(value).encode())
