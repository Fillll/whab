"""Shared identity, independent native progress, and interrupted local migration."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.storage.test_bank_boundaries import account, changes, transaction
from whab.budgets.session import TrackedStorage
from whab.config import Settings
from whab.plugins import BankError, BudgetContext, StorageContext, load_plugins
from whab.plugins.storage import open_storage
from whab.plugins.bank_adapter import ProviderFailure, ProviderWebhook
from whab.plugins.bank_crypto import BankCipher
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
