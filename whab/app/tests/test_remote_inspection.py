"""Capability/preflight reads never silently refresh a remote financial intent."""

import json

import httpx
import pytest

from whab.plugins import LedgerService, PreflightInspection, StoreError
from whab.plugins.remote_transport import RemoteStore

POLICIES = {
    'transaction_void_policy': {'version': 1, 'maxTransactions': 100, 'durableTombstones': True},
    'opening_balance_policy': {'version': 1, 'categoryType': 'opening_balance',
                               'zeroAmountReview': True, 'preservesBalances': True},
    'review_policy': {'version': 1, 'classificationRequired': True},
    'transaction_change_policy': {'version': 1, 'maxChanges': 100, 'preserveBalances': True},
    'transfer_policy': {'version': 1, 'twoLegsRequired': True, 'maxPairs': 100},
}


def response(data, revision='r2'):
    return httpx.Response(200, json={'budgetId': 'fictional', 'revision': revision, 'data': data})


def remote(handler):
    return RemoteStore('https://fictional.example', 'fictional-' + 'x' * 32, 'fictional',
                       transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('method,operation', [
    ('supports_transaction_voiding', 'transaction_void_policy'),
    ('supports_opening_balances', 'opening_balance_policy'),
    ('supports_classified_review', 'review_policy'),
    ('supports_atomic_transaction_changes', 'transaction_change_policy'),
    ('supports_transfer_integrity', 'transfer_policy'),
    ('supports_bulk_transfer_matching', 'transfer_policy'),
])
@pytest.mark.parametrize('observed', [None, 'r1'])
@pytest.mark.parametrize('outcome', ['supported', 'unsupported', 'conflict'])
def test_policy_probes_preserve_observed_revision_on_all_outcomes(method, operation, observed, outcome):
    def authority(request):
        assert request.method == 'GET' and request.url.path.endswith(operation)
        if outcome == 'conflict':
            return httpx.Response(409)
        return response(POLICIES[operation] if outcome == 'supported' else {'version': 0})
    store = remote(authority)
    try:
        store._revision = observed
        if outcome == 'conflict':
            with pytest.raises(StoreError, match='no mutation was submitted'):
                getattr(store, method)()
        else:
            assert getattr(store, method)() is (outcome == 'supported')
        assert store._revision == observed
    finally:
        store.close()


def test_inspection_is_nested_exception_safe_and_explicit_reads_still_refresh():
    store: PreflightInspection = remote(lambda _: response([]))
    try:
        store._revision = 'r1'
        with pytest.raises(RuntimeError, match='fictional'):
            with store.inspection_scope():
                store.accounts('personal')
                assert store._revision == 'r2'
                with store.inspection_scope():
                    store._revision = 'r3'
                assert store._revision == 'r2'
                raise RuntimeError('fictional inspection failure')
        assert store._revision == 'r1'
        store.accounts('personal')
        assert store._revision == 'r2'
    finally:
        store.close()


@pytest.mark.parametrize('opening_policy', ['supported', 'missing', 'unsupported'])
@pytest.mark.parametrize('transfer_policy', ['supported', 'missing', 'unsupported'])
@pytest.mark.parametrize('operation', ['update_account', 'history_apply'])
def test_host_preflight_cannot_replace_r1_with_r2_before_a_remote_mutation(opening_policy, transfer_policy, operation):
    requests, submitted, committed = [], [], []
    def authority(request):
        requests.append(request.url.path)
        if request.url.path.endswith('/commands'):
            intent = json.loads(request.content)
            submitted.append(intent)
            if intent['expected_revision'] != 'r2':
                return httpx.Response(409)
            committed.append(intent)
            return response(None, 'r3')
        name = request.url.path.rsplit('/', 1)[-1]
        if name == 'accounts':
            return response([{'id': 'cash', 'name': 'Observed account'}], 'r1')
        if name == 'opening_balance_policy' and opening_policy != 'supported':
            return httpx.Response(404) if opening_policy == 'missing' else response({'version': 0})
        if name == 'transfer_policy' and transfer_policy != 'supported':
            return httpx.Response(404) if transfer_policy == 'missing' else response({'version': 0})
        if name in POLICIES:
            return response(POLICIES[name])
        if name in {'categories', 'transactions'}:
            return response([])
        assert name == 'queries'
        assert json.loads(request.content)['operation'] == 'history_preview'
        return response({'changes': []})
    store = remote(authority)
    try:
        service = LedgerService(store, budget_id='fictional')
        observed = service.query('accounts', 'personal')
        payload = ({'accountId': 'cash', 'name': 'New name'} if operation == 'update_account'
                   else {'action': 'redo', 'target': 'fictional-history', 'fingerprint': 'a' * 64})
        with pytest.raises(StoreError, match='Remote budget changed'):
            service.command(operation, 'personal', payload,
                            request_id='fictional-intent', expected_revision=observed['revision'])
        assert len(submitted) == 1 and submitted[0]['expected_revision'] == 'r1'
        assert not committed and service.revision == observed['revision']
        if opening_policy != 'supported' or transfer_policy != 'supported':
            assert any(path.endswith('/categories') for path in requests)
            if operation == 'history_apply':
                assert any(path.endswith('/queries') for path in requests)
        if transfer_policy != 'supported':
            assert any(path.endswith('/transactions') for path in requests)
        assert any(path.endswith('/transfer_policy') for path in requests)
    finally:
        store.close()
