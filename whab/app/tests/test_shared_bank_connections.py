"""Shared identity, independent native progress, and interrupted local migration."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.storage.test_bank_boundaries import account, changes, transaction
from whab.budgets.session import TrackedStorage
from whab.config import Settings
from whab.plugins import BankError, BudgetContext, StorageContext, load_plugins
from whab.plugins.bank_adapter import ProviderFailure, ProviderWebhook
from whab.plugins.bank_crypto import BankCipher
from whab.plugins.storage import open_storage
from whab.services.bank_lifecycle import BankLifecycle
from whab.services.bank_runtime import BankCoordinator


class Provider:
    config = {'environment': 'sandbox', 'configured': True, 'liveEnabled': True, 'missingConfig': []}

    def __init__(self):
        self.calls, self.failure = [], None
        self.response = {'accounts': [account()], 'changes': changes([
            transaction('old', date='2026-09-01'), transaction('new', date='2026-10-01')]),
            'cursor': 'cursor-one', 'historical_complete': True}

    def call(self, command, **values):
        self.calls.append((command, values))
        if self.failure:
            raise self.failure
        if command == 'read_sync':
            return self.response
        if command == 'webhook_event':
            return ProviderWebhook(True, item_id='item', reconnect=True, sync=True)
        if command == 'connection_token':
            return 'fictional-link-token'
        if command == 'remove_token':
            return None
        raise AssertionError(command)

    def close(self):
        pass


@pytest.fixture(params=['sqlite', 'json'])
def shared(tmp_path, request):
    settings = Settings(tmp_path, 5173, 'sandbox', '', '', '11' * 32, False, '', '', (), ui='')
    cipher = BankCipher(settings.encryption_key)
    values = []
    for identifier in ('owner', 'consumer'):
        native = open_storage(load_plugins((request.param,)).storage_plugin(request.param),
            StorageContext(tmp_path / identifier / 'native', 'sandbox', {},
                budget=BudgetContext(identifier, {}, {}, lambda _: None)))
        tracked = TrackedStorage(native)
        if identifier == 'owner':
            native.save_item('item', cipher.encrypt_token('fictional-access-token', 'item'), 'Fictional bank')
        lifecycle = BankLifecycle(settings, tracked, identifier, home=tmp_path / identifier)
        lifecycle.migrate_connections('plaid')
        provider = Provider()
        service = BankCoordinator(provider, tracked, lifecycle.bank_store('plaid'), budget_id=identifier,
            environment='sandbox', provider_id='plaid', generation=1, home=tmp_path / identifier / 'flows',
            cipher=cipher, requires_account_setup=True, connections=lifecycle.connections)
        values.append(SimpleNamespace(native=native, tracked=tracked, lifecycle=lifecycle, provider=provider, service=service))
    try:
        yield SimpleNamespace(owner=values[0], consumer=values[1], settings=settings)
    finally:
        for value in values:
            asyncio.run(value.service.close())
            asyncio.run(value.lifecycle.close())
            value.native.close()


def run(value):
    return asyncio.run(value)


def configure(value, since):
    run(value.service.configure_sync('item', [{'bankAccountId': 'account', 'accountId': None, 'since': since}]))


def test_attachment_is_explicit_and_never_calls_provider_or_copies_ledger(shared):
    owner, consumer = shared.owner, shared.consumer
    connection = run(consumer.service.shared_connections())['connections'][0]
    assert connection['ownerBudgetId'] == 'owner' and not connection['attached']
    assert consumer.native.items() == []
    assert run(consumer.service.attach('item')) == {'attached': True, 'itemId': 'item'}
    assert consumer.provider.calls == [] and consumer.native.accounts('personal') == []
    assert consumer.native.get_item('item')['cursor'] is None
    assert owner.native.get_item('item')['token'].startswith('shared-bank-v1:')
    assert owner.service._access('item') == consumer.service._access('item') == 'fictional-access-token'
    assert run(consumer.service.attach('item'))['attached']
    with pytest.raises(BankError, match='Choose destination'):
        run(consumer.service.sync('item'))
    raw = owner.lifecycle.connections.path.read_bytes()
    assert b'fictional-access-token' not in raw
    public = json.dumps(run(consumer.service.shared_connections()))
    assert 'token' not in public and 'cursor' not in public
    assert 'item' not in json.dumps(consumer.native.export_budget('personal').get('items', []))


def test_independent_cursors_cutoffs_annotations_and_replay(shared):
    owner, consumer = shared.owner, shared.consumer
    run(consumer.service.attach('item'))
    configure(owner, None)
    configure(consumer, '2026-10-01')
    run(owner.service.sync('item'))
    assert consumer.native.get_item('item')['cursor'] is None
    run(consumer.service.sync('item'))
    assert {row['id'] for row in owner.native.transactions('personal')} == {'old', 'new'}
    assert {row['id'] for row in consumer.native.transactions('personal')} == {'new'}
    consumer.native.edit_transaction('personal', 'new', note='Keep this note', reviewed=True)
    consumer.provider.response['changes'] = changes(modified=[transaction('new', amount=1900)])
    run(consumer.service.sync('item'))
    saved = consumer.native.transactions('personal')[0]
    assert saved['note'] == 'Keep this note' and saved['reviewed'] and saved['amount'] == 1900
    assert next(row for row in owner.native.transactions('personal') if row['id'] == 'new')['amount'] == 1000
    configure(consumer, None)
    consumer.provider.response['changes'] = changes([transaction('old'), transaction('new')])
    run(consumer.service.sync('item'))
    run(consumer.service.sync('item'))
    assert len(consumer.native.transactions('personal')) == 2
    assert next(row for row in consumer.native.transactions('personal') if row['id'] == 'new')['note'] == 'Keep this note'


def test_detach_preserves_progress_and_other_consumers_and_reattach_resumes(shared):
    value = shared.consumer
    run(value.service.attach('item'))
    configure(value, None)
    run(value.service.sync('item'))
    before = value.native.get_item('item')
    run(value.service.detach('item'))
    assert value.lifecycle.bank_store('plaid').items() == []
    with pytest.raises(BankError, match='Attach this'):
        run(value.service.sync('item'))
    value.lifecycle.migrate_connections('plaid')
    assert not run(value.service.shared_connections())['connections'][0]['attached']
    assert shared.owner.service._access('item') == 'fictional-access-token'
    run(value.service.attach('item'))
    assert value.native.get_item('item') == before
    assert not any(command == 'remove_token' for command, _ in value.provider.calls)
    assert len(value.native.transactions('personal')) == 2


def test_global_disconnect_confirms_every_budget_and_fences_all_access(shared):
    owner, consumer = shared.owner, shared.consumer
    run(consumer.service.attach('item'))
    with pytest.raises(BankError, match='every affected budget'):
        run(owner.service.disconnect('item'))
    assert not owner.provider.calls
    run(owner.service.disconnect('item', ['consumer', 'owner']))
    assert [command for command, _ in owner.provider.calls] == ['remove_token']
    assert run(consumer.service.shared_connections())['connections'][0]['status'] == 'disconnected'
    with pytest.raises(BankError, match='disconnected'):
        consumer.service._access('item')
    with pytest.raises(BankError, match='disconnected'):
        run(owner.service.attach('item'))
    run(owner.service.disconnect('item', ['consumer', 'owner']))
    assert len(owner.provider.calls) == 1
    assert owner.lifecycle.connections.get('plaid', 'item')['token'] is None


def test_shared_reconnect_status_and_manual_webhook_policy(shared):
    owner, consumer = shared.owner, shared.consumer
    run(consumer.service.attach('item'))
    configure(owner, None)
    owner.provider.failure = ProviderFailure('reconnect')
    with pytest.raises(BankError):
        run(owner.service.sync('item'))
    assert consumer.lifecycle.bank_store('plaid').items()[0]['status'] == 'reconnect'
    owner.provider.failure = None
    owner.service.automatic_sync = lambda: False
    run(owner.service.webhook(b'fictional', {}))
    assert owner.service._queued == {}
    assert consumer.lifecycle.bank_store('plaid').items()[0]['status'] == 'reconnect'


def test_interrupted_registration_preserves_owner_and_original_cursor(shared, monkeypatch):
    value = shared.owner
    cipher = BankCipher('11' * 32)
    value.native.save_item('second-item', cipher.encrypt_token('fictional-second-token', 'second-item'), 'Fictional bank')
    value.native.set_item_status('second-item', 'reconnect', 'Reconnect required')
    original = value.tracked.save_item
    def fail(*args, **kwargs):
        raise OSError('fictional interrupted write')
    monkeypatch.setattr(value.tracked, 'save_item', fail)
    with pytest.raises(OSError):
        value.lifecycle.migrate_connections('plaid')
    record = value.lifecycle.connections.get('plaid', 'second-item')
    assert record['ownerBudgetId'] == 'owner'
    assert not value.native.get_item('second-item')['token'].startswith('shared-bank-v1:')
    monkeypatch.setattr(value.tracked, 'save_item', original)
    value.lifecycle.migrate_connections('plaid')
    value.lifecycle.migrate_connections('plaid')
    assert value.native.get_item('second-item')['status'] == 'reconnect'
    assert value.native.get_item('second-item')['cursor'] is None
    assert len(run(shared.consumer.service.shared_connections())['connections']) == 2
    assert value.lifecycle.connections.get('plaid', 'second-item')['ownerBudgetId'] == 'owner'


def test_missing_and_wrong_home_key_cannot_adopt_a_reference(shared, tmp_path):
    from whab.services.bank_connections import BankConnections
    registry = BankConnections(tmp_path / 'other-home', 'sandbox', BankCipher('11' * 32))
    item = {**shared.owner.native.get_item('item'), 'storedIdentity': 'item'}
    with pytest.raises(BankError, match='missing its device-local'):
        registry.register('plaid', item, 'owner')
    production = BankConnections(shared.settings.home, 'production', BankCipher('11' * 32))
    assert production.public('plaid', 'owner') == []
    wrong_key = BankConnections(shared.settings.home, 'sandbox', BankCipher('22' * 32))
    with pytest.raises(BankError, match='unlocked'):
        wrong_key.access('plaid', 'item', 'owner')


def test_multiple_provider_accounts_pin_credentials_and_namespace_identical_item_ids(shared):
    from whab.services.bank_profiles import BankProfiles
    owner, consumer = shared.owner, shared.consumer
    profiles = BankProfiles(owner.lifecycle.connections, 'plaid', 'owner', ('CLIENT_ID',),
        {'CLIENT_ID': 'fictional-client-one', 'SECRET': 'fictional-secret-one'})
    profiles.save('second-account', {'CLIENT_ID': 'fictional-client-two', 'SECRET': 'fictional-secret-two'})
    original = profiles.path.read_bytes()
    BankProfiles(owner.lifecycle.connections, 'plaid', 'owner', ('CLIENT_ID',),
        {'CLIENT_ID': 'fictional-client-one', 'SECRET': 'fictional-secret-one'})
    assert profiles.path.read_bytes() == original
    selected = []
    def call_with_config(config, command, **values):
        selected.append((config['CLIENT_ID'], command, values.get('item_id')))
        if command == 'exchange_token':
            return {'item_id': 'item', 'access_token': 'fictional-second-account-token'}
        return owner.provider.call(command, **values)
    owner.provider.call_with_config = call_with_config
    owner.service.profiles = profiles
    flow = run(owner.service.begin_link(None, 'second-account'))
    run(owner.service.complete_link(flow['flow_id'], 'fictional-public-token', 'Fictional bank'))
    connections = run(owner.service.shared_connections())['connections']
    second = next(row for row in connections if row['profileId'] == 'second-account')
    assert second['itemId'] != 'item' and second['providerItemId'] == 'item'
    assert second['ownerBudgetId'] == 'owner' and len(connections) == 2
    assert owner.service._access('item') == 'fictional-access-token'
    assert owner.service._access(second['itemId']) == 'fictional-second-account-token'
    run(owner.service.configure_sync(second['itemId'], [{'bankAccountId': 'account', 'accountId': None, 'since': None}]))
    run(owner.service.sync(second['itemId']))
    assert {row['id'] for row in owner.native.transactions('personal')} != {'old', 'new'}
    assert all(client == 'fictional-client-two' for client, _, _ in selected)
    assert all(item_id in (None, 'item') for _, _, item_id in selected)
    consumer.service.profiles = profiles
    consumer.provider.call_with_config = call_with_config
    run(consumer.service.attach(second['itemId']))
    assert consumer.service._access(second['itemId']) == 'fictional-second-account-token'
    run(consumer.service.sync_preview(second['itemId']))
    assert selected[-1][0] == 'fictional-client-two'
    public = json.dumps(run(owner.service.list_profiles()))
    assert 'SECRET' not in public and 'fictional-client' not in public
    raw = profiles.path.read_bytes()
    assert b'fictional-client' not in raw and b'fictional-secret' not in raw
    with pytest.raises(BankError, match='another provider account'):
        profiles.save('second-account', {'CLIENT_ID': 'different-account', 'SECRET': 'fictional'})
    with pytest.raises(BankError, match='already has a profile'):
        profiles.save('duplicate', {'CLIENT_ID': 'fictional-client-two', 'SECRET': 'fictional'})
    # Replacing .env with another account never redirects the old default's Items.
    changed = BankProfiles(owner.lifecycle.connections, 'plaid', 'owner', ('CLIENT_ID',),
        {'CLIENT_ID': 'different-default', 'SECRET': 'fictional-new-secret'})
    assert changed.config('default')['CLIENT_ID'] == 'fictional-client-one'


def test_pending_attachment_recovers_after_native_commit_and_reopen(shared, monkeypatch):
    value = shared.consumer
    original = value.lifecycle.connections.attachment
    def interrupted(provider, item_id, budget_id, state):
        if state == 'attached':
            raise OSError('fictional lost reply after native commit')
        return original(provider, item_id, budget_id, state)
    monkeypatch.setattr(value.lifecycle.connections, 'attachment', interrupted)
    with pytest.raises(OSError):
        run(value.service.attach('item'))
    assert value.native.get_item('item')['token'].startswith('shared-bank-v1:')
    assert value.lifecycle.connections.get('plaid', 'item')['attachments']['consumer'] == 'pending'
    value.lifecycle.migrate_connections('plaid')
    assert run(value.service.shared_connections())['connections'][0]['attached']
    assert value.provider.calls == [] and value.native.transactions('personal') == []


def test_failed_destination_save_retains_both_cursors_and_allows_replay(shared, monkeypatch):
    owner, consumer = shared.owner, shared.consumer
    run(consumer.service.attach('item'))
    configure(owner, None)
    configure(consumer, None)
    original = consumer.tracked.execute_operation
    def interrupted(operation):
        if operation.name == 'apply_sync':
            raise OSError('fictional disk failure')
        return original(operation)
    monkeypatch.setattr(consumer.tracked, 'execute_operation', interrupted)
    with pytest.raises(OSError):
        run(consumer.service.sync('item'))
    assert consumer.native.get_item('item')['cursor'] is None and consumer.native.transactions('personal') == []
    run(owner.service.sync('item'))
    assert consumer.native.get_item('item')['cursor'] is None
    monkeypatch.setattr(consumer.tracked, 'execute_operation', original)
    run(consumer.service.sync('item'))
    run(consumer.service.sync('item'))
    assert len(consumer.native.transactions('personal')) == 2


def test_discovery_migrates_dormant_disabled_budget_without_provider_work(tmp_path):
    from whab.app import create_app
    from whab.budgets.host import BudgetHost
    from whab.budgets.session import BudgetSession
    settings = Settings(tmp_path, 5173, 'sandbox', '', '', '11' * 32, False, '', '', (), ui='')
    host = BudgetHost(settings)
    session = BudgetSession.create(tmp_path, 'sandbox', 'Dormant fictional budget', (), storage='sqlite')
    native = open_storage(load_plugins(('sqlite',)).storage_plugin('sqlite'),
        StorageContext(session.backend_home, 'sandbox', {}, budget=session.context('sqlite')))
    session.bind(native)
    native.save_item('dormant-item', BankCipher('11' * 32).encrypt_token('fictional-dormant-access', 'dormant-item'), 'Fictional bank')
    native.apply_sync('dormant-item', changes([transaction()]), 'saved-cursor', [account(item_id='dormant-item')])
    session.checkpoint()
    native.close()
    host._remember(session)
    app = create_app(settings=settings, budget_host=host)
    async def scenario():
        async with app.router.lifespan_context(app):
            assert await host.discover_connections('plaid') == []
            rows = app.state.bank_lifecycle.connections.public('plaid', 'default')
            assert rows[0]['ownerBudgetId'] == session.id and rows[0]['attachments'] == [{'budgetId': session.id, 'state': 'attached'}]
            assert session.id not in host.applications
            assert await host.discover_connections('plaid') == []
    run(scenario())
    native = open_storage(load_plugins(('sqlite',)).storage_plugin('sqlite'),
        StorageContext(session.backend_home, 'sandbox', {}, budget=session.context('sqlite')))
    try:
        assert native.get_item('dormant-item')['cursor'] == 'saved-cursor'
        assert native.get_item('dormant-item')['token'].startswith('shared-bank-v1:')
        assert len(native.transactions('personal')) == 1
    finally:
        native.close()
