"""Fictional provider flows, real native receipts and controlled cancellation."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from whab.budgets.runtime import BudgetRuntime, RuntimeCandidate
from whab.budgets.session import TrackedStorage
from whab.plugins import BudgetContext, PluginError, StorageContext, load_plugins
from whab.plugins.bank_adapter import ProviderWebhook
from whab.plugins.effect_store import EffectError
from whab.plugins.storage import close_storage, open_storage
from whab.services.bank_runtime import BankCoordinator, LeasedBanks
from whab.storage.bank import BankStore


class Provider:
    config = {'environment': 'sandbox', 'configured': True, 'liveEnabled': True, 'missingConfig': []}

    def __init__(self):
        self.calls = []
        self.gate = None
        self.failure = None
        self.closed = False

    def call(self, command, **values):
        self.calls.append((command, values))
        if self.gate and command == 'exchange_token':
            self.gate[0].set()
            assert self.gate[1].wait(10)
        if self.failure and command == self.failure[0]:
            raise self.failure[1]
        if command == 'connection_token':
            return 'fictional-link-token'
        if command == 'exchange_token':
            return {'item_id': 'fictional-item', 'access_token': 'fictional-private-access-token'}
        if command == 'read_sync':
            return {'changes': {'added': [], 'modified': [], 'removed': []},
                    'cursor': 'fictional-next-cursor', 'accounts': [], 'historical_complete': True}
        if command == 'webhook_event':
            return ProviderWebhook(True, item_id='fictional-item', reconnect=True)
        assert command == 'remove_token'
        return None

    def close(self):
        self.closed = True


@pytest.fixture(params=['sqlite', 'json'])
def setup(tmp_path, request):
    plugin = load_plugins((request.param,)).storage_plugin(request.param)
    backend = open_storage(plugin, StorageContext(tmp_path / 'native', 'sandbox', {},
        budget=BudgetContext('fictional-budget', {}, {}, lambda _: None)))
    tracked = TrackedStorage(backend)
    store = BankStore(tracked, 'fictional-bank')
    provider = Provider()
    coordinator = BankCoordinator(provider, tracked, store, budget_id='fictional-budget', environment='sandbox',
        provider_id='fictional-bank', generation=7, home=tmp_path / 'flows', encryption_key='11' * 32,
        code_identity='fictional-code-v1', clock=lambda: 1000)
    value = SimpleNamespace(coordinator=coordinator, provider=provider, tracked=tracked, store=store, home=tmp_path / 'flows')
    try:
        yield value
    finally:
        if not coordinator._closed:
            asyncio.run(coordinator.close())
        close_storage(backend)


def run(coroutine):
    return asyncio.run(coroutine)


def begin(value):
    return run(value.coordinator.begin_link())


def complete(value, flow):
    return run(value.coordinator.complete_link(flow['flow_id'], 'fictional-public-token', 'Fictional institution'))


def test_begin_lost_reply_reuses_encrypted_link_token_and_original_generation(setup):
    first = begin(setup)
    setup.coordinator.scope = type(setup.coordinator.scope)('fictional-budget', 'sandbox', 'fictional-bank', 8)
    assert begin(setup) == first
    assert [call[0] for call in setup.provider.calls] == ['connection_token']
    raw = (setup.home / 'bank-continuations.json').read_bytes()
    assert b'fictional-link-token' not in raw
    assert first['generation'] == 7
    with pytest.raises(EffectError, match='Finish or reconcile'):
        setup.coordinator.prepare_close()
    assert run(setup.coordinator.cancel_link(first['flow_id']))['stage'] == 'cancelled'
    setup.coordinator.prepare_close()


def test_link_is_native_once_and_reload_finishes_without_original_public_token(setup):
    flow = begin(setup)
    result = complete(setup, flow)
    assert result['stage'] == 'complete'
    evidence = setup.tracked.commit_evidence()
    assert run(setup.coordinator.complete_link(flow['flow_id'])) == result
    assert run(setup.coordinator.continuation_status(flow['flow_id'])) == result
    assert setup.tracked.commit_evidence() == evidence
    assert [call[0] for call in setup.provider.calls].count('exchange_token') == 1
    item = setup.store.get_item('fictional-item')
    assert item['institutionName'] == 'Fictional institution'
    assert setup.coordinator._access('fictional-item') == 'fictional-private-access-token'
    raw = (setup.home / 'bank-continuations.json').read_bytes()
    for secret in (b'fictional-private-access-token', b'fictional-public-token', b'encryptedToken', b'Fictional institution'):
        assert secret not in raw
    with pytest.raises(EffectError) as error:
        run(setup.coordinator.complete_link(flow['flow_id'], 'changed-token'))
    assert error.value.code == 'effect_conflict'
    assert [call[0] for call in setup.provider.calls].count('exchange_token') == 1


def test_update_never_exchanges_public_token_and_retains_existing_identity(setup):
    complete(setup, begin(setup))
    existing = setup.store.get_item('fictional-item')
    update = run(setup.coordinator.begin_link('fictional-item'))
    assert update['kind'] == 'update'
    assert setup.provider.calls[-1][1]['access_token'] == 'fictional-private-access-token'
    assert run(setup.coordinator.complete_link(update['flow_id']))['stage'] == 'complete'
    assert setup.store.get_item('fictional-item')['token'] == existing['token']
    assert [call[0] for call in setup.provider.calls].count('exchange_token') == 1


def test_exchange_lost_response_remains_uncertain_and_cannot_repeat_or_retire(setup):
    flow = begin(setup)
    setup.provider.failure = ('exchange_token', OSError('fictional-private-provider-response'))
    with pytest.raises(EffectError) as error:
        complete(setup, flow)
    assert error.value.code == 'exchange_uncertain'
    assert run(setup.coordinator.continuation_status(flow['flow_id']))['stage'] == 'exchange_uncertain'
    for retry in (lambda: setup.coordinator.complete_link(flow['flow_id']),
                  lambda: setup.coordinator.cancel_link(flow['flow_id']),
                  setup.coordinator.begin_link):
        with pytest.raises(EffectError):
            run(retry())
    with pytest.raises(EffectError):
        setup.coordinator.prepare_close()
    assert [call[0] for call in setup.provider.calls].count('exchange_token') == 1
    assert setup.store.items() == []


def test_status_reconciles_lost_native_reply_without_provider_http_or_native_repeat(setup, monkeypatch):
    flow = begin(setup)
    execute = setup.coordinator.broker.execute
    def lost(command):
        execute(command)
        raise OSError('fictional native reply disappeared')
    monkeypatch.setattr(setup.coordinator.broker, 'execute', lost)
    with pytest.raises(EffectError) as error:
        complete(setup, flow)
    assert error.value.code == 'apply_uncertain'
    evidence, calls = setup.tracked.commit_evidence(), list(setup.provider.calls)
    monkeypatch.setattr(setup.coordinator.broker, 'execute', lambda _: pytest.fail('status cannot write native state'))
    assert run(setup.coordinator.continuation_status(flow['flow_id']))['stage'] == 'complete'
    assert setup.tracked.commit_evidence() == evidence and setup.provider.calls == calls
    setup.coordinator.prepare_close()


def test_uncaptured_native_apply_status_never_writes_and_finish_uses_exact_saved_command(setup, monkeypatch):
    flow = begin(setup)
    execute = setup.coordinator.broker.execute
    monkeypatch.setattr(setup.coordinator.broker, 'execute', lambda _: (_ for _ in ()).throw(OSError('before native dispatch')))
    with pytest.raises(EffectError):
        complete(setup, flow)
    scope = setup.coordinator.journal.scope_for(flow['flow_id'])
    original = setup.coordinator.journal.native_request(scope, flow['flow_id'])
    calls = list(setup.provider.calls)
    assert run(setup.coordinator.continuation_status(flow['flow_id']))['stage'] == 'apply_uncertain'
    assert setup.store.items() == []
    monkeypatch.setattr(setup.coordinator.broker, 'execute', execute)
    assert run(setup.coordinator.complete_link(flow['flow_id']))['stage'] == 'complete'
    assert setup.coordinator.journal.native_request(scope, flow['flow_id']) == original
    assert setup.provider.calls == calls


def test_unrelated_native_generation_or_pending_proxy_prevents_captured_command_repeat(setup, monkeypatch):
    flow = begin(setup)
    monkeypatch.setattr(setup.coordinator.broker, 'execute', lambda _: (_ for _ in ()).throw(OSError('no dispatch')))
    with pytest.raises(EffectError):
        complete(setup, flow)
    setup.tracked.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 0})
    monkeypatch.setattr(setup.coordinator.broker, 'execute', lambda _: pytest.fail('absence does not prove rollback'))
    with pytest.raises(EffectError) as error:
        run(setup.coordinator.complete_link(flow['flow_id']))
    assert error.value.code == 'apply_uncertain'
    assert setup.store.items() == []


def test_sync_checkpoint_and_webhook_owner_use_host_scoped_native_commands(setup):
    complete(setup, begin(setup))
    run(setup.coordinator.sync('fictional-item'))
    assert setup.store.get_item('fictional-item')['cursor'] == 'fictional-next-cursor'
    result = run(setup.coordinator.webhook(b'fictional signed body', {}))
    assert result.ok and setup.store.get_item('fictional-item')['status'] == 'reconnect'
    run(setup.coordinator.disconnect('fictional-item'))
    assert setup.store.get_item('fictional-item')['status'] == 'disconnected'
    run(setup.coordinator.disconnect('fictional-item'))
    assert [call[0] for call in setup.provider.calls].count('remove_token') == 1


def test_native_apply_failure_never_triggers_unrelated_status_write(setup, monkeypatch):
    complete(setup, begin(setup))
    execute = setup.tracked.execute_operation
    calls = []
    def fail(operation):
        calls.append(operation.name)
        if operation.name == 'apply_sync':
            raise OSError('fictional native uncertain outcome')
        return execute(operation)
    monkeypatch.setattr(setup.tracked, 'execute_operation', fail)
    with pytest.raises(OSError):
        run(setup.coordinator.sync('fictional-item'))
    assert calls == ['apply_sync']
    assert setup.store.get_item('fictional-item')['cursor'] is None


def test_http_cancellation_keeps_provider_result_and_lease_until_native_capture(setup):
    async def scenario():
        provider, coordinator = setup.provider, setup.coordinator
        provider.gate = threading.Event(), threading.Event()
        runtime = BudgetRuntime('fictional-budget', {'banks': {'fictional-bank': coordinator}})
        banks = LeasedBanks(runtime)
        flow = await banks['fictional-bank'].begin_link()
        task = asyncio.create_task(banks['fictional-bank'].complete_link(flow['flow_id'], 'fictional-public-token'))
        assert await asyncio.to_thread(provider.gate[0].wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.status()['active_leases'] == 1
        transitioned = asyncio.Event()
        async def prepare(preparation):
            transitioned.set()
            return RuntimeCandidate(preparation.active.services)
        transition = asyncio.create_task(runtime.transition(prepare))
        await transitioned.wait()
        await asyncio.sleep(0)
        assert not transition.done()
        provider.gate[1].set()
        await transition
        assert (await banks['fictional-bank'].continuation_status(flow['flow_id']))['stage'] == 'complete'
        assert setup.store.get_item('fictional-item') is not None
        await runtime.shutdown()
    run(scenario())


def test_inactive_authority_or_pending_native_intent_cannot_start_external_provider_work(setup, monkeypatch):
    evidence = setup.tracked.commit_evidence()
    setup.tracked.set_role('fictional-fence', evidence, 'fenced')
    with pytest.raises(EffectError):
        begin(setup)
    assert setup.provider.calls == []
    setup.tracked.set_role('fictional-fence', setup.tracked.commit_evidence(), 'primary')
    monkeypatch.setattr(setup.tracked, 'worker_recovery_required', lambda: True, raising=False)
    with pytest.raises(EffectError) as error:
        begin(setup)
    assert error.value.code == 'apply_uncertain'
    assert setup.provider.calls == []


def test_typed_sync_generates_category_suggestions_once_and_native_replay_does_not_repeat_callback(setup, monkeypatch):
    complete(setup, begin(setup))
    callbacks, operations = [], []
    setup.tracked.after_ingestion = lambda: callbacks.append('after-import')
    execute = setup.tracked.store.execute_operation
    def observe(operation):
        operations.append(operation)
        return execute(operation)
    monkeypatch.setattr(setup.tracked.store, 'execute_operation', observe)
    assert run(setup.coordinator.sync('fictional-item')) == {'added': 0, 'modified': 0, 'removed': 0}
    assert callbacks == ['after-import']
    operation, = operations
    assert setup.tracked.execute_operation(operation).replayed
    assert callbacks == ['after-import']


def test_queued_sync_retains_independent_lease_and_followup_notification(setup, monkeypatch):
    async def scenario():
        complete_flow = await setup.coordinator.begin_link()
        await setup.coordinator.complete_link(complete_flow['flow_id'], 'fictional-public-token')
        runtime = BudgetRuntime('fictional-budget', {'banks': {'fictional-bank': setup.coordinator}})
        setup.coordinator.background_scope = runtime.background
        started, release = threading.Event(), threading.Event()
        call = setup.provider.call
        sync_calls = []
        def held(command, **values):
            if command == 'read_sync':
                sync_calls.append(values['cursor'])
                if len(sync_calls) == 1:
                    started.set()
                    assert release.wait(10)
            return call(command, **values)
        monkeypatch.setattr(setup.provider, 'call', held)
        async with runtime.request():
            setup.coordinator.queue_sync('fictional-item')
            assert await asyncio.to_thread(started.wait, 5)
            assert runtime.status()['active_leases'] == 2
            setup.coordinator.queue_sync('fictional-item')
            setup.coordinator.queue_sync('fictional-item')
            worker = setup.coordinator._queued['fictional-item']['task']
        assert runtime.status()['active_leases'] == 1
        with pytest.raises(EffectError):
            setup.coordinator.prepare_close()
        release.set()
        await worker
        assert sync_calls == [None, 'fictional-next-cursor']
        assert runtime.status()['active_leases'] == 0
        setup.coordinator.prepare_close()
        await runtime.shutdown()
    run(scenario())


def test_close_cancellation_keeps_inflight_exchange_until_capture_and_native_apply_finish(setup):
    async def scenario():
        setup.provider.gate = threading.Event(), threading.Event()
        flow = await setup.coordinator.begin_link()
        completing = asyncio.create_task(setup.coordinator.complete_link(flow['flow_id'], 'fictional-public-token'))
        assert await asyncio.to_thread(setup.provider.gate[0].wait, 5)
        closing = asyncio.create_task(setup.coordinator.close())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert setup.provider.closed is False
        setup.provider.gate[1].set()
        assert (await completing)['stage'] == 'complete'
        await setup.coordinator.close()
        assert setup.provider.closed and setup.store.get_item('fictional-item') is not None
    run(scenario())


def test_scoped_native_sync_pending_replacement_keeps_latest_review_and_note(setup, monkeypatch):
    complete(setup, begin(setup))
    account = {'id': 'checking', 'name': 'Fictional checking', 'institution': 'Fictional institution',
               'type': 'depository', 'balance': 10000, 'currency': 'USD', 'source': 'fictional-bank',
               'itemId': 'fictional-item'}
    def response(identifier, pending=False, previous=None):
        transaction = {'id': identifier, 'accountId': 'checking', 'date': '2026-10-01',
            'merchant': 'Fictional market', 'amount': 1000, 'suggestedCategoryId': None,
            'pending': pending, 'source': 'fictional-bank', 'inferredTransfer': False,
            'pendingTransactionId': previous}
        return {'changes': {'added': [transaction], 'modified': [], 'removed': ['pending'] if previous else []},
                'cursor': identifier, 'accounts': [account], 'historical_complete': True}
    monkeypatch.setattr(setup.provider, 'call', lambda command, **_: response('pending', True))
    assert run(setup.coordinator.sync('fictional-item'))['added'] == 1
    pending, = setup.tracked.transactions('personal')
    setup.tracked.edit_transaction('personal', pending['id'], 'fun', True, note='Fictional reviewed memo')
    monkeypatch.setattr(setup.provider, 'call', lambda command, **_: response('posted', previous='pending'))
    run(setup.coordinator.sync('fictional-item'))
    posted, = setup.tracked.transactions('personal')
    assert posted['id'] != pending['id']
    assert (posted['reviewed'], posted['categoryId'], posted['note']) == (True, 'fun', 'Fictional reviewed memo')
    assert posted['source'] == 'fictional-bank' and posted['pending'] is False
    assert setup.store.get_item('fictional-item')['cursor'] == 'posted'


def test_cursor_changed_during_provider_read_rejects_atomic_batch_without_late_status_write(setup, monkeypatch):
    complete(setup, begin(setup))
    call = setup.provider.call
    def concurrent(command, **values):
        response = call(command, **values)
        assert command == 'read_sync' and values['cursor'] is None
        setup.store.apply_sync('fictional-item', {'added': [], 'modified': [], 'removed': []}, 'other-committed-cursor',
                               expected_cursor=None)
        return response
    monkeypatch.setattr(setup.provider, 'call', concurrent)
    # Native worker errors deliberately omit backend exception text. The
    # rejection code proves the competing cursor was retained without a write.
    with pytest.raises(PluginError) as rejected:
        run(setup.coordinator.sync('fictional-item'))
    assert rejected.value.code == 'storage_worker_rejected'
    item = setup.store.get_item('fictional-item')
    assert item['cursor'] == 'other-committed-cursor' and item['status'] == 'connected'
    assert setup.tracked.transactions('personal') == []


def test_disconnect_lost_native_reply_reconciles_before_already_disconnected_shortcut(setup, monkeypatch):
    complete(setup, begin(setup))
    execute = setup.coordinator.broker.execute
    def lost(command):
        execute(command)
        raise OSError('fictional lost native disconnect reply')
    monkeypatch.setattr(setup.coordinator.broker, 'execute', lost)
    with pytest.raises(EffectError) as error:
        run(setup.coordinator.disconnect('fictional-item'))
    assert error.value.code == 'apply_uncertain'
    assert setup.store.get_item('fictional-item')['status'] == 'disconnected'
    monkeypatch.setattr(setup.coordinator.broker, 'execute', lambda _: pytest.fail('Committed disconnect cannot repeat'))
    run(setup.coordinator.disconnect('fictional-item'))
    setup.coordinator.prepare_close()
    assert [call[0] for call in setup.provider.calls].count('remove_token') == 1


def test_new_account_setup_blocks_import_until_user_selects_destinations(setup):
    value = setup
    value.coordinator.requires_account_setup = True
    flow = begin(value)
    complete(value, flow)
    with pytest.raises(Exception, match='destination accounts'):
        run(value.coordinator.sync('fictional-item'))
    assert value.store.get_item('fictional-item')['cursor'] is None
    # Reviewing provider accounts must never advance the native cursor.
    original = value.provider.call
    remote = {'id': 'bank-account', 'name': 'Fictional checking', 'mask': '1234', 'institution': 'Fictional bank',
              'type': 'depository', 'balance': 100, 'currency': 'USD', 'source': 'fictional-bank', 'itemId': 'fictional-item'}
    def call(command, **values):
        result = original(command, **values)
        if command == 'read_sync':
            result['accounts'] = [remote]
        return result
    value.provider.call = call
    preview = run(value.coordinator.sync_preview('fictional-item'))
    assert preview['accounts'][0]['name'] == 'Fictional checking'
    assert not preview['configured'] and value.store.get_item('fictional-item')['cursor'] is None
    assert run(value.coordinator.configure_sync('fictional-item', [{'bankAccountId': 'bank-account', 'accountId': None, 'since': '2026-10-01'}]))['configured']
    assert run(value.coordinator.sync('fictional-item')) == {'added': 0, 'modified': 0, 'removed': 0}
    assert value.store.get_item('fictional-item')['cursor'] == 'fictional-next-cursor'
    value.coordinator.automatic_sync = lambda: False
    value.coordinator.queue_sync('fictional-item')
    assert not value.coordinator._queued


def test_bulk_sync_metadata_wait_does_not_block_the_event_loop(setup):
    flow = begin(setup)
    complete(setup, flow)
    entered, release = threading.Event(), threading.Event()
    original = setup.store.items
    def blocked_items():
        entered.set()
        assert release.wait(5)
        return original()
    setup.store.items = blocked_items
    async def scenario():
        task = asyncio.create_task(setup.coordinator.sync())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert not task.done()
        finally:
            release.set()
        assert await task == {'added': 0, 'modified': 0, 'removed': 0}
    run(scenario())


def test_archived_only_bank_item_makes_no_provider_request_and_mixed_item_keeps_peer_syncing(setup):
    from whab.plugins import LedgerService
    value=setup
    flow=begin(value)
    complete(value,flow)
    original=value.provider.call
    records=[{'id':'fixture-cd','itemId':'fictional-item','name':'Fictional CD','institution':'Fictional institution','type':'deposit','subtype':'cd','onBudget':False,'balance':10000,'currency':'USD','source':'fictional-bank'}]
    def provider(command,**values):
        if command=='read_sync':
            value.provider.calls.append((command,values))
            return {'changes':{'added':[],'modified':[],'removed':[]},'cursor':'next-cursor','accounts':records,'historical_complete':True}
        return original(command,**values)
    value.provider.call=provider
    run(value.coordinator.sync('fictional-item'))
    row=value.tracked.accounts('personal')[0]
    service=LedgerService(value.tracked)
    service.update_account('personal',row['id'],{'closed':True})
    value.provider.calls.clear()
    result=run(value.coordinator.sync('fictional-item'))
    assert result['skippedClosed']==1 and value.provider.calls==[]
    assert value.tracked.accounts('personal')[0]['closed']
    service.update_account('personal',row['id'],{'closed':False})
    records.append({**records[0],'id':'fixture-peer','name':'Open peer','type':'depository','subtype':'checking','onBudget':True})
    run(value.coordinator.sync('fictional-item'))
    service.update_account('personal',row['id'],{'closed':True})
    frozen=next(account for account in value.tracked.accounts('personal') if account['id']==row['id'])
    records[0]={**records[0],'balance':99999}
    records[1]={**records[1],'balance':30000}
    value.provider.calls.clear()
    run(value.coordinator.sync('fictional-item'))
    assert [call[0] for call in value.provider.calls]==['read_sync']
    saved=value.tracked.accounts('personal')
    assert next(account for account in saved if account['id']==row['id'])==frozen
    assert next(account for account in saved if account['id']!=row['id'])['balance']==30000
