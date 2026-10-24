"""Actual installed fictional provider wheels; no network or bank connections."""

import asyncio
from functools import partial
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.plugins.test_environments import approve, local_closure, make_wheel  # noqa: F401, F811
from whab.app import create_app
from whab.config import Settings
from whab.plugins import (
    BankAdapter,
    BankUI,
    Capability,
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    PluginResources,
)
from whab.plugins.environments import prepare_environment
from whab.plugins.worker_selection import save_reference


def provider_wheel(tmp_path, version, *, broken=False):
    source = f'''
from whab.plugins import BankAdapter, BankUI, Capability, Plugin, PluginContract, PluginResources, ProviderWebhook
class Provider:
    def __init__(self, context):
        {"raise ValueError('fictional-private-readiness-failure')" if broken else "self.config = {'environment': context.environment, 'configured': True, 'liveEnabled': True, 'missingConfig': []}"}
    async def connection_token(self, item_id, access_token): return 'fictional-link-{version}'
    async def exchange_token(self, public_token):
        if public_token == 'fictional-kill-child':
            import os
            os._exit(19)
        return {{'item_id': 'fictional-item', 'access_token': 'fictional-private-access'}}
    async def read_sync(self, item_id, access_token, cursor, institution_name):
        return {{'changes': {{'added': [], 'modified': [], 'removed': []}}, 'cursor': 'cursor-{version}', 'accounts': [], 'historical_complete': True}}
    async def remove_token(self, access_token): return None
    async def webhook_event(self, raw, headers): return ProviderWebhook(False, 401)
    async def close(self): return None
plugin = Plugin('fictional-reports', 'Fictional provider', bank=BankAdapter(Provider),
    bank_ui=BankUI(resources=PluginResources({{'templates/accounts.html': b'<p>fictional-provider-{version}</p>',
        'static/connect.js': b'/* fictional-connect-{version} */', 'static/oauth.js': b'/* fictional-oauth-{version} */'}})),
    contract=PluginContract(lifecycle='live', authority='local', provides=(Capability('ingestion.bank'), Capability('lifecycle.background'))))
'''
    return approve(make_wheel(tmp_path, version, source=source))


def test_installed_provider_upgrade_pending_guard_versioned_resources_and_reopen(tmp_path, local_closure, store, monkeypatch):  # noqa: F811
    from whab_plugin_sqlite.plugin import plugin as sqlite
    from whab_plugin_web.plugin import plugin as web

    template = Plugin('fictional-reports', 'Fictional provider', bank=BankAdapter(lambda _: pytest.fail('Host cannot construct provider')),
        bank_ui=BankUI(resources=PluginResources({'templates/accounts.html': b'<p>host resource</p>', 'static/connect.js': b'/* host */'})),
        contract=PluginContract(lifecycle='live', authority='local', provides=(Capability('ingestion.bank'), Capability('lifecycle.background'))))
    entry = SimpleNamespace(name=template.id, group='whab.plugins', load=lambda: template)
    monkeypatch.setattr('whab.plugins.management.installed_entrypoints', lambda: (entry,))
    monkeypatch.setattr('whab.app.installed_entrypoints', lambda: (entry,))
    settings = Settings(tmp_path / 'runtime', 5173, 'sandbox', '', '', '11' * 32, False, '', '', (template.id,))
    first = prepare_environment(settings.home / 'plugin-workers' / 'environments', template.id,
                                (*local_closure, provider_wheel(tmp_path, '1.0.0')))
    save_reference(settings.home, first)
    old_ready = (first.path / 'ready.json').read_bytes()
    def registry():
        return PluginRegistry((sqlite, web, template))
    app = create_app(store=store, settings=settings, plugins=registry())
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        prefix = '/api/banks/fictional-reports'
        pending = client.post(prefix + '/link-token', json={}).json()
        assert pending['link_token'] == 'fictional-link-1.0.0'
        assert client.post(prefix + '/link-token', json={}).json() == pending
        with pytest.raises(PluginError) as error:
            client.portal.call(partial(app.state.live_plugins.upgrade, template.id, (*local_closure, provider_wheel(tmp_path, '2.0.0'))))
        assert error.value.code == 'continuation_busy'
        assert client.delete(prefix + '/continuations/' + pending['flow_id']).json()['stage'] == 'cancelled'
        before = app.state.budget_runtime._active.services['plugins'].bank_identity(template.id)
        old_asset = f'/banks/{template.id}/{before}/static/connect.js'
        assert client.get(old_asset).content == b'/* fictional-connect-1.0.0 */'
        flow = client.post(prefix + '/link-token', json={}).json()
        assert client.post(prefix + '/exchange', json={'flowId': flow['flow_id'], 'publicToken': 'fictional-public-token'}).json()['stage'] == 'complete'
        async def settle_background():
            bank = app.state.budget_runtime._active.services['banks'][template.id]
            if bank._queued:
                await asyncio.gather(*(record['task'] for record in tuple(bank._queued.values())))
        client.portal.call(settle_background)
        assert app.state.bank_stores[template.id].get_item('fictional-item')['provider'] == template.id
        with pytest.raises(Exception):
            client.portal.call(partial(app.state.live_plugins.upgrade, template.id, (*local_closure, provider_wheel(tmp_path, '3.0.0', broken=True))))
        assert client.get(old_asset).content == b'/* fictional-connect-1.0.0 */'
        assert client.portal.call(partial(app.state.live_plugins.upgrade, template.id, (*local_closure, provider_wheel(tmp_path, '2.0.0')))) == 2
        current = app.state.budget_runtime._active.services['plugins'].bank_identity(template.id)
        assert current != before
        assert client.get(f'/banks/{template.id}/{current}/static/connect.js').content == b'/* fictional-connect-2.0.0 */'
        assert client.get(old_asset).content == b'/* fictional-connect-1.0.0 */'
        # Protected existing Item/cursor stays host-owned across the child swap.
        sync = client.post(prefix + '/sync', json={'itemId': 'fictional-item'})
        assert sync.status_code == 200 and sync.json() == {'ok': True, 'added': 0, 'modified': 0, 'removed': 0}
        assert app.state.bank_stores[template.id].get_item('fictional-item')['cursor'] == 'cursor-2.0.0'
        assert client.post(prefix + '/webhook', json={}, headers={'host': 'fictional-public.invalid'}).status_code == 401
        assert client.post(prefix + '/sync', json={}, headers={'host': 'fictional-public.invalid'}).status_code == 403
        assert client.portal.call(partial(app.state.live_plugins.activate, ())) == 3
        assert client.post(prefix + '/link-token', json={}).status_code == 404
        assert client.portal.call(partial(app.state.live_plugins.activate, (template.id,))) == 4
        resumed = client.post(prefix + '/link-token', json={}).json()
        assert resumed['link_token'] == 'fictional-link-2.0.0'
        client.delete(prefix + '/continuations/' + resumed['flow_id'])
    assert (first.path / 'ready.json').read_bytes() == old_ready
    reopened = create_app(store=store, settings=settings, plugins=registry())
    with TestClient(reopened, base_url='https://127.0.0.1:5173') as client:
        resumed = client.post('/api/banks/fictional-reports/link-token', json={}).json()
        assert resumed['link_token'] == 'fictional-link-2.0.0'
        assert reopened.state.bank_stores[template.id].get_item('fictional-item')['cursor'] == 'cursor-2.0.0'
        # A real child process dies after durable intent. Reopening must preserve
        # uncertainty, with no automatic second provider exchange.
        dead = client.post('/api/banks/fictional-reports/exchange', json={
            'flowId': resumed['flow_id'], 'publicToken': 'fictional-kill-child'})
        assert dead.status_code == 409 and dead.json()['code'] == 'exchange_uncertain'
        assert client.get('/api/banks/fictional-reports/continuations/' + resumed['flow_id']).json()['stage'] == 'exchange_uncertain'
    third = create_app(store=store, settings=settings, plugins=registry())
    with TestClient(third, base_url='https://127.0.0.1:5173') as client:
        path = '/api/banks/fictional-reports'
        assert client.get(path + '/continuations/' + resumed['flow_id']).json()['stage'] == 'exchange_uncertain'
        assert client.post(path + '/exchange', json={'flowId': resumed['flow_id']}).status_code == 409
        assert client.post(path + '/link-token', json={}).status_code == 409
        binding = third.state.budget_runtime._active.services['plugins'].workers[template.id]
        assert binding.worker.healthy  # A repeat exchange would kill this child.
        with pytest.raises(PluginError) as error:
            client.portal.call(partial(third.state.live_plugins.activate, ()))
        assert error.value.code == 'continuation_busy'
