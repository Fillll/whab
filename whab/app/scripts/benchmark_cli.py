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
    parser.add_argument('--transactions', type=int, default=14550)
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

        def cli(*command, label=None):
            started = perf_counter()
            process = subprocess.run([executable, '--home', str(home), '--environment', 'sandbox', *command],
                                     env=env, capture_output=True, timeout=120)
            elapsed = (perf_counter() - started) * 1000
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
            for i in range(args.transactions):
                category = f'Fixture: Group {i % 57},Fixture,Group {i % 57}' if i % 9 else ',,'
                rows.append(f'Account {i % accounts},,202{4 + (i // 336) % 3}-{1 + (i // 28) % 12:02}-{1 + i % 28:02},Fixture payee {i % 1103},{category},Synthetic memo {i},$12.34,$0.00,Cleared\n')
            export.writestr('Register.csv', ''.join(rows))
        target = 'Fictional performance'
        with patch.dict(os.environ, env, clear=True):
            run('budget', 'list', label='cold new host')
            with local_connection(home, 'sandbox'):
                created = run('budget', 'create', target)
                run(target, 'plugin', 'enable', 'ynab-zip-importer', label='plugin enable')
                preview = run(target, 'import', 'ynab', 'preview', str(archive), '--currency', 'USD', label='import preview')
                assert preview['canImport'] and preview['transactions'] == args.transactions
                run(target, 'import', 'ynab', 'commit', str(archive), '--currency', 'USD', '--fingerprint', preview['fingerprint'], label='import commit')
                transactions = run(target, 'trnx', 'list', label='full history output')['data']
                assert len(transactions) == args.transactions
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
                saved = run(target, 'trnx', 'list')['data']
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
            reopened = run(target, 'trnx', 'list')['data']
            assert reopened == saved and budget_path.is_file()
            deadline = monotonic() + 20
            while endpoint.exists() and monotonic() < deadline:
                sleep(.1)
            assert not endpoint.exists()
    report = {name: {'n': len(times), 'median_ms': round(median(times), 2),
                     'p95_ms': round(sorted(times)[math.ceil(.95 * len(times)) - 1], 2),
                     'total_ms': round(sum(times), 2)} for name, times in samples.items()}
    print(json.dumps({'transactions': args.transactions, 'accounts': accounts, 'runs': args.runs,
                      'saved_result_verified': True, 'commands': report}, indent=2))


if __name__ == '__main__':
    main()
