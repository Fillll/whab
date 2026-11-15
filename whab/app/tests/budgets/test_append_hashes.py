"""Append saves keep ordinary SHA checksums without rehashing snapshot bytes."""

import hashlib

import pytest

from whab.budgets.file_changes import apply_file_changes
from whab.budgets.session import BudgetSession
from whab.plugins import StoreError


def test_appended_payload_hashes_only_new_bytes_and_matches_independent_sha(monkeypatch):
    session = object.__new__(BudgetSession)
    session._payload_hashes = {}
    base = b'fictional snapshot' * 100000
    original = hashlib.sha256
    processed = []

    class CountingHash:
        def __init__(self, data=b'', state=None):
            self.state = original(data) if state is None else state
            processed.append(len(data))

        def update(self, data):
            processed.append(len(data))
            self.state.update(data)

        def copy(self):
            return CountingHash(state=self.state.copy())

        def hexdigest(self):
            return self.state.hexdigest()

    monkeypatch.setattr(hashlib, 'sha256', CountingHash)
    assert session._payload_digest('one', base) == original(base).hexdigest()
    processed.clear()
    for suffix in (b'change one', b'change two', b''):
        appended = base + suffix
        assert session._payload_digest('one', appended) == original(appended).hexdigest()
        base = appended
    assert sum(processed) == len(b'change onechange two')
    for changed in (b'x' + base[1:], base[:100], b''):
        assert session._payload_digest('one', changed) == original(changed).hexdigest()
    assert session._payload_digest('two', base) == original(base).hexdigest()


def test_corrupt_append_is_rejected_even_after_verifying_another_candidate():
    session = object.__new__(BudgetSession)
    session._payload_hashes = {}
    base = b'fictional base'
    expected = base + b'acknowledged edit'
    digest = session._payload_digest
    old = digest('budget.sqlite', base)
    new = digest('budget.sqlite', expected)
    patch = {'name': 'budget.sqlite', 'base': old, 'digest': new,
             'size': len(expected), 'chunks': [[len(base), b'corrupted edit!!X']]}
    with pytest.raises(StoreError, match='digest does not match'):
        apply_file_changes({'budget.sqlite': base}, {'patches': [patch]}, digest_file=digest)
    patch['chunks'] = [[len(base), b'acknowledged edit']]
    assert apply_file_changes({'budget.sqlite': base}, {'patches': [patch]}, digest_file=digest) == {'budget.sqlite': expected}
