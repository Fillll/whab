"""Reuse canonical bytes of immutable native snapshot records between commits."""

import json

from whab.plugins.storage_transfer import MAX_LOCAL_TRANSFER_BYTES, canonical_storage_json


class SnapshotEncoder:
    def __init__(self, *, ensure_ascii=False):
        self.ensure_ascii = ensure_ascii
        self.previous = {}
        self.current = {}
        self.arrays = {}
        self.selection = None
        self.scope = None

    def begin(self, *, transaction_ids=None, scope=None):
        self.current = {}
        self.selection = transaction_ids
        self.scope = scope

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

    def _transactions(self, rows, path):
        key = (self.scope, path)
        cached = self.arrays.get(key)
        if self.selection is None or cached is None or cached['length'] != len(rows):
            positions = {row['id']: i // 512 for i, row in enumerate(rows)}
            chunks = [b','.join(self._record(row) for row in rows[i:i + 512]) for i in range(0, len(rows), 512)]
        else:
            positions, chunks = cached['positions'], list(cached['chunks'])
            changed = {positions[identifier] for identifier in self.selection if identifier in positions}
            if not changed:
                return cached['raw']
            for index in changed:
                chunks[index] = b','.join(self._record(row) for row in rows[index * 512:(index + 1) * 512])
        raw = b'[' + b','.join(chunks) + b']'
        self.arrays[key] = {'positions': positions, 'chunks': chunks, 'raw': raw, 'length': len(rows)}
        return raw

    def _encode(self, value, path=()):
        # Detached rows, append-only entries and the journal baseline are immutable.
        if type(value) is dict and 'id' in value:
            return self._record(value)
        if type(value) is dict:
            parts = []
            for key in sorted(value):
                if key == 'baseline':
                    raw = self._record(value[key])
                elif key == 'transactions' and type(value[key]) is list:
                    raw = self._transactions(value[key], path + (key,))
                else:
                    raw = self._encode(value[key], path + (key,))
                parts.append(self._encode(key) + b':' + raw)
            return b'{' + b','.join(parts) + b'}'
        if type(value) is list:
            return b'[' + b','.join(self._encode(row, path) for row in value) + b']'
        return json.dumps(value, ensure_ascii=self.ensure_ascii, separators=(',', ':'), allow_nan=False).encode('utf-8')
