"""Version-2 logical hashes with typed, ordered, length-bound Merkle nodes.

This is a data format, not an authenticity signature. Untrusted data must still
pass the owning schema validator. Cache users must supply immutable snapshots
and exact changed indexes from a verified native transaction.
"""

import json
from hashlib import sha256

from .errors import StoreError

_EMPTY = sha256(b'whab-merkle-v2:empty').digest()


def _combine(left, right):
    return sha256(b'\x05' + left + right).digest()


class SequenceTree:
    """A complete binary tree; point changes touch only their ancestor path."""

    def __init__(self, values=()):
        self.count = len(values)
        self.capacity = 1 << (max(1, self.count) - 1).bit_length()
        self.nodes = [_EMPTY] * (2 * self.capacity)
        self.nodes[self.capacity:self.capacity + self.count] = values
        for index in range(self.capacity - 1, 0, -1):
            self.nodes[index] = _combine(self.nodes[index * 2], self.nodes[index * 2 + 1])

    def set(self, index, value):
        node = self.capacity + index
        self.nodes[node] = value
        while node > 1:
            node //= 2
            self.nodes[node] = _combine(self.nodes[node * 2], self.nodes[node * 2 + 1])

    def append(self, value):
        if self.count == self.capacity:
            old = self.nodes[self.capacity:self.capacity + self.count]
            self.__init__([*old, value])
        else:
            self.set(self.count, value)
            self.count += 1

    @property
    def root(self):
        return self.nodes[1]


def _raw(value):
    if value is not None and type(value) not in {str, bool, int}:
        raise StoreError('Invalid logical integrity value.')
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


class MerkleHasher:
    """Caches only explicitly immutable nodes and selected or appended arrays.

    ``selections`` maps complete list paths to changed indexes. Omitted paths
    rebuild, except identical list objects and append-only history entry lists.
    Discard this object after untracked writes, rollback or schema changes.
    """

    def __init__(self):
        self.arrays = {}
        self.immutable = {}
        self.selections = {}

    def digest(self, value, *, selections=None, limit=None):
        self.selections = selections or {}
        value, size = self._node(value, ())
        if limit is not None and size > limit:
            raise StoreError('Native storage state exceeds the supported bounds.')
        return value.hex()

    def _node(self, value, path):
        if path and path[-1] == 'baseline':
            cached = self.immutable.get(path)
            if cached is not None and cached[0] is value:
                return cached[1]
            result = self._value(value, path)
            self.immutable[path] = (value, result)
            return result
        return self._value(value, path)

    def _value(self, value, path):
        if type(value) is dict:
            if 'id' in value:
                raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
                return sha256(b'\x06' + raw).digest(), len(raw)
            if any(type(key) is not str for key in value):
                raise StoreError('Invalid logical integrity key.')
            nodes, size = [], 2 + max(0, len(value) - 1)
            for key in sorted(value):
                raw = _raw(key)
                child, length = self._node(value[key], (*path, key))
                nodes.append(sha256(b'\x04' + len(raw).to_bytes(8, 'big') + raw + child).digest())
                size += len(raw) + 1 + length
            return sha256(b'\x03' + len(nodes).to_bytes(8, 'big') + SequenceTree(nodes).root).digest(), size
        if type(value) in {list, tuple}:
            return self._array(value, path)
        raw = _raw(value)
        return sha256(b'\x01' + raw).digest(), len(raw)

    def _array(self, rows, path):
        cached = self.arrays.get(path)
        if cached is not None and cached[0] is rows:
            return cached[4]
        selected = self.selections.get(path)
        append = (cached is not None and path[-2:] == ('history', 'entries')
                  and len(rows) >= len(cached[0])
                  and (not cached[0] or rows[len(cached[0]) - 1] is cached[0][-1]))
        if cached is not None and (selected is not None and len(rows) == len(cached[0]) or append):
            _, tree, lengths, total, _ = cached
            indexes = range(len(cached[0]), len(rows)) if append else selected
            for index in indexes:
                child, size = self._node(rows[index], (*path, index))
                if index == tree.count:
                    tree.append(child)
                    lengths.append(size)
                    total += size
                else:
                    tree.set(index, child)
                    total += size - lengths[index]
                    lengths[index] = size
        else:
            children = [self._node(row, (*path, index)) for index, row in enumerate(rows)]
            tree = SequenceTree([child for child, _ in children])
            lengths = [size for _, size in children]
            total = sum(lengths)
        result = sha256(b'\x02' + len(rows).to_bytes(8, 'big') + tree.root).digest(), total + 2 + max(0, len(rows) - 1)
        self.arrays[path] = (rows, tree, lengths, total, result)
        return result


def merkle_digest(value):
    """Independently recompute a version-2 hash without trusting cached nodes."""
    return MerkleHasher().digest(value)
