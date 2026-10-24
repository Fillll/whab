"""Pure provider DTOs retain normalized checkpoints and signed webhook rules."""

import asyncio
import json

import httpx
import pytest
from whab_plugin_plaid.provider import PlaidProvider, _SyncData

from whab.plugins import BankProviderContext, ProviderFailure


def context(plaid_env):
    return BankProviderContext('synthetic-budget', 'sandbox', {
        key: value for key, value in plaid_env.items() if key != 'TOKEN_ENCRYPTION_KEY'})


def test_provider_uses_plaintext_during_transport_and_returns_data_without_persisting(plaid_env, plaid_client, bank_account, bank_transaction):
    seen = []
    def transport(request):
        value = json.loads(request.content)
        seen.append((request.url.path, value))
        if request.url.path == '/link/token/create':
            return httpx.Response(200, json={'link_token': 'fictional-link'})
        if request.url.path == '/item/public_token/exchange':
            return httpx.Response(200, json={'item_id': 'item', 'access_token': 'fictional-access'})
        if request.url.path == '/transactions/sync':
            assert value['access_token'] == 'fictional-access' and value['cursor'] == 'original'
            return httpx.Response(200, json={'added': [bank_transaction()], 'modified': [], 'removed': [],
                'next_cursor': 'next', 'has_more': False, 'transactions_update_status': 'HISTORICAL_UPDATE_COMPLETE'})
        if request.url.path == '/accounts/get':
            return httpx.Response(200, json={'accounts': [bank_account()]})
        assert request.url.path == '/item/remove'
        return httpx.Response(200, json={})
    async def scenario():
        async with plaid_client(transport) as client:
            provider = PlaidProvider(context(plaid_env), client)
            assert provider.config['configured'] and 'TOKEN_ENCRYPTION_KEY' not in provider.env
            assert await provider.connection_token(None, None) == 'fictional-link'
            assert await provider.connection_token('item', 'fictional-access') == 'fictional-link'
            assert seen[-1][1]['access_token'] == 'fictional-access'
            assert provider._access_token is None
            assert await provider.exchange_token('fictional-public') == {'item_id': 'item', 'access_token': 'fictional-access'}
            result = await provider.read_sync('item', 'fictional-access', 'original', 'Fictional bank')
            assert result['cursor'] == 'next' and result['historical_complete']
            assert result['accounts'][0]['itemId'] == 'item' and result['changes']['added'][0]['pending'] is False
            assert provider._access_token is None
            assert await provider.remove_token('fictional-access') is None
            await provider.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('method,args', [
    ('connection_token', (None, None)), ('exchange_token', ('public',)),
    ('read_sync', ('item', 'access', None, 'Bank')), ('remove_token', ('access',)),
])
def test_provider_configuration_failure_never_calls_http(plaid_client, method, args):
    async def scenario():
        async with plaid_client(lambda _: pytest.fail('Missing config must not make HTTP')) as client:
            provider = PlaidProvider(BankProviderContext('budget', 'sandbox', {}), client)
            with pytest.raises(ProviderFailure) as error:
                await getattr(provider, method)(*args)
            assert error.value.code == 'not_configured'
    asyncio.run(scenario())


@pytest.mark.parametrize('method,args,error_code', [
    ('connection_token', (None, None), 'INTERNAL_SERVER_ERROR'),
    ('exchange_token', ('public',), 'INTERNAL_SERVER_ERROR'),
    ('read_sync', ('item', 'access', None, 'Bank'), 'ITEM_LOGIN_REQUIRED'),
    ('read_sync', ('item', 'access', None, 'Bank'), 'INTERNAL_SERVER_ERROR'),
    ('remove_token', ('access',), 'INTERNAL_SERVER_ERROR'),
])
def test_expected_provider_failures_use_fixed_codes_no_private_exception_payload(plaid_env, plaid_client, method, args, error_code):
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(400, json={'error_code': error_code,
                'error_message': 'fictional-private-provider-secret'})) as client:
            provider = PlaidProvider(context(plaid_env), client)
            with pytest.raises(ProviderFailure) as error:
                await getattr(provider, method)(*args)
            assert error.value.code == ('reconnect' if error_code == 'ITEM_LOGIN_REQUIRED' else 'transport')
            assert 'fictional-private' not in str(error.value) and provider._access_token is None
    asyncio.run(scenario())


def test_detached_accumulator_rejects_foreign_item_checkpoint_and_no_token_without_scope(plaid_env, plaid_client):
    snapshot = _SyncData('item', 'cursor')
    assert snapshot.get_item('foreign') is None
    with pytest.raises(ValueError):
        snapshot.apply_sync('foreign', {}, None, [], True, expected_cursor='cursor')
    with pytest.raises(ValueError):
        snapshot.set_item_status('foreign', 'error', None)
    async def scenario():
        async with plaid_client(lambda _: pytest.fail('No HTTP')) as client:
            provider = PlaidProvider(context(plaid_env), client)
            with pytest.raises(ProviderFailure):
                provider.token('item')
            provider._access_token = 'access'
            with pytest.raises(ProviderFailure):
                provider.token('foreign')
    asyncio.run(scenario())


@pytest.mark.parametrize('body', [b'not json', b'[]', b'{}', b'{"item_id":false}'])
def test_verified_webhook_payload_still_requires_a_structured_item(plaid_env, plaid_client, monkeypatch, body):
    async def scenario():
        async with plaid_client(lambda _: pytest.fail('Mock verification needs no network')) as client:
            provider = PlaidProvider(context(plaid_env), client)
            async def verified(*_):
                return True
            monkeypatch.setattr(provider, 'verify_webhook', verified)
            event = await provider.webhook_event(body, {})
            assert not event.ok and event.status_code == 400
    asyncio.run(scenario())


def test_provider_signature_verification_and_scoped_event_actions_use_same_real_crypto(plaid_env, plaid_client, webhook_signature):
    key, sign = webhook_signature
    async def scenario():
        async with plaid_client(lambda _: httpx.Response(200, json={'key': key})) as client:
            provider = PlaidProvider(context(plaid_env), client)
            denied = await provider.webhook_event(b'{}', {})
            assert not denied.ok and denied.status_code == 401
            cases = [
                ({'item_id': 'item', 'webhook_type': 'TRANSACTIONS', 'webhook_code': 'SYNC_UPDATES_AVAILABLE'}, True, False),
                ({'item_id': 'item', 'webhook_type': 'ITEM', 'webhook_code': 'PENDING_DISCONNECT'}, False, True),
                ({'item_id': 'item', 'webhook_type': 'ITEM', 'webhook_code': 'PENDING_EXPIRATION'}, False, True),
                ({'item_id': 'item', 'webhook_type': 'ITEM', 'webhook_code': 'ERROR', 'error': {'error_code': 'ITEM_LOGIN_REQUIRED'}}, False, True),
                ({'item_id': 'item', 'webhook_type': 'ITEM', 'webhook_code': 'ERROR', 'error': 'invalid'}, False, False),
                ({'item_id': 'item', 'webhook_type': 'ITEM', 'webhook_code': 'OTHER'}, False, False),
            ]
            for event, sync, reconnect in cases:
                raw = json.dumps(event).encode()
                result = await provider.webhook_event(raw, {'Plaid-Verification': sign(raw)})
                assert result.ok and result.item_id == 'item' and result.sync is sync and result.reconnect is reconnect
    asyncio.run(scenario())


def test_public_typed_factory_opens_without_http_or_host_encryption_key():
    import asyncio

    from whab_plugin_plaid.plugin import plugin

    from whab.plugins import BankAdapter, BankProviderContext

    assert type(plugin.bank) is BankAdapter
    provider = plugin.bank.factory(BankProviderContext('fictional-budget', 'sandbox', {}))
    try:
        assert provider.config['configured'] is False
        assert 'TOKEN_ENCRYPTION_KEY' not in provider.config['missingConfig']
    finally:
        asyncio.run(provider.close())
