"""Prove genuine pre-void readers reject newly saved tombstone budgets.

Run from whab/app with the development Python. Full local Git history must contain
the pinned WHAB 2.81.0 commit. Uses only temporary fictional budgets, without
network access, installation, credentials or production runtime data.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PREVIOUS_COMMIT = '416b7858c637be6be902a5a0b9b9d20ccbef8f50'
OLD_READER = '''
import importlib, json, pathlib, sys
import whab
from whab.budgets.session import BudgetSession
from whab.plugins import StoreError
root, backend, filename, destination, rejected = sys.argv[1:]
module = importlib.import_module('whab_plugin_' + backend)
for item in (whab, module):
    assert pathlib.Path(item.__file__).resolve().is_relative_to(pathlib.Path(root).resolve())
assert whab.__version__ == '2.81.0'
artifact = pathlib.Path(filename)
original = artifact.read_bytes()
session = BudgetSession.open(destination, 'sandbox', artifact)
store = module.Store(session.storage_home / ('fictional.' + backend), month='2026-11')
try:
    before, evidence = store.export_budget('personal'), store.commit_evidence()
    native = store.db.serialize() if backend == 'sqlite' else store.path.read_bytes()
    try:
        session.bind(store)
    except StoreError as error:
        assert rejected == 'true', str(error)
        assert store.export_budget('personal') == before
        assert store.commit_evidence() == evidence
        assert (store.db.serialize() if backend == 'sqlite' else store.path.read_bytes()) == native
        assert artifact.read_bytes() == original
        print(json.dumps({'backend': backend, 'rejected': True,
                          'destinationUnchanged': True, 'artifactUnchanged': True, 'error': str(error)}))
    else:
        assert rejected == 'false', 'Old reader accepted explicit void semantics'
        assert len(store.transactions('personal')) == 2
        print(json.dumps({'backend': backend, 'rejected': False, 'legacyOpened': True}))
finally:
    store.close()
'''


def fixture():
    from whab.domain.accounts import normalize_account
    from whab.domain.ledger_state import initial_state

    state = initial_state('personal', '2026-10')
    state['accounts'] = {name: normalize_account({
        'id': name, 'name': 'Fictional ' + name, 'type': 'depository', 'balance': 100000,
        'currency': 'USD', 'source': 'manual', 'institution': '', 'stale': False,
        'balanceUnknown': False,
    }) for name in ('cash', 'savings')}
    state['transactions'] = {}
    for name, account, amount, date in [('out', 'cash', 10000, '2026-10-31'), ('in', 'savings', -10000, '2026-11-02')]:
        state['transactions'][name] = {
            'id': name, 'accountId': account, 'date': date, 'merchant': 'Fictional transfer',
            'merchantLogo': None, 'amount': amount, 'categoryId': None, 'pending': False,
            'reviewed': False, 'source': 'manual', 'note': 'Preserved fictional memo',
            'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': True, 'deleted': False,
        }
    return state


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    from whab_plugin_json import Store as JSONStore
    from whab_plugin_sqlite import Store as SQLiteStore
    from whab_plugin_sqlite.portable_journal import read as read_journal

    from whab.budgets.container import decode_container, encode_container
    from whab.budgets.session import BudgetSession, TrackedStorage
    from whab.domain.ledger_state import portable_snapshot
    from whab.plugins import LedgerService

    spec = importlib.util.spec_from_file_location('whab_compatibility', ROOT / 'scripts/check_plugin_devkit.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('WHAB_', 'PLAID_', 'CLIENT_', 'SERVER_', 'TOKEN_ENCRYPTION_', 'ALLOW_LIVE_'))}
    with tempfile.TemporaryDirectory(prefix='whab-void-portable-compatibility-') as temporary:
        base = Path(temporary)
        old = helper.source_at_revision(base, environment, PREVIOUS_COMMIT)
        versions = {'whab': helper.host_version(old)}
        for backend in ('sqlite', 'json'):
            versions[backend] = tomllib.loads((old / f'plugins/storage/{backend}/pyproject.toml').read_text())['project']['version']
        assert versions == {'whab': '2.81.0', 'sqlite': '1.29.0', 'json': '1.21.0'}, versions
        environment['PYTHONPATH'] = os.pathsep.join(str(path) for path in (
            old / 'src', old / 'plugins/storage/sqlite/src', old / 'plugins/storage/json/src'))
        evidence = []
        for backend, store_class in [('sqlite', SQLiteStore), ('json', JSONStore)]:
            session = BudgetSession.create(base / backend / 'current', 'sandbox', 'Fictional void compatibility',
                                           storage=backend, path=base / f'{backend}.whab')
            store = store_class(session.storage_home / ('fictional.' + backend), month='2026-11')
            try:
                store.restore_budget('personal', portable_snapshot(fixture()))
                session.bind(store)
                ledger = LedgerService(TrackedStorage(store, session), budget_id=session.id)
                saved = session.path.read_bytes()
                artifacts = [('snapshot', saved, backend == 'sqlite')]
                if backend == 'sqlite':
                    # Envelope 4 fences readers without incremental replay. Keep
                    # the genuine pre-void raw SQLite acceptance check separate.
                    container = decode_container(saved)
                    base_image, entries = read_journal(container.files['plugins/sqlite/budget.sqlite'])
                    assert not entries
                    legacy_files = {**container.files, 'plugins/sqlite/budget.sqlite': base_image}
                    artifacts.append(('legacy-raw', encode_container(container.manifest, legacy_files), False))
                ids = ['out', 'in']
                preview = ledger.query('preview_void_transactions', 'personal', parameters={'transactionIds': ids})
                ledger.command('void_transactions', 'personal', {'transactionIds': ids, 'fingerprint': preview['data']['fingerprint']},
                               request_id='fictional-void', expected_revision=preview['revision'])
                artifacts.append(('voided', session.path.read_bytes(), True))
                entry = next(row for row in store.budget_history('personal')['entries'] if row['operation'] == 'void_transactions')
                preview = ledger.query('history_preview', 'personal', parameters={'action': 'undo', 'target': entry['id']})
                ledger.command('history_apply', 'personal', {'action': 'undo', 'target': entry['id'], 'fingerprint': preview['data']['fingerprint']},
                               request_id='fictional-undo', expected_revision=preview['revision'])
                assert not any(row.get('voided') for row in store.transactions('personal'))
                artifacts.append(('history-only', session.path.read_bytes(), True))
            finally:
                store.close()
            for case, raw, rejected in artifacts:
                artifact = base / f'{backend}-{case}.whab'
                artifact.write_bytes(raw)
                result = subprocess.run([sys.executable, '-c', OLD_READER, str(old), backend, str(artifact),
                                         str(base / backend / case), str(rejected).lower()],
                                        cwd=base, env=environment, capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise RuntimeError(f'{backend}/{case}: ' + (result.stderr or result.stdout))
                evidence.append({'case': case, **json.loads(result.stdout)})
        print(json.dumps({'commit': PREVIOUS_COMMIT, 'versions': versions, 'checks': evidence}, indent=2))


if __name__ == '__main__':
    main()
