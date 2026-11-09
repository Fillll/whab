"""Encrypted provider account profiles; no credentials enter public results."""

import base64
import hashlib
import json
from contextlib import contextmanager

from whab.budgets.document import atomic_write
from whab.hosting import process_lock
from whab.plugins import BankError
from whab.plugins.bank_adapter import BankProviderContext
from whab.plugins.sdk import valid_plugin_name


class BankProfiles:
    def __init__(self, connections, provider, budget_id, identity_keys, default_config):
        self.connections, self.provider, self.budget_id = connections, provider, budget_id
        self.identity_keys = identity_keys
        self.path = connections.directory / (provider + '-profiles.json')
        if all(default_config.get(key) for key in identity_keys):
            with self.records() as records:
                current = records.get('default')
                identity = self.principal(default_config)
                if current is None or current['principal'] == identity:
                    self._save(records, 'default', default_config)

    @contextmanager
    def records(self):
        with process_lock(self.path.with_suffix('.lock')):
            try:
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError
                records = json.loads(self.path.read_bytes())
                if type(records) is not dict or len(records) > 100:
                    raise ValueError
                for identifier, row in records.items():
                    if (not valid_plugin_name(identifier) or type(row) is not dict
                            or row.keys() != {'principal', 'sealed'} or type(row['principal']) is not str
                            or len(row['principal']) != 64 or type(row['sealed']) is not str):
                        raise ValueError
            except FileNotFoundError:
                records = {}
            except (OSError, ValueError, TypeError, KeyError):
                raise BankError('Saved bank account profiles are unreadable. Preserve the runtime before repairing them.') from None
            before = json.dumps(records, sort_keys=True)
            yield records
            if json.dumps(records, sort_keys=True) != before:
                atomic_write(self.path, json.dumps(records).encode())

    def principal(self, config):
        if not all(type(config.get(key)) is str and config[key] for key in self.identity_keys):
            raise BankError('Supply the provider account identity fields for this profile.')
        return hashlib.sha256(json.dumps([self.provider, self.connections.environment,
            [(key, config[key]) for key in self.identity_keys]], sort_keys=True).encode()).hexdigest()

    def _aad(self, identifier):
        return json.dumps(['bank-profile-v1', self.provider, self.connections.environment, identifier]).encode()

    def _save(self, records, identifier, config):
        BankProviderContext(self.budget_id, self.connections.environment, config)
        principal = self.principal(config)
        previous = records.get(identifier)
        if previous is not None and previous['principal'] != principal:
            raise BankError('This profile belongs to another provider account. Choose a new profile name.')
        duplicate = next((key for key, value in records.items() if key != identifier and value['principal'] == principal), None)
        if duplicate is not None:
            raise BankError('This provider account already has a profile. Use its existing profile name.')
        raw = json.dumps(config).encode()
        if previous is not None:
            try:
                old = self.connections.cipher.open(base64.b64decode(previous['sealed'], validate=True), self._aad(identifier))
            except Exception:
                raise BankError('The bank account profile could not be unlocked. Restore its matching runtime and encryption key.') from None
            if json.loads(old) == config:
                return
        records[identifier] = {'principal': principal,
            'sealed': base64.b64encode(self.connections.cipher.seal(raw, self._aad(identifier))).decode()}

    def save(self, identifier, config):
        if not valid_plugin_name(identifier) or identifier == 'default':
            raise BankError('Choose a named profile other than default. The default profile comes from local configuration.')
        if type(config) is not dict:
            raise BankError('Supply a JSON object of provider credentials.')
        with self.records() as records:
            self._save(records, identifier, config)
        return {'profileId': identifier, 'saved': True}

    def config(self, identifier):
        with self.records() as records:
            record = records.get(identifier)
            if record is None:
                raise BankError('Bank account profile not found. Add it or choose an existing profile.')
            try:
                config = json.loads(self.connections.cipher.open(base64.b64decode(record['sealed'], validate=True), self._aad(identifier)))
                BankProviderContext(self.budget_id, self.connections.environment, config)
                if self.principal(config) != record['principal']:
                    raise ValueError
            except Exception:
                raise BankError('The bank account profile could not be unlocked. Restore its matching runtime and encryption key.') from None
            return config

    def public(self):
        with self.records() as records:
            return [{'profileId': identifier} for identifier in records]
