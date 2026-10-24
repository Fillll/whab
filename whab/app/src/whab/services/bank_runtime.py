"""Host-owned provider continuations, encrypted capture and native receipt apply.

Only provider transport DTOs cross the worker boundary. The host owns admission,
keys, provider identities and all protected/financial writes. A captured native
request is recovery input; the backend's atomic receipt remains its authority.
"""

import asyncio
import base64
from collections.abc import Mapping
from copy import deepcopy
from functools import partial
from uuid import uuid4

from whab.domain.validation import validate_label
from whab.plugins import BankError
from whab.plugins.bank_adapter import BankProviderContext, ProviderFailure
from whab.plugins.bank_continuations import BankContinuations, ContinuationScope
from whab.plugins.bank_crypto import BankCipher
from whab.plugins.effect_store import EffectError
from whab.plugins.storage_transfer import canonical_storage_json, read_storage_json

from .bank_native import BankNativeBroker, CapturedBankOperation

_TERMINAL = frozenset({'complete', 'cancelled', 'expired'})


async def _blocking(callback, *args, **kwargs):
    """Do not abandon admitted disk/provider/native work on caller cancellation."""
    task = asyncio.create_task(asyncio.to_thread(partial(callback, *args, **kwargs)))
    cancelled = None
    while True:
        try:
            result = await asyncio.shield(task)
            if cancelled is not None:
                raise cancelled
            return result
        except asyncio.CancelledError as error:
            if task.done():
                # Observe the original worker result, then preserve cancellation.
                if not task.cancelled():
                    task.exception()
                raise
            cancelled = error


def _status(value):
    return {'flow_id': value.identifier, 'generation': value.generation, 'kind': value.kind,
            'stage': value.stage, 'deadline_ms': value.deadline_ms}


def _record(raw):
    value = read_storage_json(raw, limit=64 * 1024)
    if type(value) is not dict:
        raise EffectError('invalid_effect')
    return value


class WorkerBankProvider:
    """Synchronous bounded worker RPC, without persistence capabilities."""

    def __init__(self, worker, context, *, owns_worker=False):
        if type(context) is not BankProviderContext:
            raise EffectError('invalid_effect')
        self.worker, self.owns_worker = worker, owns_worker
        self.closed = False
        try:
            self.config = worker.provider('open', {'budget_id': context.budget_id,
                'environment': context.environment, 'config': dict(context.config)})
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def call(self, command, **values):
        if self.closed:
            raise EffectError('effect_unavailable')
        if command == 'webhook_event':
            values = {**values, 'raw': base64.b64encode(values['raw']).decode('ascii')}
        return self.worker.provider(command, values)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.owns_worker:
            self.worker.close()
        elif self.worker.healthy:
            self.worker.provider('close', {})


class BankCoordinator:
    """One selected provider engine, with a retained host-private journal owner.

    Lifecycle finalization must call ``prepare_close`` after admission drains.
    The journal can outlive an engine and be rebound after approved replacement;
    recovery never invokes provider HTTP. ``close`` preserves unfinished records.
    """

    def __init__(self, provider, tracked, bank_store, *, budget_id, environment, provider_id,
                 generation, home=None, encryption_key=None, journal=None, cipher=None,
                 code_identity='installed', background_scope=None, clock=None):
        self.provider, self.tracked, self.bank_store = provider, tracked, bank_store
        self.scope = ContinuationScope(budget_id, environment, provider_id, generation)
        self.code_identity = code_identity
        if type(code_identity) is not str or not 1 <= len(code_identity) <= 200:
            raise EffectError('invalid_effect')
        self.config = deepcopy(dict(provider.config))
        self.cipher = cipher or BankCipher(encryption_key)
        self.broker = BankNativeBroker(tracked, bank_store, budget_id, environment, provider_id)
        self.journal = journal or BankContinuations(home, budget_id, environment, provider_id,
            seal=self.cipher.seal, open=self.cipher.open, **({'clock': clock} if clock is not None else {}))
        self.owns_journal = journal is None
        self.background_scope = background_scope
        self._serial = asyncio.Lock()
        self._queued = {}
        self._scheduled = set()
        self._closed = False
        self._closing = None

    def _ready(self):
        if self._closed:
            raise EffectError('effect_unavailable')
        if not self.config.get('configured'):
            raise ProviderFailure('not_configured')
        self.broker.admit()

    def _original(self, flow_id):
        scope = self.journal.scope_for(flow_id)
        return scope, self.journal.status(scope, flow_id)

    def _request(self, scope, status):
        raw = (self.journal.secret(scope, status.identifier) if status.stage == 'ready'
               else self.journal.exchange_request(scope, status.identifier))
        value = _record(raw)
        if (value.get('action') not in {'link', 'update', 'disconnect'} or type(value.get('item_id')) not in {str, type(None)}
                or type(value.get('code_identity')) is not str):
            raise EffectError('invalid_effect')
        if value['item_id'] is not None:
            self.bank_store.item_identity(value['item_id'])
        return value

    def _item(self, item_id):
        item = self.bank_store.get_item(item_id)
        if item is None:
            raise EffectError('continuation_missing')
        return item

    def _access(self, item_id):
        item = self._item(item_id)
        return self.cipher.decrypt_token(item['token'], self.bank_store.item_identity(item_id))

    def _begin(self, item_id):
        self._ready()
        if item_id is not None:
            self._item(item_id)
        action = 'update' if item_id is not None else 'link'
        for previous in self.journal.statuses():
            scope, previous = self._original(previous.identifier)
            if previous.stage in _TERMINAL:
                continue
            if previous.stage == 'ready':
                request = self._request(scope, previous)
                if (request['action'], request['item_id'], request['code_identity']) == (action, item_id, self.code_identity):
                    return {**_status(previous), 'link_token': request['link_token']}
            raise EffectError('continuation_busy')
        token = self.provider.call('connection_token', item_id=item_id,
                                   access_token=self._access(item_id) if item_id is not None else None)
        secret = canonical_storage_json({'action': action, 'item_id': item_id, 'code_identity': self.code_identity,
                                         'link_token': token})
        status = self.journal.begin(self.scope, action, secret=secret)
        return {**_status(status), 'link_token': token}

    async def begin_link(self, item_id=None):
        async with self._serial:
            return await _blocking(self._begin, item_id)

    def _capture(self, scope, status, public_token, institution_name):
        request = self._request(scope, status)
        action = request['action']
        if status.stage == 'ready':
            self._ready()
            if request['code_identity'] != self.code_identity:
                raise EffectError('continuation_busy')
            if action == 'link' and (type(public_token) is not str or not 1 <= len(public_token) <= 500):
                raise EffectError('continuation_missing')
            if action != 'link' and public_token is not None:
                raise EffectError('effect_conflict')
            if institution_name is not None and (type(institution_name) is not str or len(institution_name) > 200):
                raise EffectError('invalid_effect')
            label = validate_label(institution_name) if institution_name is not None else 'Connected institution'
            label.encode('utf-8')
            request = {**request, 'public_token': public_token, 'institution_name': label}
            if action == 'disconnect':
                request['access_token'] = self._access(request['item_id'])
        else:
            # A reload can finish captured work without the old browser token.
            # Any supplied fields must still identify the exact original intent.
            if ((public_token is not None and public_token != request.get('public_token'))
                    or (institution_name is not None and institution_name != request.get('institution_name'))):
                raise EffectError('effect_conflict')
        raw = canonical_storage_json(request)

        def send(_raw):
            if action == 'link':
                response = self.provider.call('exchange_token', public_token=request['public_token'])
            elif action == 'disconnect':
                self.provider.call('remove_token', access_token=request['access_token'])
                response = {'item_id': request['item_id'], 'disconnected': True}
            else:
                # Link update changes the existing provider Item in place. There
                # is no public-token exchange in this flow.
                response = {'item_id': request['item_id'], 'updated': True}
            return canonical_storage_json(response)

        self.journal.exchange(scope, status.identifier, status.identifier, raw, send)
        return request

    def _captured(self, scope, flow_id, request):
        saved = self.journal.native_request(scope, flow_id)
        if saved is not None:
            fingerprint, raw = saved
            captured = self.broker.validate(CapturedBankOperation.read(raw))
            if captured.operation.operation_id != flow_id or captured.operation.fingerprint != fingerprint:
                raise EffectError('invalid_effect')
            return captured
        response = _record(self.journal.captured_response(scope, flow_id))
        action, item_id = request['action'], response['item_id']
        stored_id = self.bank_store.item_identity(item_id)
        if action == 'link':
            if response.keys() != {'item_id', 'access_token'}:
                raise EffectError('invalid_effect')
            payload = {'itemId': stored_id, 'encryptedToken': self.cipher.encrypt_token(response['access_token'], stored_id),
                       'institutionName': request['institution_name'], 'providerId': self.scope.provider_id}
            name = 'save_item'
        elif action == 'update':
            if response != {'item_id': request['item_id'], 'updated': True}:
                raise EffectError('invalid_effect')
            payload, name = {'itemId': stored_id, 'status': 'connected', 'error': None}, 'set_item_status'
        else:
            if response != {'item_id': request['item_id'], 'disconnected': True}:
                raise EffectError('invalid_effect')
            payload, name = {'itemId': stored_id}, 'disconnect_item'
        captured = self.broker.prepare(flow_id, item_id, name, payload)
        self.journal.prepare_native(scope, flow_id, captured.operation.fingerprint, captured.encode())
        return captured

    def _apply(self, scope, flow_id, request):
        with self.tracked.lock:
            captured = self._captured(scope, flow_id, request)
            self.journal.apply(scope, flow_id, native_fingerprint=captured.operation.fingerprint,
                lookup=lambda *_: self.broker.lookup(captured, prove_absence=True),
                commit=lambda *_: self.broker.execute(captured))
        return captured.item_id

    def _complete(self, flow_id, public_token, institution_name):
        scope, status = self._original(flow_id)
        if status.stage in {'cancelled', 'expired'}:
            raise EffectError('continuation_expired')
        request = self._capture(scope, status, public_token, institution_name)
        self._apply(scope, flow_id, request)
        return _status(self.journal.status(scope, flow_id))

    async def complete_link(self, flow_id, public_token=None, institution_name=None):
        async with self._serial:
            result = await _blocking(self._complete, flow_id, public_token, institution_name)
            self._scheduled.intersection_update(status.identifier for status in self.journal.statuses())
            if self.background_scope is not None and flow_id not in self._scheduled:
                scope, status = self._original(flow_id)
                request = self._request(scope, status)
                if request['action'] != 'disconnect':
                    response = _record(self.journal.captured_response(scope, flow_id))
                    self.queue_sync(response['item_id'])
                self._scheduled.add(flow_id)
            return result

    def _status(self, flow_id):
        scope, status = self._original(flow_id)
        if status.stage in {'captured', 'apply_uncertain'}:
            saved = self.journal.native_request(scope, flow_id)
            if saved is not None:
                fingerprint, raw = saved
                captured = self.broker.validate(CapturedBankOperation.read(raw))
                if captured.operation.operation_id != flow_id or captured.operation.fingerprint != fingerprint:
                    raise EffectError('invalid_effect')
                with self.tracked.lock:
                    evidence = self.broker.lookup(captured)
                    if evidence.outcome == 'committed':
                        self.journal.apply(scope, flow_id, native_fingerprint=fingerprint,
                            lookup=lambda *_: evidence, commit=lambda *_: (_ for _ in ()).throw(EffectError('apply_uncertain')))
                status = self.journal.status(scope, flow_id)
        return _status(status)

    async def continuation_status(self, flow_id):
        async with self._serial:
            return await _blocking(self._status, flow_id)

    async def cancel_link(self, flow_id):
        async with self._serial:
            scope = self.journal.scope_for(flow_id)
            return await _blocking(lambda: _status(self.journal.cancel(scope, flow_id)))

    def _command(self, item_id, name, payload):
        with self.tracked.lock:
            captured = self.broker.prepare(uuid4().hex, item_id, name, payload)
            return self.broker.execute(captured)

    def _sync(self, item_id):
        self._ready()
        item = self._item(item_id)
        try:
            response = self.provider.call('read_sync', item_id=item_id, access_token=self._access(item_id),
                cursor=item['cursor'], institution_name=item['institutionName'])
        except ProviderFailure as error:
            self._command(item_id, 'set_item_status', {'itemId': self.bank_store.item_identity(item_id),
                'status': 'reconnect' if error.code == 'reconnect' else 'error', 'error': str(error)})
            raise BankError(str(error)) from None
        # Native uncertain failures propagate without another status mutation.
        with self.tracked.lock:
            payload = self.bank_store.prepare_sync(item_id, response['changes'], response['cursor'], response['accounts'],
                response['historical_complete'], expected_cursor=item['cursor'])
            return read_storage_json(self._command(item_id, 'apply_sync', payload).result)

    async def sync(self, item_id=None):
        async with self._serial:
            if item_id is not None:
                return await _blocking(self._sync, item_id)
            results = [await _blocking(self._sync, item['id']) for item in self.bank_store.items()
                       if self.bank_store.get_item(item['id'])['token'] is not None]
            return {name: sum(result[name] for result in results) for name in ('added', 'modified', 'removed')}

    def _disconnect(self, item_id):
        selected = None
        for previous in self.journal.statuses():
            scope, previous = self._original(previous.identifier)
            if previous.stage in _TERMINAL:
                continue
            request = self._request(scope, previous)
            if request['action'] == 'disconnect' and request['item_id'] == item_id:
                selected = previous
            else:
                raise EffectError('continuation_busy')
        if selected is None:
            item = self._item(item_id)
            if item['status'] == 'disconnected' and item['token'] is None:
                return
            self._ready()
            self._access(item_id)  # Refuse a locked Item before creating any new intent.
            selected = self.journal.begin(self.scope, 'update', secret=canonical_storage_json({
                'action': 'disconnect', 'item_id': item_id, 'code_identity': self.code_identity, 'link_token': ''}))
        self._complete(selected.identifier, None, None)

    async def disconnect(self, item_id):
        async with self._serial:
            return await _blocking(self._disconnect, item_id)

    async def webhook(self, raw, headers):
        from whab.plugins import WebhookResult
        async with self._serial:
            result = await _blocking(self.provider.call, 'webhook_event', raw=raw, headers=dict(headers))
            if result.item_id is not None and self.bank_store.get_item(result.item_id) is not None:
                if result.reconnect:
                    await _blocking(self._command, result.item_id, 'set_item_status', {
                        'itemId': self.bank_store.item_identity(result.item_id), 'status': 'reconnect',
                        'error': 'The bank connection requires a scoped update through its provider.'})
                if result.sync:
                    self.queue_sync(result.item_id)
            return WebhookResult(result.ok, result.status_code)

    def queue_sync(self, item_id):
        if item_id in self._queued:
            self._queued[item_id]['again'] = True
            return
        if len(self._queued) >= 128:
            raise EffectError('continuation_busy')
        record = {'again': False, 'task': None}
        async def run():
            try:
                while True:
                    record['again'] = False
                    if self.background_scope is None:
                        await self.sync(item_id)
                    else:
                        async with self.background_scope():
                            await self.sync(item_id)
                    if not record['again']:
                        break
            except Exception:
                pass  # Item status/native recovery owns the safe visible outcome.
            finally:
                self._queued.pop(item_id, None)
        record['task'] = asyncio.create_task(run())
        self._queued[item_id] = record

    def prepare_close(self):
        if self._serial.locked() or self._queued:
            raise EffectError('continuation_busy')
        self.journal.prepare_close()

    async def _close(self):
        self._closed = True
        # Shutdown is separate from replacement: it preserves unfinished intent.
        for record in tuple(self._queued.values()):
            await asyncio.shield(record['task'])
        async with self._serial:
            try:
                await _blocking(self.provider.close)
            finally:
                if self.owns_journal:
                    await _blocking(self.journal.close)

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        return await asyncio.shield(self._closing)


class LeasedBanks(Mapping):
    """Generation-aware bank access with cancellation-safe admitted operations."""

    def __init__(self, runtime):
        self.runtime = runtime

    def __iter__(self):
        with self.runtime.lease() as lease:
            return iter(tuple(lease.service('banks')))

    def __len__(self):
        with self.runtime.lease() as lease:
            return len(lease.service('banks'))

    def __getitem__(self, owner):
        with self.runtime.lease() as lease:
            if owner not in lease.service('banks'):
                raise KeyError(owner)
        return _BankHandle(self.runtime, owner)


class _BankHandle:
    _METHODS = frozenset({'begin_link', 'complete_link', 'continuation_status', 'cancel_link',
                          'link_token', 'exchange', 'sync', 'disconnect', 'webhook', 'close'})

    def __init__(self, runtime, owner):
        self.runtime, self.owner = runtime, owner

    @property
    def config(self):
        with self.runtime.lease() as lease:
            return deepcopy(dict(lease.service('banks')[self.owner].config))

    def __getattr__(self, method):
        if method not in self._METHODS:
            raise AttributeError(method)
        with self.runtime.lease() as lease:
            if not callable(getattr(lease.service('banks')[self.owner], method, None)):
                raise AttributeError(method)

        async def call(*args, **kwargs):
            async def operation():
                async with self.runtime.request() as lease:
                    return await getattr(lease.service('banks')[self.owner], method)(*args, **kwargs)
            return await self.runtime._owned(operation())
        return call


class BankStores(Mapping):
    """Provider-scoped persistence views exist only for the pinned active bank."""

    def __init__(self, banks, factory):
        self.banks, self.factory = banks, factory

    def __iter__(self):
        return iter(self.banks)

    def __len__(self):
        return len(self.banks)

    def __getitem__(self, owner):
        if owner not in self.banks:
            raise KeyError(owner)
        return self.factory(owner)
