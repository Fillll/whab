"""Interleave fixed-output CLI workloads across fictional budget sizes.

Run from whab/app with .venv/bin/python scripts/benchmark_cli_matrix.py.
Each size returns the same 300 monthly rows, 13 merchant matches and ten
uncategorized samples. Full-history output and provider calls are excluded.
Raw wall times, client CPU times and system load accompany every observation.
"""

import argparse
import gc
import json
import math
import os
import platform
import random
import resource
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter, sleep
from unittest.mock import patch

from benchmark_cli import verify_portable_file

from whab.budgets.container import encode_container
from whab.budgets.document import atomic_write, new_manifest
from whab.domain.accounts import normalize_account
from whab.domain.history import financial
from whab.domain.portable import ACCOUNT_FIELDS
from whab.initialization import initialize_home
from whab.plugins.local import local_connection


def fixture(root, total, accounts):
    from whab_plugin_sqlite import Store
    name = f'Fixture {total:06}'
    manifest = new_manifest(name, storage='sqlite')
    path = root / f'fictional-{total}.whab'
    store = Store(':memory:', month='2026-10', _authority=False)
    try:
        snapshot = store.export_budget('personal')
        snapshot['accounts'] = []
        for i in range(accounts):
            row = normalize_account({'id': f'account-{i}', 'name': f'Account {i}', 'type': 'other',
                                     'onBudget': False, 'balance': 100000, 'currency': 'USD',
                                     'source': 'manual', 'institution': 'Fictional', 'stale': False, 'balanceUnknown': False})
            snapshot['accounts'].append({key: row[key] for key in ACCOUNT_FIELDS})
        snapshot['categories'] = [{'id': f'category-{i}', 'name': f'Group {i}', 'group': 'Fixture', 'target': 0, 'currency': 'USD'} for i in range(57)]
        snapshot['transactions'] = [{
            'id': f'tx-{i:06}', 'accountId': f'account-{i % accounts}',
            'date': f'2026-05-{1 + i % 28:02}' if i < 300 else '2025-01-01',
            'merchant': 'Selective fictional merchant' if i < 13 else f'Other fictional merchant {i}',
            'merchantLogo': None, 'amount': 100, 'categoryId': None, 'pending': False,
            'reviewed': False, 'source': 'manual', 'note': '', 'isTransfer': False,
            'inferredTransfer': False, 'categoryEdited': False, 'deleted': False,
        } for i in range(total)]
        snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
        store.restore_budget('personal', snapshot)
        files = {'plugins/sqlite/' + key: raw for key, raw in store.export_budget_files().items()}
        atomic_write(path, encode_container(manifest, files))
    finally:
        store.close()
    return {'id': manifest['id'], 'name': name, 'path': str(path), 'transactions': total}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=100)
    parser.add_argument('--sizes', default='1000,14550,100000')
    args = parser.parse_args()
    sizes = [int(text) for text in args.sizes.split(',')]
    if not 1 <= args.runs <= 1000 or len(sizes) != len(set(sizes)) or not sizes or min(sizes) < max(300, args.runs) or max(sizes) > 250000:
        parser.error('Use unique sizes between max(300,runs) and 250000, and 1–1000 runs; portable size bounds still apply.')
    samples, cold = {}, {}
    executable = str(Path(sys.executable).parent / 'whab')
    with TemporaryDirectory(prefix='whab-cli-matrix-') as directory:
        root = Path(directory)
        home = root / 'runtime'
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
        env.update(HOME=directory, WHAB_HOME=str(home), WHAB_ENV='sandbox', WHAB_UI='',
                   WHAB_PLUGINS='', WHAB_STORAGE='sqlite', ALLOW_LIVE_PLAID='false')

        def cli(*command, sample=None, expected_error=None, plain=False):
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            load = os.getloadavg()
            started = perf_counter()
            result = subprocess.run([executable, '--home', str(home), '--environment', 'sandbox', *command],
                                    env=env, capture_output=True, timeout=120)
            elapsed = (perf_counter() - started) * 1000
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            if expected_error is not None:
                assert result.returncode and expected_error in result.stderr, result.stderr.decode()
                return None
            assert result.returncode == 0, result.stderr.decode()
            if sample:
                samples.setdefault(sample, []).append({'wall_ms': elapsed,
                    'client_user_ms': (after.ru_utime - usage.ru_utime) * 1000,
                    'client_system_ms': (after.ru_stime - usage.ru_stime) * 1000,
                    'load_average': load, 'output_bytes': len(result.stdout)})
            return result.stdout if plain else json.loads(result.stdout)

        def history(target):
            entries, cursor = [], None
            while True:
                page = cli(target, 'history', 'list', '--limit', '100', *(['--before', cursor] if cursor else []))['data']
                entries.extend(page['entries'])
                if page['next'] is None:
                    assert len(entries) == page['total']
                    return entries
                cursor = page['next']

        with patch.dict(os.environ, env, clear=True):
            initialize_home(home)
            budgets = [fixture(root, size, max(100, args.runs)) for size in sizes]
            atomic_write(home / 'budgets/index-sandbox.json', json.dumps([
                {key: value for key, value in budget.items() if key != 'transactions'} for budget in budgets]).encode())
            gc.collect()
            cli('Missing fictional budget', 'trnx', 'list', '--help', plain=True)
            for _ in range(args.runs):
                cli('Missing fictional budget', 'trnx', 'list', '--help', plain=True, sample='help_before')
            with local_connection(home, 'sandbox'):
                for budget in budgets:
                    target = budget['name']
                    start = perf_counter()
                    cli(target, 'category', 'list')
                    cold[target] = {'activation_ms': (perf_counter() - start) * 1000}
                    start = perf_counter()
                    assert cli(target, 'trnx', 'list', '--merchant', 'Selective fictional merchant')['returned'] == 13
                    cold[target]['merchant_index_ms'] = (perf_counter() - start) * 1000
                rng = random.Random(20261110)
                for i in range(args.runs):
                    cli('Missing fictional budget', 'trnx', 'list', '--help', plain=True, sample='help_during')
                    order = list(budgets)
                    rng.shuffle(order)
                    for budget in order:
                        target = budget['name']
                        def run(*command, label):
                            return cli(target, *command, sample=f'{budget["transactions"]}:{label}')
                        monthly = run('trnx', 'list', '--month', '2026-05', label='monthly list')
                        assert monthly['complete'] and monthly['returned'] == monthly['total'] == 300
                        merchants = run('trnx', 'list', '--merchant', 'Selective fictional merchant', label='merchant search')
                        assert merchants['complete'] and merchants['returned'] == merchants['total'] == 13
                        uncategorized = run('trnx', 'uncategorized', '--limit', '10', label='uncategorized sample')
                        assert uncategorized['total'] == budget['transactions'] - i and uncategorized['returned'] == 10
                        change = run('trnx', 'categorize', f'tx-{i:06}', '--category', 'Fixture / Group 56', '--agent', 'Benchmark', label='categorize')
                        run('account', 'classify', f'Account {i}', '--type', 'depository', '--on-budget', label='classify')
                        if i == 0:
                            budget['intent'] = change
                            retry = cli(target, 'trnx', 'categorize', 'tx-000000', '--category', 'Fixture / Group 56', '--agent', 'Benchmark',
                                        '--revision', change['expectedRevision'], '--request-id', change['requestId'])
                            assert retry == change
                            cli(target, 'trnx', 'categorize', 'tx-000000', '--clear', '--revision', change['expectedRevision'],
                                '--request-id', 'fictional-stale', expected_error=b'Categorization was not confirmed')
                for budget in budgets:
                    budget['history'] = history(budget['name'])
                    assert len(budget['history']) == 2 * args.runs
            endpoint = home / 'run/sandbox/host.json'
            deadline = monotonic() + 20
            while endpoint.exists() and monotonic() < deadline:
                sleep(.1)
            assert not endpoint.exists()
            with local_connection(home, 'sandbox'):
                for budget in budgets:
                    target, change = budget['name'], budget['intent']
                    assert history(target) == budget['history']
                    retry = cli(target, 'trnx', 'categorize', 'tx-000000', '--category', 'Fixture / Group 56', '--agent', 'Benchmark',
                                '--revision', change['expectedRevision'], '--request-id', change['requestId'])
                    assert retry == change
            deadline = monotonic() + 20
            while endpoint.exists() and monotonic() < deadline:
                sleep(.1)
            assert not endpoint.exists()
            for budget in budgets:
                verify_portable_file(Path(budget['path']), transaction_ids=[f'tx-{i:06}' for i in range(args.runs)],
                                     accounts=args.runs, total=budget['transactions'])
            for _ in range(args.runs):
                cli('Missing fictional budget', 'trnx', 'list', '--help', plain=True, sample='help_after')
    report = {}
    for key, rows in samples.items():
        times = sorted(row['wall_ms'] for row in rows)
        report[key] = {'n': len(rows), 'median_ms': round(median(times), 2),
                       'p95_ms': round(times[math.ceil(.95 * len(times)) - 1], 2),
                       'max_ms': round(max(times), 2), 'samples': rows}
    print(json.dumps({'python': platform.python_version(), 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
                      'versions': {name: version(name) for name in ('whab', 'whab_plugin_cli', 'whab_plugin_sqlite')},
                      'transactions': sizes, 'accounts': max(100, args.runs), 'categories': 57, 'runs': args.runs,
                      'seed': 20261110, 'cold': cold, 'saved_results_verified': True, 'commands': report}, indent=2))


if __name__ == '__main__':
    main()
