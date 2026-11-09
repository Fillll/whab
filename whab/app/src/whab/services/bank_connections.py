"""Device-local connection ownership, independent of budget import progress.

The registry is not a ledger or a provider cache. Budget backends retain their
own atomic transaction/cursor commits. Registry writes use a process lock and
atomic replacement; native attachment writes are recoverable from their intent.
"""

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock

from whab.budgets.document import atomic_write
from whab.domain.imports import bank_identifier
from whab.hosting import process_lock
from whab.plugins import BankError
from whab.plugins.bank_crypto import BankCipher
from whab.plugins.sdk import valid_plugin_name

REFERENCE = 'shared-bank-v1:'


class BankConnections:
    def __init__(self, home, environment, cipher):
        if environment not in {'sandbox', 'production'} or not isinstance(cipher, BankCipher):
            raise BankError('Shared bank connections require a configured local encryption key.')
        self.directory = Path(home) / 'plugin-workers' / 'bank-connections' / environment
        self.path = self.directory / 'connections.json'
        self.environment, self.cipher, self.lock = environment, cipher, RLock()

    def identity(self, provider, item_id):
        if not valid_plugin_name(provider):
            raise BankError('Invalid bank provider.')
        bank_identifier(item_id)
        return hashlib.sha256(json.dumps([self.environment, provider, item_id]).encode()).hexdigest()

    def reference(self, provider, item_id):
        return REFERENCE + self.identity(provider, item_id)

    def _aad(self, provider, item_id):
        return 'whab-shared-bank-v1:' + self.identity(provider, item_id)

    @contextmanager
    def records(self):
        with self.lock, process_lock(self.directory / 'registry.lock'):
            try:
                if self.path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError
                records = json.loads(self.path.read_bytes())
                if type(records) is not dict or len(records) > 1000:
                    raise ValueError
                for key, row in records.items():
                    if (type(row) is not dict or row.keys() != {'provider', 'itemId', 'ownerBudgetId', 'institutionName',
                            'token', 'status', 'error', 'accounts', 'attachments', 'profileId', 'profilePrincipal', 'providerItemId'}
                            or key != self.identity(row['provider'], row['itemId'])
                            or row['status'] not in {'connected', 'reconnect', 'error', 'revoking', 'disconnected'}
                            or type(row['attachments']) is not dict
                            or any(value not in {'pending', 'attached', 'detached'} for value in row['attachments'].values())
                            or type(row['accounts']) is not list):
                        raise ValueError
            except FileNotFoundError:
                records = {}
            except (OSError, ValueError, TypeError, KeyError):
                raise BankError('The shared bank registry is unreadable. Preserve it and repair it before continuing.') from None
            before = deepcopy(records)
            yield records
            if records != before:
                atomic_write(self.path, json.dumps(records, ensure_ascii=False).encode())
                # Replacement and directory entry must survive a power loss.
                from whab.budgets.session import _flush_source_home
                _flush_source_home(self.directory, self.directory)

    def register(self, provider, item, budget_id, accounts=(), *, profile_id='default', provider_item_id=None, profile_principal=None):
        """Register before replacing legacy ciphertext; retries retain ownership."""
        key = self.identity(provider, item['id'])
        with self.records() as records:
            record = records.get(key)
            if item['token'].startswith(REFERENCE) and item['token'] != self.reference(provider, item['id']):
                raise BankError('The saved bank attachment belongs to another connection.')
            if record is None:
                if item['token'].startswith(REFERENCE):
                    raise BankError('This bank attachment is missing its device-local connection. Restore the matching runtime backup.')
                token = self.cipher.decrypt_token(item['token'], item['storedIdentity'])
                record = records[key] = {'provider': provider, 'itemId': item['id'], 'ownerBudgetId': budget_id,
                    'profileId': profile_id, 'profilePrincipal': profile_principal, 'providerItemId': provider_item_id or item['id'],
                    'institutionName': item['institutionName'],
                    'token': self.cipher.encrypt_token(token, self._aad(provider, item['id'])),
                    'status': item['status'], 'error': item.get('error'), 'accounts': [], 'attachments': {}}
            elif not item['token'].startswith(REFERENCE) and record['token'] is not None:
                old = self.cipher.decrypt_token(item['token'], item['storedIdentity'])
                if old != self.cipher.decrypt_token(record['token'], self._aad(provider, item['id'])):
                    raise BankError('Conflicting bank credentials require recovery before this connection can be shared.')
            record['attachments'].setdefault(budget_id, 'attached')
            if profile_principal is not None and record['profileId'] == profile_id:
                if record['profilePrincipal'] not in (None, profile_principal):
                    raise BankError('The bank connection belongs to another provider account. Restore the matching profile.')
                record['profilePrincipal'] = profile_principal
            if record['attachments'][budget_id] == 'pending':
                record['attachments'][budget_id] = 'attached'
            if accounts and not record['accounts']:
                record['accounts'] = self.safe_accounts(accounts)

    @staticmethod
    def safe_accounts(accounts):
        return [{key: row[key] for key in ('id', 'name', 'mask', 'currency', 'type') if key in row} for row in accounts]

    def get(self, provider, item_id):
        with self.records() as records:
            record = records.get(self.identity(provider, item_id))
            if record is None:
                raise BankError('Shared bank connection not found on this device.')
            return deepcopy(record)

    def public(self, provider, budget_id):
        with self.records() as records:
            return [{key: deepcopy(row[key]) for key in ('provider', 'itemId', 'ownerBudgetId',
                        'institutionName', 'status', 'error', 'accounts', 'profileId', 'providerItemId')}
                    | {'attached': row['attachments'].get(budget_id) == 'attached',
                       'attachments': [{'budgetId': key, 'state': value} for key, value in row['attachments'].items()]}
                    for row in records.values() if row['provider'] == provider]

    def connection_id(self, provider, profile_id, item_id):
        if profile_id == 'default':
            return item_id
        return 'shared:' + hashlib.sha256(json.dumps([provider, profile_id, item_id]).encode()).hexdigest()

    def access(self, provider, item_id, budget_id, *, revocation=False):
        record = self.get(provider, item_id)
        if record['attachments'].get(budget_id) != 'attached':
            raise BankError('Attach this connection to the budget before using it.')
        if record['token'] is None or record['status'] in ({'disconnected'} if revocation else {'revoking', 'disconnected'}):
            raise BankError('This bank connection is disconnected or has an unfinished disconnection.')
        return self.cipher.decrypt_token(record['token'], self._aad(provider, item_id))

    def attachment(self, provider, item_id, budget_id, state):
        self.get(provider, item_id)
        with self.records() as records:
            record = records[self.identity(provider, item_id)]
            if state != 'detached' and record['status'] in {'revoking', 'disconnected'}:
                raise BankError('This connection cannot be attached while disconnected or revoking.')
            record['attachments'][budget_id] = state

    def update(self, provider, item_id, *, status=None, error=None, accounts=None):
        self.get(provider, item_id)
        with self.records() as records:
            record = records[self.identity(provider, item_id)]
            if status is not None:
                if status not in {'revoking', 'disconnected'} and record['status'] in {'revoking', 'disconnected'}:
                    return
                record.update(status=status, error=error)
                if status == 'disconnected':
                    record['token'] = None
            if accounts is not None:
                record['accounts'] = self.safe_accounts(accounts)


class AttachedBankStore:
    """Filter public budget connections without changing financial persistence."""

    def __init__(self, store, connections, budget_id):
        self.store, self.connections, self.budget_id = store, connections, budget_id

    def __getattr__(self, name):
        return getattr(self.store, name)

    def items(self):
        result = []
        for item in self.store.items():
            record = self.connections.get(self.store._provider, item['id'])
            if record['attachments'].get(self.budget_id) == 'attached':
                result.append({**item, 'status': record['status'], 'error': record['error'],
                               'ownerBudgetId': record['ownerBudgetId'], 'profileId': record['profileId']})
        return result
