"""Measure fresh CLI processes against an isolated fictional, durable budget.

Run from whab/app with .venv/bin/python scripts/benchmark_cli.py --runs 100.
The JSON report includes subprocess startup, TLS, output and synchronous saves.
No household runtime, credentials or external bank service is opened.
"""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter, sleep
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from whab.plugins.local import local_connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=100)
    parser.add_argument('--transactions', type=int, default=100000)
    args = parser.parse_args()
    if not 1 <= args.runs <= 1000 or not args.runs <= args.transactions <= 100000:
        parser.error('Use 1–1000 runs and runs–100000 transactions.')
    executable = str(Path(sys.executable).parent / 'whab')
    samples = {}
    with TemporaryDirectory(prefix='whab-cli-benchmark-') as directory:
        root = Path(directory)
        home = root / 'runtime'
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'PYTHONPATH') if key in os.environ}
        env.update(HOME=directory, WHAB_HOME=str(home), WHAB_ENV='sandbox', WHAB_UI='',
                   WHAB_PLUGINS='', WHAB_STORAGE='sqlite', ALLOW_LIVE_PLAID='false')

        def cli(*command, label=None, allow_oversized=False):
            started = perf_counter()
            process = subprocess.run([executable, '--home', str(home), '--environment', 'sandbox', *command],
                                     env=env, capture_output=True, timeout=120)
            elapsed = (perf_counter() - started) * 1000
            if process.returncode and allow_oversized and b'16 MiB' in process.stderr:
                samples.setdefault(label, []).append(elapsed)
                return None
            if process.returncode:
                raise RuntimeError(f'Fictional command {command[:3]} failed: {process.stderr.decode()}')
            if label:
                samples.setdefault(label, []).append(elapsed)
            return process.stdout

        def run(*command, label=None):
            return json.loads(cli(*command, label=label))

        archive = root / 'fictional.zip'
        accounts = max(31, args.runs)
        with ZipFile(archive, 'w', ZIP_DEFLATED) as export:
            export.writestr('Budget.csv', 'Month,Category Group/Category,Category Group,Category,Assigned,Activity,Available\n' + ''.join(
                f'Oct 2026,Fixture: Group {i},Fixture,Group {i},$100.00,-$12.34,$87.66\n' for i in range(57)))
            rows = ['Account,Flag,Date,Payee,Category Group/Category,Category Group,Category,Memo,Outflow,Inflow,Cleared\n']
            for i in range(min(args.transactions, 100)):
                category = f'Fixture: Group {i % 57},Fixture,Group {i % 57}' if i % 9 else ',,'
                rows.append(f'Account {i % accounts},,202{4 + (i // 336) % 3}-{1 + (i // 28) % 12:02}-{1 + i % 28:02},Fixture payee {i % 1103},{category},Synthetic memo {i},$12.34,$0.00,Cleared\n')
            export.writestr('Register.csv', ''.join(rows))
        target = 'Fictional performance'
        # Seed the fictional portable file through the owning storage/container
        # APIs. A 100k-row import DTO can exceed provider-worker response limits.
        from whab_plugin_sqlite import Store

        from whab.budgets.container import encode_container
        from whab.budgets.document import atomic_write, new_manifest
        from whab.domain.accounts import normalize_account
        from whab.domain.history import financial
        from whab.domain.portable import ACCOUNT_FIELDS
        manifest = new_manifest(target, storage='sqlite')
        fixture_path = root / 'fictional-performance.whab'
        with patch.dict(os.environ, env, clear=True):
            from whab.initialization import initialize_home
            initialize_home(home)
            store = Store(':memory:', month='2026-10', _authority=False)
            try:
                snapshot = store.export_budget('personal')
                snapshot['accounts'] = []
                for i in range(accounts):
                    row = normalize_account({'id': f'account-{i}', 'name': f'Account {i}', 'type': 'other',
                        'onBudget': False, 'balance': 100000, 'currency': 'USD', 'institution': 'Fictional',
                        'source': 'manual', 'stale': False, 'balanceUnknown': False})
                    snapshot['accounts'].append({key: row[key] for key in ACCOUNT_FIELDS})
                snapshot['transactions'] = [{
                    'id': f'tx-{i:06}', 'accountId': f'account-{i % accounts}',
                    'date': f'202{4 + (i // 336) % 3}-{1 + (i // 28) % 12:02}-{1 + i % 28:02}',
                    'merchant': f'Fixture payee {i % 1103}', 'merchantLogo': None, 'amount': 100,
                    'categoryId': None, 'pending': False, 'reviewed': False, 'source': 'manual',
                    'note': '', 'isTransfer': False, 'inferredTransfer': False,
                    'categoryEdited': False, 'deleted': False,
                } for i in range(args.transactions)]
                snapshot['categories'] = [{'id': f'category-{i}', 'name': f'Group {i}', 'group': 'Fixture',
                                           'target': 0, 'currency': 'USD'} for i in range(57)]
                snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
                store.restore_budget('personal', snapshot)
                files = {'plugins/sqlite/' + key: raw for key, raw in store.export_budget_files().items()}
                atomic_write(fixture_path, encode_container(manifest, files))
            finally:
                store.close()
            atomic_write(home / 'budgets/index-sandbox.json', json.dumps([
                {'id': manifest['id'], 'name': target, 'path': str(fixture_path)}]).encode())
            run('budget', 'list', label='cold host')
            with local_connection(home, 'sandbox'):
                created = {'path': str(fixture_path)}
                import_target = 'Fictional import'
                run('budget', 'create', import_target)
                run(import_target, 'plugin', 'enable', 'ynab-zip-importer', label='plugin enable')
                preview = run(import_target, 'import', 'ynab', 'preview', str(archive), '--currency', 'USD', label='small import preview')
                assert preview['canImport'] and preview['transactions'] == min(args.transactions, 100)
                run(import_target, 'import', 'ynab', 'commit', str(archive), '--currency', 'USD', '--fingerprint', preview['fingerprint'], label='small import commit')
                # The full output limit is measured independently from setup and verification.
                cli(target, 'trnx', 'list', label='full history output or limit refusal', allow_oversized=True)
                def read_all():
                    rows = []
                    for year in range(2024, 2027):
                        for month in range(1, 13):
                            result = run(target, 'trnx', 'list', '--month', f'{year}-{month:02}')
                            assert result['complete'] and result['returned'] == result['total']
                            rows.extend(result['data'])
                    assert len(rows) == len({row['id'] for row in rows}) == args.transactions
                    return sorted(rows, key=lambda row: (row['date'], row['id']), reverse=True)
                transactions = read_all()
                for i in range(args.runs):
                    cli(target, 'trnx', 'list', '--help', label='help')
                    run(target, 'trnx', 'list', '--month', '2026-05', label='monthly list')
                    selected = run(target, 'trnx', 'list', '--merchant', 'Fixture payee 1001', label='merchant search')
                    assert selected['complete']
                    run(target, 'trnx', 'uncategorized', '--limit', '10', label='uncategorized sample')
                    transaction = transactions[i]
                    change = run(target, 'trnx', 'categorize', transaction['id'], '--category', 'Fixture / Group 56', '--agent', 'Benchmark', label='categorize')
                    run(target, 'account', 'classify', f'Account {i}', '--type', 'depository', '--on-budget', label='classify')
                    run(target, 'account', 'show', f'Account {i}', label='account show')
                    run(target, 'category', 'list', label='category list')
                    if i == 0:
                        retry = run(target, 'trnx', 'categorize', transaction['id'], '--category', 'Fixture / Group 56', '--agent', 'Benchmark',
                                    '--revision', change['expectedRevision'], '--request-id', change['requestId'])
                        assert retry == change
                saved = read_all()
                by_id = {row['id']: row for row in saved}
                assert all(by_id[row['id']]['categoryAgent'] == 'Benchmark' for row in transactions[:args.runs])
                history = run(target, 'history', 'list')['data']
                assert history['total'] >= 2 * args.runs
                budget_path = Path(created['path'])
            endpoint = home / 'run/sandbox/host.json'
            deadline = monotonic() + 20
            while endpoint.exists() and monotonic() < deadline:
                sleep(.1)
            assert not endpoint.exists(), 'Fictional host must stop before reopen'
            reopened = read_all()
            assert reopened == saved and budget_path.is_file()
            deadline = monotonic() + 20
            while endpoint.exists() and monotonic() < deadline:
                sleep(.1)
            assert not endpoint.exists()
            saved_file_bytes = budget_path.stat().st_size
    report = {name: {'n': len(times), 'median_ms': round(median(times), 2),
                     'p95_ms': round(sorted(times)[math.ceil(.95 * len(times)) - 1], 2),
                     'total_ms': round(sum(times), 2)} for name, times in samples.items()}
    print(json.dumps({'transactions': args.transactions, 'accounts': accounts, 'runs': args.runs,
                      'saved_result_verified': True, 'saved_container_bytes': saved_file_bytes, 'commands': report}, indent=2))


if __name__ == '__main__':
    main()
