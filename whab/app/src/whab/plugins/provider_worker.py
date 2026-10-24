"""Bounded typed provider transport; native writes and encryption stay in host."""

import asyncio
import base64
import inspect
import re
from dataclasses import asdict

from whab.domain.imports import (
    ImportedAccount,
    ImportedTransaction,
    imported_account,
    validate_imported_transaction,
)

from .bank_adapter import BankAdapter, BankProviderContext, ProviderWebhook
from .effect_store import decode, encode
from .storage_transfer import storage_identifier
from .wire import WireError

MAX_PROVIDER_BYTES = 16 * 1024 * 1024
_COMMANDS = frozenset({'open', 'connection_token', 'exchange_token', 'read_sync', 'remove_token', 'webhook_event', 'close'})


def _text(value, limit=4096, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or not 1 <= len(value) <= limit or '\x00' in value:
        raise WireError()
    value.encode('utf-8')
    return value


def provider_config(value, environment):
    if (type(value) is not dict or value.keys() != {'environment', 'configured', 'liveEnabled', 'missingConfig'}
            or value['environment'] != environment or type(value['configured']) is not bool
            or type(value['liveEnabled']) is not bool or type(value['missingConfig']) is not list
            or len(value['missingConfig']) > 20 or any(type(name) is not str or re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', name) is None
                                                    for name in value['missingConfig'])):
        raise WireError()
    return value


def read_provider_result(command, value, *, provider_id, environment=None, item_id=None):
    if command == 'open':
        return provider_config(value, environment)
    if command == 'connection_token':
        return _text(value)
    if command == 'exchange_token':
        if type(value) is not dict or value.keys() != {'item_id', 'access_token'}:
            raise WireError()
        storage_identifier(value['item_id'])
        _text(value['access_token'])
        return value
    if command == 'read_sync':
        if (type(value) is not dict or value.keys() != {'changes', 'cursor', 'accounts', 'historical_complete'}
                or type(value['historical_complete']) is not bool or type(value['accounts']) is not list
                or len(value['accounts']) > 500 or type(value['changes']) is not dict
                or value['changes'].keys() != {'added', 'modified', 'removed'}):
            raise WireError()
        _text(value['cursor'], nullable=True)
        count = 0
        for name, rows in value['changes'].items():
            if type(rows) is not list:
                raise WireError()
            count += len(rows)
            if count > 100000:
                raise WireError()
            for row in rows:
                if name == 'removed':
                    storage_identifier(row)
                else:
                    if (type(row) is not dict or not ImportedTransaction.__required_keys__ <= row.keys()
                            or not row.keys() <= ImportedTransaction.__required_keys__ | ImportedTransaction.__optional_keys__
                            or row['source'] != provider_id):
                        raise WireError()
                    validate_imported_transaction(row)
        for row in value['accounts']:
            if (type(row) is not dict or not ImportedAccount.__required_keys__ <= row.keys()
                    or not row.keys() <= ImportedAccount.__required_keys__ | ImportedAccount.__optional_keys__
                    or row['itemId'] != item_id or row['source'] != provider_id):
                raise WireError()
            imported_account(row)
        return value
    if command in {'remove_token', 'close'}:
        if value is not None:
            raise WireError()
        return None
    if command == 'webhook_event':
        if type(value) is not dict or value.keys() != {'ok', 'status_code', 'item_id', 'sync', 'reconnect'}:
            raise WireError()
        return ProviderWebhook(**value)
    raise WireError()


class ProviderDispatcher:
    """Child owns only provider client/cache and one persistent async event loop."""

    def __init__(self, plugin):
        if type(plugin.bank) is not BankAdapter:
            raise WireError()
        self.plugin, self.engine, self.context = plugin, None, None
        self.loop = asyncio.new_event_loop()

    def _await(self, value):
        if not inspect.isawaitable(value):
            raise WireError()
        return self.loop.run_until_complete(value)

    def call(self, arguments, payload):
        if (type(arguments) is not dict or arguments.keys() != {'command'}
                or type(arguments['command']) is not str or arguments['command'] not in _COMMANDS):
            raise WireError()
        command = arguments['command']
        value = decode(payload, MAX_PROVIDER_BYTES)
        if type(value) is not dict:
            raise WireError()
        if command == 'open':
            if self.engine is not None or value.keys() != {'budget_id', 'environment', 'config'}:
                raise WireError()
            context = BankProviderContext(**value)
            engine = self.plugin.bank.factory(context)
            if inspect.isawaitable(engine):
                if inspect.iscoroutine(engine):
                    engine.close()
                raise WireError()
            self.engine, self.context = engine, context
            if any(not callable(getattr(engine, name, None)) for name in _COMMANDS - {'open'}):
                raise WireError()
            output = provider_config(dict(engine.config), context.environment)
        elif self.engine is None:
            raise WireError()
        elif command == 'connection_token':
            if value.keys() != {'item_id', 'access_token'} or (value['item_id'] is None) != (value['access_token'] is None):
                raise WireError()
            if value['item_id'] is not None:
                storage_identifier(value['item_id'])
                _text(value['access_token'])
            output = self._await(self.engine.connection_token(**value))
        elif command == 'exchange_token':
            if value.keys() != {'public_token'}:
                raise WireError()
            _text(value['public_token'], 500)
            output = self._await(self.engine.exchange_token(**value))
        elif command == 'read_sync':
            if value.keys() != {'item_id', 'access_token', 'cursor', 'institution_name'}:
                raise WireError()
            storage_identifier(value['item_id'])
            _text(value['access_token'])
            _text(value['cursor'], nullable=True)
            _text(value['institution_name'], 200)
            output = self._await(self.engine.read_sync(**value))
        elif command == 'remove_token':
            if value.keys() != {'access_token'}:
                raise WireError()
            _text(value['access_token'])
            output = self._await(self.engine.remove_token(**value))
        elif command == 'webhook_event':
            if (value.keys() != {'raw', 'headers'} or type(value['headers']) is not dict or len(value['headers']) > 100
                    or any(type(name) is not str or type(item) is not str or len(name) > 200 or len(item) > 8192
                           for name, item in value['headers'].items())):
                raise WireError()
            raw = base64.b64decode(value['raw'], validate=True)
            if len(raw) > 1024 * 1024:
                raise WireError()
            event = self._await(self.engine.webhook_event(raw, value['headers']))
            if type(event) is not ProviderWebhook:
                raise WireError()
            output = asdict(event)
        else:
            if value:
                raise WireError()
            output = self._await(self.engine.close())
            self.engine = None
        read_provider_result(command, output, provider_id=self.plugin.id,
                             environment=self.context.environment, item_id=value.get('item_id'))
        return {'provider': 1}, encode(output, MAX_PROVIDER_BYTES)

    def close(self):
        if self.loop.is_closed():
            return
        try:
            if self.engine is not None:
                engine, self.engine = self.engine, None
                self._await(engine.close())
        finally:
            async def drain():
                tasks = tuple(task for task in asyncio.all_tasks(self.loop) if task is not asyncio.current_task())
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            try:
                self.loop.run_until_complete(drain())
            finally:
                self.loop.close()


class WorkerBankFactory:
    """Host-side factory marker for an already approved immutable provider child."""

    def __init__(self, worker, *, owns_worker=False, code_identity=None):
        self.worker, self.owns_worker = worker, owns_worker
        self.code_identity = code_identity or f"{worker.info['distribution']}/{worker.info['version']}"

    def __call__(self, context):
        from whab.services.bank_runtime import WorkerBankProvider
        return WorkerBankProvider(self.worker, context, owns_worker=self.owns_worker)
