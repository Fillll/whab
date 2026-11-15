"""Native pairs cross the real worker, receipt, restart and saved-file boundary."""

from copy import deepcopy

import pytest

from whab.domain.accounts import normalize_account
from whab.domain.ledger_state import initial_state, portable_snapshot
from whab.plugins import LedgerService, RequestConflict, RevisionConflict, StorageContext
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker


def fixture_state(count=100):
    state = initial_state('personal', '2026-11')
    state['accounts'] = {name: normalize_account({
        'id': name, 'name': '旅行 ' + name, 'type': 'depository', 'balance': 100000,
        'currency': 'USD', 'source': 'manual', 'institution': '', 'stale': False,
        'balanceUnknown': False,
    }) for name in ('a', 'b')}
    state['transactions'] = {}
    for index in range(count):
        for suffix, account, sign in [('out', 'a', 1), ('in', 'b', -1)]:
            identifier = f'{suffix}-{index}'
            state['transactions'][identifier] = {
                'id': identifier, 'accountId': account, 'date': '2026-11-01',
                'merchant': '  Fictional 商店  ', 'merchantLogo': None,
                'amount': sign * (100 + index), 'categoryId': None, 'pending': False,
                'reviewed': False, 'source': 'manual', 'note': '[YNAB]  \nPreserved',
                'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': True,
                'deleted': False, 'categoryAgent': 'Earlier agent',
            }
    return state


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_hundred_pairs_one_commit_save_exact_restart_receipt_and_restored_budget(kind, tmp_path):
    context = StorageContext(tmp_path / 'fictional', 'sandbox', {})
    intents = tmp_path / 'intents'
    store = StorageWorkerProxy(StorageWorker(kind), context, intents)
    pairs = [{'outId': f'out-{index}', 'inId': f'in-{index}'} for index in range(100)]
    saves = []
    try:
        store.restore_budget('personal', portable_snapshot(fixture_state()))
        service = LedgerService(store, budget_id='fictional', on_commit=lambda: saves.append(store.export_budget_files()))
        untouched = deepcopy(store.export_budget('personal'))
        policy = service.query('transfer_policy', 'personal')['data']
        assert policy['maxPairs'] == 100 and policy['twoLegsRequired']
        preview = service.query('preview_transfer_matches', 'personal', parameters={'pairs': pairs, 'reviewed': True})
        assert store.export_budget('personal') == untouched and saves == []
        payload = {'pairs': pairs, 'reviewed': True, 'fingerprint': preview['data']['fingerprint']}
        args = {'request_id': 'fictional-bulk', 'expected_revision': preview['revision'],
                'history': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-pairs'}}
        generation = store.commit_evidence().generation
        first = service.command('match_transfers', 'personal', payload, **args)
        assert first['data'] == preview['data']
        assert len(saves) == 1 and store.commit_evidence().generation == generation + 1
        assert service.command('match_transfers', 'personal', payload, **args) == first
        assert len(saves) == 1 and store.commit_evidence().generation == generation + 1
        with pytest.raises(RequestConflict):
            service.command('match_transfers', 'personal', {**payload, 'reviewed': False}, **args)
        with pytest.raises(RevisionConflict):
            service.command('match_transfers', 'personal', payload, **{**args, 'request_id': 'new-stale'})
        saved = store.export_budget('personal')
        assert all(row['reviewed'] and row['transferPeerId'] for row in saved['transactions'])
        assert saved['accounts'] == untouched['accounts']
        assert saved['assignments'] == untouched['assignments']
        assert len(first['data']['changes']) == 200
        assert all(row['merchant'] == '  Fictional 商店  ' and row['note'] == '[YNAB]  \nPreserved' for row in saved['transactions'])
        history = store.budget_history('personal')['entries'][0]
        assert history['actor'] == 'Codex' and history['batchId'] == 'fictional-pairs'
    finally:
        store.close()
    reopened = StorageWorkerProxy(StorageWorker(kind), context, intents)
    restored = StorageWorkerProxy(StorageWorker(kind), StorageContext(tmp_path / 'empty', 'sandbox', {}), tmp_path / 'empty-intents')
    try:
        service = LedgerService(reopened, budget_id='fictional', on_commit=lambda: saves.append(None))
        assert service.command('match_transfers', 'personal', payload, **args) == first
        assert len(saves) == 1 and reopened.commit_evidence().generation == generation + 1
        restored.restore_budget_files('personal', saves[0])
        assert restored.export_budget('personal')['transactions'] == saved['transactions']
        for action in ('undo', 'redo'):
            plan = service.query('history_preview', 'personal', parameters={'action': action, 'target': history['id']})
            service.command('history_apply', 'personal', {key: plan['data'][key] for key in ('action', 'target', 'fingerprint')}, request_id='fixture-' + action, expected_revision=plan['revision'])
        assert reopened.export_budget('personal')['transactions'] == saved['transactions']
    finally:
        reopened.close()
        restored.close()
