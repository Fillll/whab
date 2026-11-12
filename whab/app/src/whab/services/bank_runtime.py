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
from threading import RLock
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
        self.context = context
        self._active_config = dict(context.config)
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

    def call_with_config(self, config, command, **values):
        if self.closed:
            raise EffectError('effect_unavailable')
        if config != self._active_config:
            previous, self._active_config = self._active_config, None
            if previous is not None:
                try:
                    self.worker.provider('close', {})
                except BaseException:
                    self.closed = True
                    raise
            self.worker.provider('open', {'budget_id': self.context.budget_id,
                'environment': self.context.environment, 'config': dict(config)})
            self._active_config = dict(config)
        return self.call(command, **values)


class OnDemandBankProvider:
    """Pure provider clients live for one operation; native continuations stay here.

    A readiness probe captures validated configuration, then releases its child.
    An operation acquires a fresh, exactly pinned child and never replays a failed
    call. Durable exchange/import uncertainty remains the coordinator's concern.
    """

    def __init__(self, factory, release):
        self.factory, self.release = factory, release
        self.lock, self.closed = RLock(), False
        provider = factory()
        try:
            self.config = deepcopy(dict(provider.config))
        finally:
            self.release(provider)

    def call(self, command, **values):
        with self.lock:
            if self.closed:
                raise EffectError('effect_unavailable')
            provider = self.factory()
            try:
                return provider.call(command, **values)
            finally:
                self.release(provider)

    def close(self):
        with self.lock:
            self.closed = True

    def call_with_config(self, config, command, **values):
        with self.lock:
            if self.closed:
                raise EffectError('effect_unavailable')
            provider = self.factory(config)
            try:
                return provider.call(command, **values)
            finally:
                self.release(provider)


class BankCoordinator:
    """One selected provider engine, with a retained host-private journal owner.

    Lifecycle finalization must call ``prepare_close`` after admission drains.
    The journal can outlive an engine and be rebound after approved replacement;
    recovery never invokes provider HTTP. ``close`` preserves unfinished records.
    """

    def __init__(self, provider, tracked, bank_store, *, budget_id, environment, provider_id,
                 generation, home=None, encryption_key=None, journal=None, cipher=None,
                 code_identity='installed', background_scope=None, clock=None,
                 requires_account_setup=False, supports_investments=False, supports_account_metadata=False, automatic_sync=lambda: True, connections=None,
                 shared_event=None, profiles=None):
        self.provider, self.tracked, self.bank_store = provider, tracked, bank_store
        self.scope = ContinuationScope(budget_id, environment, provider_id, generation)
        self.code_identity = code_identity
        if type(code_identity) is not str or not 1 <= len(code_identity) <= 200:
            raise EffectError('invalid_effect')
        self.config = deepcopy(dict(provider.config))
        self.connections = connections
        self.shared_event = shared_event
        self.profiles = profiles
        if profiles is not None and profiles.public():
            self.config.update(configured=True, missingConfig=[])
        if connections is not None:
            self.config['sharedConnections'] = True
        self.requires_account_setup, self.automatic_sync = requires_account_setup, automatic_sync
        if requires_account_setup:
            self.config["requiresAccountSetup"] = True
        self.supports_investments = supports_investments
        self.supports_account_metadata = supports_account_metadata
        if supports_investments:
            self.config["supportsInvestments"] = True
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

    def _provider_call(self, command, *, profile_id=None, **values):
        if self.profiles is None:
            return self.provider.call(command, **values)
        item_id = values.get('item_id')
        record = None
        if item_id is not None:
            record = self.connections.get(self.scope.provider_id, item_id)
            profile_id = record['profileId']
            values['item_id'] = record['providerItemId']
        config = self.profiles.config(profile_id or 'default')
        if record is not None and record['profilePrincipal'] not in (None, self.profiles.principal(config)):
            raise BankError('The bank connection belongs to another provider account. Restore its original profile.')
        config['WHAB_BANK_PROFILE'] = profile_id or 'default'
        return self.provider.call_with_config(config, command, **values)

    async def _operation(self, callback, *args):
        def run():
            if self.connections is None:
                return callback(*args)
            from whab.hosting import process_lock
            # One provider operation at a time across its attached budgets. Hold
            # this through native commit so revocation/detach cannot race import.
            with process_lock(self.connections.directory / (self.scope.provider_id + '-operations.lock')):
                return callback(*args)
        return await _blocking(run)

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
        if self.connections is not None:
            record = self.connections.get(self.scope.provider_id, item_id)
            if record['attachments'].get(self.scope.budget_id) != 'attached':
                raise BankError('Attach this connection to the budget before using it.')
        return item

    def _access(self, item_id):
        item = self._item(item_id)
        if self.connections is not None:
            record = self.connections.get(self.scope.provider_id, item_id)
            if self.profiles is not None and record['profilePrincipal'] not in (None,
                    self.profiles.principal(self.profiles.config(record['profileId']))):
                raise BankError('The bank connection belongs to another provider account. Restore its original profile.')
            return self.connections.access(self.scope.provider_id, item_id, self.scope.budget_id)
        return self.cipher.decrypt_token(item['token'], self.bank_store.item_identity(item_id))

    def _begin(self, item_id, profile_id='default'):
        self._ready()
        if item_id is not None:
            self._item(item_id)
        action = 'update' if item_id is not None else 'link'
        if self.profiles is not None:
            profile_id = self.connections.get(self.scope.provider_id, item_id)['profileId'] if item_id else profile_id
            self.profiles.config(profile_id)
        for previous in self.journal.statuses():
            scope, previous = self._original(previous.identifier)
            if previous.stage in _TERMINAL:
                continue
            if previous.stage == 'ready':
                request = self._request(scope, previous)
                if (request['action'], request['item_id'], request['code_identity'], request.get('profile_id', 'default')) == (action, item_id, self.code_identity, profile_id):
                    return {**_status(previous), 'link_token': request['link_token']}
            raise EffectError('continuation_busy')
        token = self._provider_call('connection_token', profile_id=profile_id, item_id=item_id,
                                   access_token=self._access(item_id) if item_id is not None else None)
        secret = canonical_storage_json({'action': action, 'item_id': item_id, 'code_identity': self.code_identity,
                                         'link_token': token, 'profile_id': profile_id})
        status = self.journal.begin(self.scope, action, secret=secret)
        return {**_status(status), 'link_token': token}

    async def begin_link(self, item_id=None, profile_id='default'):
        async with self._serial:
            return await self._operation(self._begin, item_id, profile_id)

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
                request['access_token'] = (self.connections.access(self.scope.provider_id, request['item_id'],
                    self.scope.budget_id, revocation=True) if self.connections else self._access(request['item_id']))
            elif action == 'update' and self.connections:
                self._access(request['item_id'])
        else:
            # A reload can finish captured work without the old browser token.
            # Any supplied fields must still identify the exact original intent.
            if ((public_token is not None and public_token != request.get('public_token'))
                    or (institution_name is not None and institution_name != request.get('institution_name'))):
                raise EffectError('effect_conflict')
        raw = canonical_storage_json(request)

        def send(_raw):
            if action == 'link':
                response = self._provider_call('exchange_token', profile_id=request.get('profile_id', 'default'), public_token=request['public_token'])
                if self.connections is not None:
                    raw_id = response['item_id']
                    response = {**response, 'item_id': self.connections.connection_id(self.scope.provider_id,
                        request.get('profile_id', 'default'), raw_id), 'provider_item_id': raw_id}
            elif action == 'disconnect':
                profile_id = self.connections.get(self.scope.provider_id, request['item_id'])['profileId'] if self.connections else None
                self._provider_call('remove_token', profile_id=profile_id, access_token=request['access_token'])
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
            if response.keys() not in ({'item_id', 'access_token'}, {'item_id', 'access_token', 'provider_item_id'}):
                raise EffectError('invalid_effect')
            payload = {'itemId': stored_id, 'encryptedToken': self.cipher.encrypt_token(response['access_token'], stored_id),
                       'institutionName': request['institution_name'], 'providerId': self.scope.provider_id}
            if self.connections is not None:
                self.connections.register(self.scope.provider_id, {'id': item_id, 'storedIdentity': stored_id,
                    'token': payload['encryptedToken'], 'institutionName': request['institution_name'],
                    'status': 'connected', 'error': None}, self.scope.budget_id,
                    profile_id=request.get('profile_id', 'default'), provider_item_id=response.get('provider_item_id', item_id),
                    profile_principal=self.profiles.principal(self.profiles.config(request.get('profile_id', 'default'))) if self.profiles else None)
                payload['encryptedToken'] = self.connections.reference(self.scope.provider_id, item_id)
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
        if self.connections is not None:
            response = _record(self.journal.captured_response(scope, flow_id))
            self.connections.update(self.scope.provider_id, response['item_id'],
                status='disconnected' if request['action'] == 'disconnect' else 'connected')
        return _status(self.journal.status(scope, flow_id))

    async def complete_link(self, flow_id, public_token=None, institution_name=None):
        async with self._serial:
            result = await self._operation(self._complete, flow_id, public_token, institution_name)
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
        if self.connections is not None and status.stage == 'complete':
            request = self._request(scope, status)
            if request['action'] == 'disconnect':
                self.connections.update(self.scope.provider_id, request['item_id'], status='disconnected')
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
        links = item.get('syncSettings')
        owned = self.bank_store.item_identity(item_id)
        destinations = [row for row in self.tracked.accounts('personal')
                        if row['id'] in {link['accountId'] for link in links.values()}] if links is not None else [
                            row for row in self.tracked.accounts('personal') if row.get('itemId') == owned]
        if destinations and all(row.get('closed', False) for row in destinations):
            return {'added': 0, 'modified': 0, 'removed': 0, 'skippedClosed': len(destinations)}
        if self.requires_account_setup and item.get('syncSettings') is None and item.get('lastSynced') is None:
            raise BankError('Choose destination accounts and an import start date in Sync setup first.')
        try:
            response = self._provider_call('read_sync', item_id=item_id, access_token=self._access(item_id),
                cursor=item['cursor'], institution_name=item['institutionName'])
        except ProviderFailure as error:
            if self.connections is not None:
                self.connections.update(self.scope.provider_id, item_id,
                    status='reconnect' if error.code == 'reconnect' else 'error', error=str(error))
            self._command(item_id, 'set_item_status', {'itemId': self.bank_store.item_identity(item_id),
                'status': 'reconnect' if error.code == 'reconnect' else 'error', 'error': str(error)})
            raise BankError(str(error)) from None
        if self.connections is not None:
            self.connections.update(self.scope.provider_id, item_id, status='connected', accounts=response['accounts'])
        # Native uncertain failures propagate without another status mutation.
        with self.tracked.lock:
            payload = self.bank_store.prepare_sync(item_id, response['changes'], response['cursor'], response['accounts'],
                response['historical_complete'], expected_cursor=item['cursor'])
            return read_storage_json(self._command(item_id, 'apply_sync', payload).result)

    async def sync(self, item_id=None):
        async with self._serial:
            if item_id is not None:
                return await self._operation(self._sync, item_id)
            items = await _blocking(self.bank_store.items)
            results = [await self._operation(self._sync, item['id']) for item in items
                       if not (self.requires_account_setup and not item.get('setupConfigured')
                               and item.get('lastSynced') is None)]
            return {name: sum(result[name] for result in results) for name in ('added', 'modified', 'removed')}

    def _account_metadata(self, item_id):
        self._ready()
        item = self._item(item_id)
        if self.supports_account_metadata:
            return self._provider_call('read_accounts', item_id=item_id, access_token=self._access(item_id),
                                       institution_name=item['institutionName'])
        return self._provider_call('read_sync', item_id=item_id, access_token=self._access(item_id),
                                   cursor=item['cursor'], institution_name=item['institutionName'])['accounts']

    def _sync_preview(self, item_id):
        provider_accounts = self._account_metadata(item_id)
        item = self._item(item_id)
        accounts = self.tracked.accounts('personal')
        read_latest = getattr(self.tracked, 'account_latest_dates', None)
        if callable(read_latest):
            latest = read_latest('personal')
        else:
            latest = {}
            for transaction in self.tracked.transactions('personal'):
                if not transaction.get('deleted'):
                    key = transaction['accountId']
                    latest[key] = max(latest.get(key, ''), transaction['date'])
        saved = item.get('syncSettings')
        return provider_accounts, {
            'itemId': item_id, 'institution': item['institutionName'], 'configured': saved is not None,
            'accounts': [{key: account[key] for key in ('id', 'name', 'mask', 'currency') if key in account}
                | {'link': (saved or {}).get(self.bank_store._record_identity('account', item_id, account['id']))}
                for account in provider_accounts],
            'destinations': [{key: account[key] for key in ('id', 'name', 'currency', 'itemId') if key in account}
                | {'latestDate': latest.get(account['id']), 'type': account['type'],
                   'closed': account.get('closed', False), 'available': not account.get('closed', False) and account['type'] not in {'other', 'unclassified'} and account.get('itemId') in (None, self.bank_store.item_identity(item_id))} for account in accounts],
        }

    async def sync_preview(self, item_id):
        async with self._serial:
            return (await self._operation(self._sync_preview, item_id))[1]

    def _investments(self, item_id, account_id, start_date, end_date):
        from datetime import date

        from whab.domain.investments import investment_report
        from whab.domain.validation import validate_date

        self._ready()
        self._item(item_id)
        if not self.supports_investments:
            raise BankError('This bank provider does not support investment reports.')
        end_date = end_date or date.today().isoformat()
        validate_date(end_date)
        end = date.fromisoformat(end_date)
        if start_date is None:
            if end.year < 3:
                raise BankError('Choose an explicit investment history start date.')
            try:
                start_date = end.replace(year=end.year - 2).isoformat()
            except ValueError:
                start_date = end.replace(year=end.year - 2, day=28).isoformat()
        validate_date(start_date)
        if start_date > end_date:
            raise BankError('Investment history start must not follow its end.')
        if account_id is not None:
            self.bank_store._raw_id(account_id)
        snapshot = self._provider_call('read_investments', item_id=item_id, access_token=self._access(item_id),
                                      start_date=start_date, end_date=end_date, account_id=account_id)
        if snapshot['startDate'] != start_date or snapshot['endDate'] != end_date:
            raise BankError('The investment provider returned a different history window.')
        if account_id is not None and any(row['id'] != account_id for row in snapshot['accounts']):
            raise BankError('The investment provider returned a different account.')
        report = investment_report(snapshot)
        report.update(itemId=item_id, provider=self.scope.provider_id)
        return report

    async def investments(self, item_id, account_id=None, start_date=None, end_date=None):
        async with self._serial:
            return await self._operation(self._investments, item_id, account_id, start_date, end_date)

    def _configure_sync(self, item_id, selection):
        accounts = self._account_metadata(item_id)
        payload = self.bank_store.prepare_sync(item_id, {'added': [], 'modified': [], 'removed': []}, None, accounts)
        scoped = {}
        if type(selection) is not list or len(selection) > 500:
            raise BankError('Choose the bank accounts to import.')
        for row in selection:
            if type(row) is not dict or row.keys() != {'bankAccountId', 'accountId', 'since'}:
                raise BankError('Invalid bank account selection.')
            source = self.bank_store._record_identity('account', item_id, row['bankAccountId'])
            if source in scoped:
                raise BankError('Select each bank account once.')
            scoped[source] = {'accountId': source if row['accountId'] is None else row['accountId'], 'since': row['since']}
        payload = {'itemId': payload['itemId'], 'accounts': payload['accounts'], 'selection': scoped}
        return read_storage_json(self._command(item_id, 'configure_bank_sync', payload).result)

    async def configure_sync(self, item_id, selection):
        async with self._serial:
            return await self._operation(self._configure_sync, item_id, selection)

    async def shared_connections(self):
        if self.connections is None:
            raise BankError('Shared bank connections are unavailable for this provider.')
        return {'connections': await _blocking(self.connections.public, self.scope.provider_id, self.scope.budget_id)}

    async def list_profiles(self):
        if self.profiles is None:
            raise BankError('Named account profiles are unavailable for this provider.')
        return {'profiles': await _blocking(self.profiles.public)}

    async def save_profile(self, identifier, config):
        if self.profiles is None:
            raise BankError('Named account profiles are unavailable for this provider.')
        async with self._serial:
            result = await self._operation(self.profiles.save, identifier, config)
            self.config.update(configured=True, missingConfig=[])
            return result

    def _attach(self, item_id):
        self.broker.admit()
        if self.connections is None:
            raise BankError('Shared bank connections are unavailable for this provider.')
        record = self.connections.get(self.scope.provider_id, item_id)
        if record['status'] in {'revoking', 'disconnected'}:
            raise BankError('This connection cannot be attached while disconnected or revoking.')
        if record['attachments'].get(self.scope.budget_id) == 'attached':
            return {'attached': True, 'itemId': item_id}
        self.connections.attachment(self.scope.provider_id, item_id, self.scope.budget_id, 'pending')
        # A retained detached attachment keeps its mappings, cursor and history.
        if self.bank_store.get_item(item_id) is None:
            self._command(item_id, 'save_item', {'itemId': self.bank_store.item_identity(item_id),
                'encryptedToken': self.connections.reference(self.scope.provider_id, item_id),
                'institutionName': record['institutionName'], 'providerId': self.scope.provider_id})
        self.connections.attachment(self.scope.provider_id, item_id, self.scope.budget_id, 'attached')
        return {'attached': True, 'itemId': item_id}

    async def attach(self, item_id):
        async with self._serial:
            return await self._operation(self._attach, item_id)

    async def detach(self, item_id):
        async with self._serial:
            if self.connections is None:
                raise BankError('Shared bank connections are unavailable for this provider.')
            self.journal.prepare_close()
            await self._operation(self.connections.attachment, self.scope.provider_id, item_id, self.scope.budget_id, 'detached')
            return {'attached': False, 'itemId': item_id}

    def _disconnect(self, item_id, affected_budgets=None):
        if self.connections is not None:
            record = self.connections.get(self.scope.provider_id, item_id)
            affected = sorted(key for key, state in record['attachments'].items() if state in {'attached', 'pending'})
            if (len(affected) > 1 or affected_budgets is not None) and affected_budgets != affected:
                raise BankError('Review every affected budget and confirm their exact sorted budget IDs before disconnecting this bank.')
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
                if self.connections is not None:
                    self.connections.update(self.scope.provider_id, item_id, status='disconnected')
                return
            self._ready()
            self._access(item_id)  # Refuse a locked Item before creating any new intent.
            selected = self.journal.begin(self.scope, 'update', secret=canonical_storage_json({
                'action': 'disconnect', 'item_id': item_id, 'code_identity': self.code_identity, 'link_token': ''}))
        if self.connections is not None:
            self.connections.update(self.scope.provider_id, item_id, status='revoking')
        self._complete(selected.identifier, None, None)

    async def disconnect(self, item_id, affected_budgets=None):
        async with self._serial:
            return await self._operation(self._disconnect, item_id, affected_budgets)

    async def webhook(self, raw, headers, profile_id='default'):
        from whab.plugins import WebhookResult
        async with self._serial:
            result = await _blocking(self._provider_call, 'webhook_event', profile_id=profile_id, raw=raw, headers=dict(headers))
            if self.connections is not None and result.item_id is not None:
                from dataclasses import replace
                result = replace(result, item_id=self.connections.connection_id(self.scope.provider_id, profile_id, result.item_id))
            if self.connections is not None and result.item_id is not None:
                known = any(row['itemId'] == result.item_id for row in self.connections.public(self.scope.provider_id, self.scope.budget_id))
                if known and self.shared_event is not None:
                    await self.shared_event(self.scope.provider_id, result.item_id, result.sync, result.reconnect)
            if result.item_id is not None and self.bank_store.get_item(result.item_id) is not None:
                if result.reconnect:
                    if self.connections is not None:
                        await _blocking(self.connections.update, self.scope.provider_id, result.item_id,
                            status='reconnect', error='The bank connection requires a scoped update through its provider.')
                    await _blocking(self._command, result.item_id, 'set_item_status', {
                        'itemId': self.bank_store.item_identity(result.item_id), 'status': 'reconnect',
                        'error': 'The bank connection requires a scoped update through its provider.'})
                if result.sync:
                    self.queue_sync(result.item_id)
            return WebhookResult(result.ok, result.status_code)

    async def notify_sync(self, item_id):
        self.queue_sync(item_id)

    def queue_sync(self, item_id):
        if not self.automatic_sync():
            return
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
                    if not self.automatic_sync():
                        break
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
                          'link_token', 'exchange', 'sync', 'sync_preview', 'configure_sync', 'investments',
                          'shared_connections', 'list_profiles', 'save_profile', 'attach', 'detach', 'notify_sync', 'disconnect', 'webhook', 'close'})

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
