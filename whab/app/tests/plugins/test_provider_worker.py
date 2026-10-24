"""Pure provider transports exchange strict bounded DTOs, with no host handles."""

import base64
from dataclasses import asdict

import pytest

from whab.plugins import (
    BankAdapter,
    BankProviderContext,
    Capability,
    Plugin,
    PluginContract,
    ProviderWebhook,
)
from whab.plugins.effect_store import decode, encode
from whab.plugins.provider_worker import ProviderDispatcher, read_provider_result
from whab.plugins.wire import WireError
from whab.plugins.worker_adapters import supports_live_plugin


class Provider:
    config = {'environment': 'sandbox', 'configured': True, 'liveEnabled': False, 'missingConfig': []}
    instances = []

    def __init__(self, context):
        assert type(context) is BankProviderContext
        assert not any(hasattr(context, name) for name in ('store', 'budget', 'encryption_key', 'background_scope'))
        self.context, self.closed = context, False
        self.instances.append(self)

    async def connection_token(self, item_id, access_token):
        return 'fictional Link token'
    async def exchange_token(self, public_token):
        return {'item_id': 'fictional-item', 'access_token': 'fictional-access-token'}
    async def read_sync(self, item_id, access_token, cursor, institution_name):
        return {'changes': {'added': [], 'modified': [], 'removed': []}, 'cursor': 'next-cursor',
                'accounts': [], 'historical_complete': True}
    async def remove_token(self, access_token):
        return None
    async def webhook_event(self, raw, headers):
        return ProviderWebhook(True, item_id='fictional-item', sync=True)
    async def close(self):
        self.closed = True


def plugin(factory=Provider):
    return Plugin('fictional-bank', 'Fictional bank', bank=BankAdapter(factory),
                  contract=PluginContract(lifecycle='live', provides=(Capability('lifecycle.background'),)))


def call(dispatcher, command, values):
    metadata, raw = dispatcher.call({'command': command}, encode(values, 16 * 1024 * 1024))
    assert metadata == {'provider': 1}
    return decode(raw, 16 * 1024 * 1024)


@pytest.fixture
def dispatcher():
    dispatcher = ProviderDispatcher(plugin())
    try:
        yield dispatcher
    finally:
        dispatcher.close()


def opened(dispatcher):
    return call(dispatcher, 'open', {'budget_id': 'synthetic-budget', 'environment': 'sandbox',
                                   'config': {'PROVIDER_SECRET': 'fictional private provider credential'}})


def test_explicit_provider_factory_and_all_operations_are_data_only(dispatcher):
    assert supports_live_plugin(plugin())
    assert opened(dispatcher)['configured']
    assert call(dispatcher, 'connection_token', {'item_id': None, 'access_token': None}) == 'fictional Link token'
    assert call(dispatcher, 'connection_token', {'item_id': 'fictional-item', 'access_token': 'fictional-access-token'})
    assert call(dispatcher, 'exchange_token', {'public_token': 'fictional-public-token'}) == {
        'item_id': 'fictional-item', 'access_token': 'fictional-access-token'}
    assert call(dispatcher, 'read_sync', {'item_id': 'fictional-item', 'access_token': 'fictional-access-token',
        'cursor': None, 'institution_name': 'Fictional institution'})['cursor'] == 'next-cursor'
    assert call(dispatcher, 'remove_token', {'access_token': 'fictional-access-token'}) is None
    assert call(dispatcher, 'webhook_event', {'raw': base64.b64encode(b'fictional signed webhook').decode(),
        'headers': {'provider-signature': 'fictional'}}) == asdict(ProviderWebhook(True, item_id='fictional-item', sync=True))
    assert call(dispatcher, 'close', {}) is None
    assert Provider.instances[-1].closed


@pytest.mark.parametrize('command,values', [
    ('open', {'budget_id': 'synthetic-budget', 'environment': 'sandbox', 'config': {'TOKEN_ENCRYPTION_KEY': 'forbidden'}}),
    ('connection_token', {'item_id': 'other-item', 'access_token': None}),
    ('exchange_token', {'public_token': 'x' * 501}),
    ('read_sync', {'item_id': 'item', 'access_token': 'token', 'cursor': False, 'institution_name': 'Institution'}),
    ('webhook_event', {'raw': base64.b64encode(b'x' * (1024 * 1024 + 1)).decode(), 'headers': {}}),
    ('webhook_event', {'raw': 'not base64', 'headers': {}}),
    ('webhook_event', {'raw': '', 'headers': {'X': 'x' * 8193}}),
])
def test_provider_arguments_fail_before_external_work(dispatcher, command, values):
    if command != 'open':
        opened(dispatcher)
    with pytest.raises(ValueError):
        call(dispatcher, command, values)


@pytest.mark.parametrize('command,value', [
    ('open', {'environment': 'sandbox', 'configured': True, 'liveEnabled': False, 'missingConfig': [], 'SECRET': 'private'}),
    ('exchange_token', {'item_id': 'item', 'access_token': 'token', 'unexpected': 'private'}),
    ('connection_token', 'x' * 4097),
    ('read_sync', {'changes': {'added': [], 'modified': [], 'removed': []}, 'cursor': 'cursor',
                   'accounts': [], 'historical_complete': 1}),
    ('read_sync', {'changes': {'added': [], 'modified': [], 'removed': [False]}, 'cursor': 'cursor',
                   'accounts': [], 'historical_complete': True}),
    ('webhook_event', {'ok': False, 'status_code': 401, 'item_id': 'wrong-item', 'sync': False, 'reconnect': False}),
    ('remove_token', 'unexpected'),
])
def test_provider_results_refuse_extra_fields_invalid_money_and_unverified_actions(command, value):
    with pytest.raises(ValueError):
        read_provider_result(command, value, provider_id='fictional-bank', environment='sandbox', item_id='item')


def test_provider_factory_failure_and_async_factory_close_resources():
    async def asynchronous(_):
        return None
    dispatcher = ProviderDispatcher(plugin(asynchronous))
    try:
        with pytest.raises(WireError):
            opened(dispatcher)
    finally:
        dispatcher.close()
    class Malformed(Provider):
        exchange_token = None
    dispatcher = ProviderDispatcher(plugin(Malformed))
    try:
        with pytest.raises(WireError):
            opened(dispatcher)
    finally:
        dispatcher.close()
    assert Provider.instances[-1].closed
