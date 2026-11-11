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
    previous = deepcopy(s)
    commands.add_account(s, 'savings', {'name': 'Fictional saving', 'type': 'depository', 'balance': 300})
    history.record_states(previous, s, {'operation':'add_account'})
    apply(s, 'undo', 'agent-batch')
    assert s['transactions']['purchase']['categoryId'] is None
    assert 'categoryAgent' not in s['transactions']['purchase']
    assert s['history']['entries'][:2] == original and 'savings' in s['accounts']
    apply(s, 'redo', 'agent-batch')
    assert s['transactions']['purchase']['categoryId'] == 'dining'
    assert s['transactions']['purchase']['categoryAgent'] == 'Codex'
    assert len(s['history']['entries']) == 5
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


def test_checkpoint_pagination_and_long_history_do_not_prune_old_entries():
    snapshot = history.financial(portable_snapshot(state()))
    journal = None
    for _ in range(1000):
        journal = history.append(journal, snapshot, snapshot, {'action':'snapshot'})
    assert len(journal['entries']) == 1000
    first = journal['entries'][0]['id']
    page = history.listing(journal, limit=1)
    assert page['total'] == 1000 and page['next']
    assert history.listing(journal, limit=1, before=page['next'])['total'] == 1000
    assert history.snapshot_at(journal, first) == snapshot
    history.validate_history(journal)
    with pytest.raises(StoreError):
        history.listing(journal, before='missing')
    with pytest.raises(StoreError):
        history.snapshot_at(journal, 'missing')


@pytest.mark.parametrize('corrupt', [None, {}, {'formatVersion':1,'baseline':None,'entries':[]}, {'formatVersion':True,'baseline':{},'entries':[]}])
def test_invalid_history_shapes_have_authored_errors(corrupt):
    with pytest.raises(StoreError):
        history.validate_history(corrupt)


def test_history_cannot_hide_unknown_original_fields_or_forged_state_chain():
    s = state()
    edit(s,'groceries')
    entry = s['history']['entries'][0]
    for kind in ['original', 'chain']:
        broken = deepcopy(s['history'])
        row = broken['entries'][0]
        if kind == 'original':
            row['changes'][0]['after']['original'] = {'accessToken':'fictional-secret'}
        else:
            row['afterDigest'] = 'f' * 64
        row['hash'] = history.digest({k:v for k,v in row.items() if k != 'hash'})
        with pytest.raises(StoreError):
            history.validate_history(broken)
    assert s['history']['entries'][0] == entry


def test_undo_cannot_detach_an_account_referenced_by_current_bank_settings():
    s = initial_state('personal', '2026-11')
    before = deepcopy(s)
    commands.add_account(s, 'linked', {'name':'Fictional linked','type':'depository','balance':100})
    history.record_states(before, s, {'operation':'add_account'})
    s['items']['fictional'] = {'syncSettings':{'provider-account':{'accountId':'linked','since':'2026-11-01'}}}
    identifier = s['history']['entries'][-1]['id']
    before = deepcopy(s)
    with pytest.raises(StoreError, match='linked bank account'):
        history.public_preview(s, 'undo', identifier)
    assert s == before


def test_full_restore_refuses_unrecorded_import_or_external_financial_changes():
    s = state()
    apply(s, 'snapshot', None)
    saved = s['history']['entries'][-1]['id']
    s['accounts']['checking']['balance'] += 100
    before = deepcopy(s)
    with pytest.raises(StoreError, match='Unrecorded import or external changes'):
        history.public_preview(s, 'restore', saved)
    assert s == before
    # A new explicit snapshot records the adopted state as a barrier and keeps
    # both historical exports available without crossing that barrier.
    apply(s, 'snapshot', None)
    assert s['history']['entries'][-2]['operation'] == 'import_workspace'
    with pytest.raises(StoreError, match='provider/import'):
        history.public_preview(s, 'restore', saved)


@pytest.mark.parametrize('action', ['undo', 'redo'])
def test_planning_reversal_cannot_overcommit_funds_used_by_later_allocations(action):
    s = initial_state('personal','2026-11')
    commands.add_account(s,'cash',{'name':'Fictional','type':'depository','balance':10000})
    def assign(category, value):
        before = deepcopy(s)
        commands.assign(s,category,'2026-11',value,as_of_month='2026-11')
        history.record_states(before,s,{'operation':'assign'})
        return s['history']['entries'][-1]['id']
    original = assign('groceries',10000)
    if action == 'undo':
        target = assign('groceries',0)
    else:
        target = original
        apply(s,'undo',target)
    assign('dining',10000)
    before = deepcopy(s)
    with pytest.raises(StoreError, match='Later financial changes'):
        history.public_preview(s,action,target)
    assert s == before
    apply(s,'restore',original)
    assert s['budget']['2026-11'] == {'groceries':10000}


def test_sorted_native_change_hints_keep_exact_history_digests_and_noop_semantics():
    before = history.financial(portable_snapshot(state()))
    after = deepcopy(before)
    after['transactions'][0]['note'] = 'Fictional bounded edit'
    changes = history.difference(before, after)
    journal = history.record_financial(None, before, after, {'operation': 'edit_transaction'},
                                       changes=changes, transactions_sorted=True)
    assert journal['entries'][0]['beforeDigest'] == history.digest(before)
    assert journal['entries'][0]['afterDigest'] == history.digest(after)
    history.validate_history(journal)
    assert history.record_financial(journal, after, after, changes=[], transactions_sorted=True) is journal
