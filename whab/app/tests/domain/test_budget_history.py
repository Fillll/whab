"""Permanent financial history, safe batches and conflict-aware append-only replay."""

from copy import deepcopy

import pytest

from whab.domain import commands, history
from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state, portable_snapshot


def state():
    s = initial_state('personal', '2026-11')
    commands.add_account(s, 'checking', {'name': 'Fictional', 'type': 'depository', 'balance': 100000})
    commands.add_transaction(s, 'purchase', {'accountId': 'checking', 'date': '2026-11-02', 'merchant': 'Fictional shop', 'amount': 1200, 'categoryId': None})
    return s


def edit(s, category, *, batch='agent-batch'):
    before = deepcopy(s)
    commands.edit_transaction(s, 'purchase', category, category_agent='Codex')
    history.record_states(before, s, {'actor': 'Codex', 'actorKind': 'agent', 'batchId': batch, 'operation': 'edit_transaction'})


def apply(s, action, target):
    p = history.public_preview(s, action, target)
    before = deepcopy(s)
    result = history.apply_request(s, action, target, p['fingerprint'])
    history.record_states(before, s, {'operation': 'history', 'action': action, 'target': target})
    return result


def test_batch_undo_redo_preserves_history_and_disjoint_later_changes():
    s = state()
    edit(s, 'groceries')
    edit(s, 'dining')
    original = deepcopy(s['history']['entries'])
    commands.add_account(s, 'savings', {'name': 'Fictional saving', 'type': 'depository', 'balance': 300})
    apply(s, 'undo', 'agent-batch')
    assert s['transactions']['purchase']['categoryId'] is None
    assert 'categoryAgent' not in s['transactions']['purchase']
    assert s['history']['entries'][:2] == original and 'savings' in s['accounts']
    apply(s, 'redo', 'agent-batch')
    assert s['transactions']['purchase']['categoryId'] == 'dining'
    assert s['transactions']['purchase']['categoryAgent'] == 'Codex'
    assert len(s['history']['entries']) == 4
    history.validate_history(s['history'])


def test_conflicts_and_stale_previews_do_not_mutate_the_candidate():
    s = state()
    edit(s, 'groceries')
    p = history.public_preview(s, 'undo', 'agent-batch')
    commands.edit_transaction(s, 'purchase', 'dining')
    before = deepcopy(s)
    with pytest.raises(StoreError, match='conflict'):
        history.apply_request(s, 'undo', 'agent-batch', p['fingerprint'])
    assert s == before
    with pytest.raises(StoreError):
        history.apply_request(state(), 'snapshot', None, 'wrong')


def test_snapshot_export_restore_and_provider_barrier():
    s = state()
    before = deepcopy(s)
    apply(s, 'snapshot', None)
    saved = s['history']['entries'][-1]['id']
    edit(s, 'groceries')
    assert history.snapshot_at(s['history'], saved) == history.financial(portable_snapshot(before))
    apply(s, 'restore', saved)
    assert s['transactions']['purchase']['categoryId'] is None
    prior = deepcopy(s)
    s['accounts']['checking']['balance'] = 80000
    history.record_states(prior, s, {'operation': 'apply_sync', 'actor': 'Bank', 'actorKind': 'provider'})
    with pytest.raises(StoreError, match='provider/import'):
        history.public_preview(s, 'restore', saved)
    # History never carries the private connection state.
    s['items']['private'] = {'token': 'fictional-secret', 'cursor': 'private-cursor'}
    assert 'fictional-secret' not in str(s['history'])


def test_history_is_optional_in_legacy_portable_ledgers_and_detects_corruption():
    s = state()
    assert 'history' not in portable_snapshot(s)
    edit(s, 'groceries')
    exported = portable_snapshot(s)
    assert exported['history']['entries'][0]['actor'] == 'Codex'
    broken = deepcopy(s['history'])
    broken['entries'][0]['actor'] = 'Changed'
    with pytest.raises(StoreError, match='checksum'):
        history.validate_history(broken)
    assert history.listing(s['history'], limit=1)['entries'][0]['changeCount'] == 1
    with pytest.raises(StoreError):
        history.listing(s['history'], limit=101)


def test_removing_an_account_used_by_later_rows_and_orphaning_attachments_is_refused():
    s = state()
    before = deepcopy(s)
    commands.add_transaction(s, 'new', {'accountId': 'checking', 'date': '2026-11-03', 'merchant': 'Fictional second shop', 'amount': 100, 'categoryId': None})
    history.record_states(before, s, {'operation': 'add_transaction'})
    s['receipts']['new'] = b'fictional image'
    with pytest.raises(StoreError, match='orphan'):
        history.apply_request(s, 'undo', s['history']['entries'][-1]['id'], history.public_preview(s, 'undo', s['history']['entries'][-1]['id'])['fingerprint'])
