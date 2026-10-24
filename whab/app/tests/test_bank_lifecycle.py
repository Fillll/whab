"""Host key boundaries, retained private journals and candidate cleanup ownership."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from whab.budgets.session import TrackedStorage
from whab.config import Settings
from whab.plugins import (
    BankAdapter,
    BankProviderContext,
    Capability,
    Plugin,
    PluginContract,
    PluginError,
)
from whab.plugins.bank_continuations import BankContinuations, ContinuationScope
from whab.plugins.bank_crypto import BankCipher
from whab.plugins.effect_store import EffectError
from whab.plugins.provider_worker import WorkerBankFactory
from whab.services.bank_lifecycle import BankLifecycle
from whab.services.bank_runtime import WorkerBankProvider


class Worker:
    info = {'distribution': 'whab_plugin_fictional_bank', 'version': '1.0.0'}
    plugin_id = 'fictional-bank'
    healthy = True
    cleanup_pending = False

    def __init__(self, *, failure=None, cleanup_failure=False):
        self.calls, self.failure, self.cleanup_failure, self.closed = [], failure, cleanup_failure, False

    def provider(self, command, values):
        self.calls.append((command, values))
        if command == 'open':
            if self.failure is not None:
                raise self.failure
            return {'environment': 'sandbox', 'configured': True, 'liveEnabled': True, 'missingConfig': []}
        assert command == 'close'
        if self.cleanup_failure:
            raise OSError('fictional-private-cleanup')
        return None

    def close(self):
        self.closed = True
        self.healthy = False
        self.cleanup_pending = self.cleanup_failure
        if self.cleanup_failure:
            raise OSError('fictional-private-cleanup')


def settings(home, *, key='11' * 32):
    return Settings(home, 5173, 'sandbox', '', '', key, False, '', '', (), ui='')


def plugin(worker=None):
    factory = WorkerBankFactory(worker) if worker is not None else lambda _: None
    return Plugin('fictional-bank', 'Fictional provider', bank=BankAdapter(factory),
        contract=PluginContract(lifecycle='live', authority='local', provides=(Capability('lifecycle.background'),)))


def test_missing_host_key_opens_no_plaintext_journal_and_provider_receives_no_host_key(tmp_path, store):
    worker = Worker()
    lifecycle = BankLifecycle(settings(tmp_path, key=''), TrackedStorage(store), 'default', home=tmp_path)
    service = lifecycle.create(plugin(worker), 1)
    assert service.config['configured'] is False and 'TOKEN_ENCRYPTION_KEY' in service.config['missingConfig']
    _, context = worker.calls[0]
    assert 'TOKEN_ENCRYPTION_KEY' not in context['config']
    assert not (tmp_path / 'plugin-workers').exists()
    with pytest.raises(ValueError, match='not configured'):
        asyncio.run(service.begin_link())
    asyncio.run(lifecycle.close())
    assert [call[0] for call in worker.calls] == ['open', 'close']


def test_wrong_retained_journal_key_refuses_without_reset_and_releases_failed_open_lock(tmp_path, store):
    directory = tmp_path / 'plugin-workers' / 'bank-continuations' / 'sandbox' / 'fictional-bank'
    cipher = BankCipher('11' * 32)
    scope = ContinuationScope('default', 'sandbox', 'fictional-bank', 7)
    journal = BankContinuations(directory, 'default', 'sandbox', 'fictional-bank', seal=cipher.seal, open=cipher.open)
    flow = journal.begin(scope, 'link', secret=b'fictional-private-link')
    journal.close()
    before = (directory / 'bank-continuations.json').read_bytes()
    for key in ('', '22' * 32):
        with pytest.raises(EffectError):
            BankLifecycle(settings(tmp_path, key=key), TrackedStorage(store), 'default', home=tmp_path)
        assert (directory / 'bank-continuations.json').read_bytes() == before
    lifecycle = BankLifecycle(settings(tmp_path), TrackedStorage(store), 'default', home=tmp_path)
    assert lifecycle.journals['fictional-bank'].status(scope, flow.identifier).stage == 'ready'
    asyncio.run(lifecycle.close())


def test_candidate_failure_owns_installed_child_until_cleanup_and_preserves_cancellation(tmp_path, store, monkeypatch):
    from whab.plugins.bank_adapter import BankProviderContext

    failure = asyncio.CancelledError('fictional cancellation identity')
    worker = Worker(failure=failure, cleanup_failure=True)
    context = BankProviderContext('default', 'sandbox', {})
    with pytest.raises(asyncio.CancelledError) as error:
        WorkerBankProvider(worker, context, owns_worker=True)
    assert error.value is failure and worker.closed

    worker = Worker(failure=RuntimeError('fictional-private-factory'), cleanup_failure=True)
    monkeypatch.setattr('whab.services.bank_lifecycle.ReportWorker', lambda *_, **__: worker)
    entry = SimpleNamespace(name='fictional-bank', dist=SimpleNamespace(metadata={'Name': worker.info['distribution']}, version='1.0.0'))
    monkeypatch.setattr('whab.services.bank_lifecycle.metadata.entry_points', lambda **_: (entry,))
    lifecycle = BankLifecycle(settings(tmp_path), TrackedStorage(store), 'default', home=tmp_path)
    with pytest.raises(PluginError) as error:
        lifecycle.create(plugin(), 1)
    assert error.value.code == 'bank_startup_failed' and 'fictional-private' not in str(error.value)
    assert worker in lifecycle._workers
    with pytest.raises(PluginError) as error:
        asyncio.run(lifecycle.close())
    assert error.value.code == 'worker_cleanup_pending'
    assert not lifecycle.journals


def test_hello_failure_retains_actual_child_when_bounded_reap_fails_until_lifecycle_retry(tmp_path, store, monkeypatch):
    import base64
    import os
    import signal
    import subprocess
    import sys

    from whab.plugins import report_worker
    from whab.plugins.wire import Frame

    packet = base64.b64encode(Frame({'bad': 'hello'}).encode()).decode()
    source = (f'import base64,signal,sys;sys.stdout.buffer.write(base64.b64decode({packet!r}));'
              'sys.stdout.buffer.flush();signal.pause()')
    popen, children = subprocess.Popen, []

    def start(_args, **kwargs):
        child = popen([sys.executable, '-I', '-u', '-c', source], **kwargs)
        children.append(child)
        return child

    entry = SimpleNamespace(name='fictional-bank', dist=SimpleNamespace(metadata={'Name': 'whab_plugin_fictional_bank'}, version='1.0.0'))
    lifecycle = BankLifecycle(settings(tmp_path), TrackedStorage(store), 'default', home=tmp_path)
    try:
        with monkeypatch.context() as failure:
            failure.setattr(report_worker.subprocess, 'Popen', start)
            failure.setattr(report_worker, '_stop_process_group', lambda _: (_ for _ in ()).throw(OSError('fictional reap failure')))
            failure.setattr('whab.services.bank_lifecycle.metadata.entry_points', lambda **_: (entry,))
            with pytest.raises(PluginError) as error:
                lifecycle.create(plugin(), 1)
            assert error.value.code == 'bank_startup_failed'
            assert len(lifecycle._workers) == 1 and children[0].poll() is None
            worker, = lifecycle._workers
            assert worker.cleanup_pending
            with pytest.raises(PluginError) as error:
                asyncio.run(lifecycle.close())
            assert error.value.code == 'worker_cleanup_pending'
            assert worker in lifecycle._workers and children[0].poll() is None
        asyncio.run(lifecycle.close())
        assert not lifecycle._workers and children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def test_initial_bank_resource_keeps_service_holder_until_failed_close_really_finishes(tmp_path, store):
    from fastapi.testclient import TestClient
    from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

    from whab.app import create_app
    from whab.budgets.runtime import RuntimeCandidate
    from whab.plugins import PluginRegistry
    from whab.services.bank_runtime import BankCoordinator
    from whab.storage.bank import BankStore

    worker = Worker(cleanup_failure=True)
    provider = WorkerBankProvider(worker, BankProviderContext('default', 'sandbox', {}), owns_worker=True)
    tracked = TrackedStorage(store)
    service = BankCoordinator(provider, tracked, BankStore(tracked, 'fictional-bank'), budget_id='default', environment='sandbox',
                              provider_id='fictional-bank', generation=1, home=tmp_path / 'injected-flow', encryption_key='11' * 32)
    descriptor = plugin(worker)
    app = create_app(store=store, settings=replace(settings(tmp_path), plugins=('fictional-bank',)),
        plugins=PluginRegistry((sqlite_plugin, descriptor)), bank_services={'fictional-bank': service})
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        runtime, lifecycle = app.state.budget_runtime, app.state.bank_lifecycle
        resource = lifecycle.resources[id(service)]
        async def remove():
            def prepare(preparation):
                active = preparation.active
                return RuntimeCandidate({**active.services, 'banks': {}}, tuple(owner for owner in active.resources if owner is not resource))
            await runtime.transition(prepare)
        client.portal.call(remove)
        assert worker.cleanup_pending and service in lifecycle.services
        # This branch's base runtime drops failed owners; directly invoke its
        # retained callback exactly as the integrated runtime's retry will do.
        worker.cleanup_failure = False
        client.portal.call(resource.close)
        assert not worker.cleanup_pending and service not in lifecycle.services
        assert not worker.healthy


def test_remote_authority_rejects_live_bank_enable_before_factory(tmp_path, store):
    lifecycle = BankLifecycle(settings(tmp_path), TrackedStorage(store), 'default', home=tmp_path, allow_banks=False)
    candidate = plugin(Worker())
    with pytest.raises(PluginError) as error:
        lifecycle.preflight({}, (candidate,))
    assert error.value.code == 'invalid_configuration'
    asyncio.run(lifecycle.close())


def test_partial_legacy_context_service_can_remain_explicit_host_test_injection(tmp_path, store):
    closed = []
    async def close():
        closed.append(True)
    service = SimpleNamespace(config={}, close=close)
    lifecycle = BankLifecycle(settings(tmp_path), TrackedStorage(store), 'default', home=tmp_path)
    descriptor = replace(plugin(), bank=lambda _: pytest.fail('Explicit injection is the service'))
    assert lifecycle.create(descriptor, 1, injected=service) is service
    lifecycle.preflight({'fictional-bank': service}, (descriptor,))
    with pytest.raises(PluginError) as error:
        lifecycle.preflight({'fictional-bank': service}, ())
    assert error.value.code == 'restart_required'
    asyncio.run(lifecycle.close())
    assert closed == [True]


def test_app_tail_failure_releases_retained_bank_journal_without_opening_provider(tmp_path, store, monkeypatch):
    from whab.app import create_app
    from whab.plugins import PluginRegistry
    from whab.plugins.ui_selection import UISelectionStore

    directory = tmp_path / 'plugin-workers' / 'bank-continuations' / 'sandbox' / 'fictional-bank'
    cipher = BankCipher('11' * 32)
    journal = BankContinuations(directory, 'default', 'sandbox', 'fictional-bank', seal=cipher.seal, open=cipher.open)
    scope = ContinuationScope('default', 'sandbox', 'fictional-bank', 7)
    flow = journal.begin(scope, 'link', secret=b'fictional-private-link')
    journal.close()
    original = (directory / 'bank-continuations.json').read_bytes()
    error = RuntimeError('Fictional construction tail failure')

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr('whab.app.StorageRuntime', fail)
    with pytest.raises(RuntimeError) as caught:
        create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry(()))
    assert caught.value is error
    assert (directory / 'bank-continuations.json').read_bytes() == original
    reopened = BankContinuations(directory, 'default', 'sandbox', 'fictional-bank', seal=cipher.seal, open=cipher.open)
    try:
        assert reopened.status(scope, flow.identifier).generation == 7
    finally:
        reopened.close()
    with UISelectionStore(tmp_path, 'default', 'sandbox', None):
        pass
    assert store.accounts('personal') == []
