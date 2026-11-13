"""Transfer links cross the real public worker boundary and survive exact retries."""

from copy import deepcopy

import pytest

from whab.plugins import LedgerService, StorageContext
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_pair_commit_exact_restart_retry_and_saved_restore(kind, tmp_path):
    context = StorageContext(tmp_path / 'fictional', 'sandbox', {})
    root = tmp_path / 'intents'
    store = StorageWorkerProxy(StorageWorker(kind), context, root)
    try:
        a = store.add_account('personal', {'name': '旅行 🍏', 'type': 'depository', 'balance': 1000})
        b = store.add_account('personal', {'name': 'Сбережения', 'type': 'depository', 'balance': 1000})
        ids = [store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional', 'amount': amount, 'categoryId': None, 'note': '[YNAB]  '}) for account, amount in [(a, 100), (b, -100)]]
        service = LedgerService(store, budget_id='fictional')
        preview = service.query('preview_transfer_match', 'personal', parameters={'transactionIds': ids, 'action': 'match'})
        payload = {'transactionIds': ids, 'action': 'match', 'fingerprint': preview['data']['fingerprint']}
        args = {'request_id': 'fictional-exact-retry', 'expected_revision': preview['revision'], 'history': {'actor': 'Codex', 'actorKind': 'agent'}}
        generation = store.commit_evidence().generation
        first = service.command('match_transfer', 'personal', payload, **args)
        assert store.commit_evidence().generation == generation + 1
        saved = deepcopy(store.transactions('personal'))
        files = store.export_budget_files()
    finally:
        store.close()
    reopened = StorageWorkerProxy(StorageWorker(kind), context, root)
    restored = StorageWorkerProxy(StorageWorker(kind), StorageContext(tmp_path / 'empty', 'sandbox', {}), tmp_path / 'empty-intents')
    try:
        service = LedgerService(reopened, budget_id='fictional')
        replay = service.command('match_transfer', 'personal', payload, **args)
        assert replay == first
        assert reopened.commit_evidence().generation == generation + 1
        assert reopened.transactions('personal') == saved
        assert sum(row['requestId'] == args['request_id'] for row in reopened.budget_history('personal')['entries']) == 1
        restored.restore_budget_files('personal', files)
        assert restored.transactions('personal') == saved
        assert service.query('transfer_candidates', 'personal', parameters={})['data']['total'] == 0
    finally:
        reopened.close()
        restored.close()
