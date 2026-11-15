"""Versioned hashes bind types/order and update selected immutable records."""

from copy import deepcopy

import pytest

from whab.domain import integrity
from whab.domain.errors import StoreError


def test_merkle_types_order_keys_and_unicode_are_unambiguous():
    values = [None, False, 0, '', [], {}, [1, 2], [2, 1], {'a': 1}, {'a': '1'}, ['ä'], ['a']]
    assert len({integrity.merkle_digest(value) for value in values}) == len(values)
    assert integrity.merkle_digest({'b': 2, 'a': 1}) == integrity.merkle_digest({'a': 1, 'b': 2})
    for value in (1.5, b'value', {1: 'value'}):
        with pytest.raises(StoreError):
            integrity.merkle_digest(value)


def test_selected_edit_hash_matches_independent_rebuild_and_touches_logarithmic_path(monkeypatch):
    rows = [{'id': f'fictional-{index}', 'note': ''} for index in range(8192)]
    state = {'transactions': rows, 'history': {'baseline': {'transactions': deepcopy(rows)}, 'entries': []}}
    hasher = integrity.MerkleHasher()
    before = hasher.digest(state)
    counts = []
    original = integrity._combine
    monkeypatch.setattr(integrity, '_combine', lambda a, b: (counts.append(1), original(a, b))[1])
    changed = rows.copy()
    changed[4000] = {**rows[4000], 'note': 'fictional selected edit'}
    result = hasher.digest({**state, 'transactions': changed}, selections={('transactions',): {4000}})
    assert result != before and len(counts) < 32
    monkeypatch.setattr(integrity, '_combine', original)
    assert result == integrity.merkle_digest({**state, 'transactions': changed})
    assert hasher.digest({**state, 'transactions': changed}, selections={('transactions',): set()}) == result
    with pytest.raises(StoreError, match='bounds'):
        hasher.digest(state, limit=1)


def test_append_tree_grows_and_reordered_arrays_rebuild():
    hasher = integrity.MerkleHasher()
    entries = []
    for index in range(20):
        entries = [*entries, {'id': str(index), 'data': 'fictional'}]
        value = {'history': {'entries': entries}}
        assert hasher.digest(value) == integrity.merkle_digest(value)
    value = {'history': {'entries': list(reversed(entries))}}
    assert hasher.digest(value) == integrity.merkle_digest(value)
    value = {'history': {'entries': []}}
    assert hasher.digest(value) == integrity.merkle_digest(value)


def test_mixed_history_preserves_old_hashes_and_rejects_transition_tampering():
    from whab.domain import commands, history
    from whab.domain.ledger_state import initial_state, portable_snapshot

    state = initial_state('personal', '2026-11')
    commands.add_account(state, 'fictional-account', {'name': 'Fictional cash', 'type': 'depository', 'balance': 0})
    commands.add_transaction(state, 'fictional-tx', {'accountId': 'fictional-account', 'date': '2026-11-01', 'merchant': 'Fictional shop', 'amount': 100})
    baseline = portable_snapshot(state)
    commands.edit_transaction(state, 'fictional-tx', 'groceries')
    first = portable_snapshot(state)
    journal = history.record_financial(None, baseline, first, {'operation': 'edit_transaction'})
    original = deepcopy(journal['entries'][0])
    commands.edit_transaction(state, 'fictional-tx', 'dining')
    second = portable_snapshot(state)
    journal = history.record_financial(journal, first, second, {'operation': 'edit_transaction'}, digest_version=2)
    assert journal['formatVersion'] == 2 and journal['entries'][0] == original
    assert journal['entries'][1]['digestVersion'] == 2
    history.validate_history(journal)
    appended = history.replay_portable_entries({**first, 'history': {'formatVersion': 1, 'baseline': baseline, 'entries': [original]}}, [journal['entries'][1]])
    assert appended['transactions'] == second['transactions']
    assert appended['categories'] == second['categories']
    history.preview(journal, second, 'restore', 'baseline')
    broken = deepcopy(journal)
    broken['entries'][1]['beforeDigest'] = original['afterDigest']
    broken['entries'][1]['hash'] = history.digest({key: value for key, value in broken['entries'][1].items() if key != 'hash'})
    with pytest.raises(StoreError, match='state chain'):
        history.validate_history(broken)
    broken = deepcopy(journal)
    broken['entries'][1]['digestVersion'] = 3
    with pytest.raises(StoreError, match='integrity version'):
        history.validate_history(broken)
    with pytest.raises(StoreError):
        history.state_digest(second, 3)


def test_verified_seed_preserves_source_tree_and_requires_exact_content():
    rows = [{'id': str(i), 'note': ''} for i in range(100)]
    source = integrity.MerkleHasher()
    source_value = {'transactions': rows}
    source_root = source.digest(source_value)
    target = integrity.MerkleHasher()
    copied = deepcopy(rows)
    assert target.seed_array(('transactions',), copied, source, ('transactions',))
    changed = copied.copy()
    changed[50] = {**copied[50], 'note': 'fictional edit'}
    actual = target.digest({'transactions': changed}, selections={('transactions',): {50}})
    assert actual == integrity.merkle_digest({'transactions': changed})
    assert source.digest(source_value) == source_root
    assert not target.seed_array(('transactions',), changed, source, ('transactions',))
    assert not target.seed_array(('missing',), rows, source, ('missing',))
    baseline = deepcopy(source_value)
    assert target.seed_immutable(('history', 'baseline'), baseline, source.last)
    assert target.digest({'history': {'baseline': baseline}}) == integrity.merkle_digest({'history': {'baseline': baseline}})
    assert not target.seed_immutable(('history', 'baseline'), {'changed': True}, source.last)
    assert not target.seed_immutable(('history', 'baseline'), baseline, None)
