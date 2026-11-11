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
        return b''.join(self.chunks(value, limit=limit))

    def chunks(self, value, *, limit):
        from whab.domain.errors import StoreError
        chunks = tuple(self._chunks(value))
        if sum(map(len, chunks)) > limit:
            raise StoreError('Native storage state exceeds the supported bounds.')
        return chunks

    def _record(self, value):
        cached = self.previous.get(id(value))
        if cached is None:
            raw = canonical_storage_json(value, limit=MAX_LOCAL_TRANSFER_BYTES)
            if self.ensure_ascii and not raw.isascii():
                raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()
            cached = (value, raw)
        self.current[id(value)] = cached
        return cached[1]

    def _transactions(self, rows, path):
        key = (self.scope, path)
        cached = self.arrays.get(key)
        if self.selection is None or cached is None or cached['length'] != len(rows):
            positions = {row['id']: i // 64 for i, row in enumerate(rows)}
            chunks = [b','.join(self._record(row) for row in rows[i:i + 64]) for i in range(0, len(rows), 64)]
        else:
            positions, chunks = cached['positions'], list(cached['chunks'])
            changed = {positions[identifier] for identifier in self.selection if identifier in positions}
            for index in changed:
                chunks[index] = b','.join(self._record(row) for row in rows[index * 64:(index + 1) * 64])
        self.arrays[key] = {'positions': positions, 'chunks': chunks, 'length': len(rows)}
        parts = [b'[']
        for i, chunk in enumerate(chunks):
            if i:
                parts.append(b',')
            parts.append(chunk)
        return (*parts, b']')

    def _chunks(self, value, path=()):
        # Detached rows, append-only entries and the journal baseline are immutable.
        if type(value) is dict and 'id' in value:
            yield self._record(value)
        elif type(value) is dict:
            yield b'{'
            for i, key in enumerate(sorted(value)):
                if i:
                    yield b','
                yield json.dumps(key, ensure_ascii=self.ensure_ascii).encode('utf-8') + b':'
                if key == 'baseline':
                    yield self._record(value[key])
                elif key == 'transactions' and type(value[key]) is list:
                    yield from self._transactions(value[key], path + (key,))
                else:
                    yield from self._chunks(value[key], path + (key,))
            yield b'}'
        elif type(value) is list:
            yield b'['
            for i, row in enumerate(value):
                if i:
                    yield b','
                yield from self._chunks(row, path)
            yield b']'
        else:
            yield json.dumps(value, ensure_ascii=self.ensure_ascii, separators=(',', ':'), allow_nan=False).encode('utf-8')
