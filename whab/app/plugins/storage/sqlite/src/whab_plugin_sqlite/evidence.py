"""Reuse canonical bytes of immutable native snapshot records between commits."""

import json

from whab.plugins.storage_transfer import MAX_LOCAL_TRANSFER_BYTES, canonical_storage_json


class SnapshotEncoder:
    def __init__(self, *, ensure_ascii=False):
        self.ensure_ascii = ensure_ascii
        self.previous = {}
        self.current = {}

    def begin(self):
        self.current = {}

    def finish(self):
        self.previous = self.current
        self.current = {}

    def encode(self, value, *, limit):
        from whab.domain.errors import StoreError
        raw = self._encode(value)
        if len(raw) > limit:
            raise StoreError('Native storage state exceeds the supported bounds.')
        return raw

    def _record(self, value):
        cached = self.previous.get(id(value))
        if cached is None:
            raw = canonical_storage_json(value, limit=MAX_LOCAL_TRANSFER_BYTES)
            if self.ensure_ascii:
                raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()
            cached = (value, raw)
        self.current[id(value)] = cached
        return cached[1]

    def _encode(self, value):
        # Detached rows, append-only entries and the journal baseline are immutable.
        if type(value) is dict and 'id' in value:
            return self._record(value)
        if type(value) is dict:
            return b'{' + b','.join(self._encode(key) + b':' + (self._record(value[key]) if key == 'baseline' else self._encode(value[key]))
                                    for key in sorted(value)) + b'}'
        if type(value) is list:
            return b'[' + b','.join(self._encode(row) for row in value) + b']'
        return json.dumps(value, ensure_ascii=self.ensure_ascii, separators=(',', ':'), allow_nan=False).encode('utf-8')
