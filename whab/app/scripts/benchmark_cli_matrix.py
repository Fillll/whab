"""Interleave fixed-output CLI workloads across fictional budget sizes.

Run from whab/app with .venv/bin/python scripts/benchmark_cli_matrix.py.
Each size returns the same 300 monthly rows, 13 merchant matches and ten
uncategorized samples. Full-history output and provider calls are excluded.
Raw wall times, client CPU times and system load accompany every observation.
For a before/after comparison, run --prepare-fixtures /tmp/fictional-seeds once
from the baseline source, then use --fixtures-directory /tmp/fictional-seeds in
both source versions. Copies preserve identical input bytes and seed provenance;
activation, first mutations and independent saved-file restoration still run.
After each timed mutation, an untimed public budget-status read also requires
every configured copy to be saved; final restoration alone is insufficient.
"""

import argparse
import gc
import json
import math
import os
import platform
import random
import subprocess
import sys
from hashlib import sha256
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

FIXTURE_FORMAT = 'whab-fictional-cli-performance-1'


def verify_saved_status(connection, identifier):
    """Read copy status without checkpointing or decoding portable budget bytes."""
    response = connection.get(f'/budgets/{identifier}/api/budget')
    response.raise_for_status()
    budget = response.json()
    assert budget.get('id') == identifier and budget.get('portable') is True, 'Save proof must identify the portable budget'
    copies = budget.get('copies')
    assert isinstance(copies, list) and copies, 'Save proof requires the budget file status'
    assert copies[0].get('name') == 'Budget file', 'Save proof must include the primary budget file'
    assert all(copy.get('status') == 'saved' for copy in copies), 'An acknowledged mutation must already be durably saved'
    return True


def source_identity():
    return {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'source_diff_sha256': sha256(subprocess.check_output(['git', 'diff', '--binary', 'HEAD'])).hexdigest(),
            'versions': {name: version(name) for name in ('whab', 'whab_plugin_cli', 'whab_plugin_sqlite')}}


def write_fixtures(directory, sizes, accounts):
    """Prepare immutable fictional input once, before either timed source run."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    index = directory / 'fixtures.json'
    if index.exists():
        raise ValueError('Choose a new fixture directory; existing seed provenance is never overwritten.')
    records = {}
    for total in sizes:
        seed_root = directory / f'{total}-{accounts}'
        seed_root.mkdir()
        budget = fixture(seed_root, total, accounts)
        path = Path(budget['path'])
        records[f'{total}:{accounts}'] = {**budget, 'accounts': accounts, 'categories': 57,
            'path': str(path.relative_to(directory)), 'sha256': sha256(path.read_bytes()).hexdigest()}
    index.write_text(json.dumps({'format': FIXTURE_FORMAT, **source_identity(), 'fixtures': records}, indent=2) + '\n')
    return index


def prepared_fixture(root, total, accounts, directory=None):
    """Copy a hash-verified seed into this isolated run; never mutate the seed."""
    if directory is None:
        budget = fixture(root, total, accounts)
        return {**budget, 'fixture': {**source_identity(), 'seeded': False,
                'sha256': sha256(Path(budget['path']).read_bytes()).hexdigest()}}
    directory = Path(directory).resolve()
    record = json.loads((directory / 'fixtures.json').read_text())
    if record.get('format') != FIXTURE_FORMAT:
        raise ValueError('This is not a generated fictional benchmark fixture directory.')
    seed = record['fixtures'][f'{total}:{accounts}']
    source = (directory / seed['path']).resolve()
    if not source.is_relative_to(directory) or seed['transactions'] != total or seed['accounts'] != accounts or seed['categories'] != 57:
        raise ValueError('Fixture identity or record counts do not match this workload.')
    raw = source.read_bytes()
    if sha256(raw).hexdigest() != seed['sha256']:
        raise ValueError('Fictional fixture bytes changed after preparation.')
    target = root / f'fictional-{total}.whab'
    target.write_bytes(raw)
    return {key: seed[key] for key in ('id', 'name', 'transactions')} | {'path': str(target),
        'fixture': {'sha256': seed['sha256'], 'seeded': True,
                    **{key: record[key] for key in ('source_commit', 'versions')}}}


class MeasuredProcess(subprocess.Popen):
    """Collect only this CLI child's CPU, excluding separately reaped hosts."""
    usage = None

    def _try_wait(self, flags):
        pid, status, usage = os.wait4(self.pid, flags)
        if pid:
            self.usage = usage
        return pid, status


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
    fixtures = parser.add_mutually_exclusive_group()
    fixtures.add_argument('--prepare-fixtures', metavar='DIRECTORY', help='Generate immutable fictional seeds and provenance, then exit without timing')
    fixtures.add_argument('--fixtures-directory', help='Reuse hash-verified fictional seeds; copy each into the isolated runtime before timing')
    args = parser.parse_args()
    sizes = [int(text) for text in args.sizes.split(',')]
    if not 1 <= args.runs <= 1000 or len(sizes) != len(set(sizes)) or not sizes or min(sizes) < max(300, args.runs) or max(sizes) > 250000:
        parser.error('Use unique sizes between max(300,runs) and 250000, and 1–1000 runs; portable size bounds still apply.')
    if args.prepare_fixtures:
        print(write_fixtures(args.prepare_fixtures, sizes, max(100, args.runs)))
        return
    initial_source = source_identity()
    samples, cold, durability_checks = {}, {}, {}
    executable = str(Path(sys.executable).parent / 'whab')
    with TemporaryDirectory(prefix='whab-cli-matrix-') as directory:
        root = Path(directory)
        home = root / 'runtime'
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
        env.update(HOME=directory, WHAB_HOME=str(home), WHAB_ENV='sandbox', WHAB_UI='',
                   WHAB_PLUGINS='', WHAB_STORAGE='sqlite', ALLOW_LIVE_PLAID='false')

        def cli(*command, sample=None, expected_error=None, plain=False):
            load = os.getloadavg()
            started = perf_counter()
            with MeasuredProcess([executable, '--home', str(home), '--environment', 'sandbox', *command],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                try:
                    stdout, stderr = process.communicate(timeout=120)
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
            elapsed = (perf_counter() - started) * 1000
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            usage = process.usage
            assert usage is not None, 'Per-child CPU accounting must be available'
            if expected_error is not None:
                assert result.returncode and expected_error in result.stderr, result.stderr.decode()
                return None
            assert result.returncode == 0, result.stderr.decode()
            if sample:
                samples.setdefault(sample, []).append({'wall_ms': elapsed,
                    'client_user_ms': usage.ru_utime * 1000,
                    'client_system_ms': usage.ru_stime * 1000,
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

        def saved(connection, budget, phase):
            verify_saved_status(connection, budget['id'])
            checks = durability_checks.setdefault(budget['name'], {})
            checks[phase] = checks.get(phase, 0) + 1

        with patch.dict(os.environ, env, clear=True):
            initialize_home(home)
            budgets = [prepared_fixture(root, size, max(100, args.runs), args.fixtures_directory) for size in sizes]
            atomic_write(home / 'budgets/index-sandbox.json', json.dumps([
                {key: budget[key] for key in ('id', 'name', 'path')} for budget in budgets]).encode())
            gc.collect()
            cli('Missing fictional budget', 'trnx', 'list', '--help', plain=True)
            for _ in range(args.runs):
                cli('Missing fictional budget', 'trnx', 'list', '--help', plain=True, sample='help_before')
            with local_connection(home, 'sandbox') as connection:
                for budget in budgets:
                    target = budget['name']
                    start = perf_counter()
                    cli(target, 'category', 'list')
                    cold[target] = {'activation_ms': (perf_counter() - start) * 1000, 'fixture': budget['fixture']}
                    saved(connection, budget, 'activation')
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
                            sample = f'{budget["transactions"]}:{label}'
                            result = cli(target, *command, sample=sample)
                            if label in {'categorize', 'classify'}:
                                saved(connection, budget, 'timed_mutation')
                                samples[sample][-1]['durable_status_verified'] = True
                            return result
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
                            saved(connection, budget, 'retry_before_restart')
                            cli(target, 'trnx', 'categorize', 'tx-000000', '--clear', '--revision', change['expectedRevision'],
                                '--request-id', 'fictional-stale', expected_error=b'Categorization was not confirmed')
                            saved(connection, budget, 'stale_before_restart')
                for budget in budgets:
                    budget['history'] = history(budget['name'])
                    assert len(budget['history']) == 2 * args.runs
            endpoint = home / 'run/sandbox/host.json'
            deadline = monotonic() + 20
            while endpoint.exists() and monotonic() < deadline:
                sleep(.1)
            assert not endpoint.exists()
            with local_connection(home, 'sandbox') as connection:
                for budget in budgets:
                    target, change = budget['name'], budget['intent']
                    assert history(target) == budget['history']
                    retry = cli(target, 'trnx', 'categorize', 'tx-000000', '--category', 'Fixture / Group 56', '--agent', 'Benchmark',
                                '--revision', change['expectedRevision'], '--request-id', change['requestId'])
                    assert retry == change
                    saved(connection, budget, 'retry_after_restart')
            deadline = monotonic() + 20
            while endpoint.exists() and monotonic() < deadline:
                sleep(.1)
            assert not endpoint.exists()
            for budget in budgets:
                cold[budget['name']]['saved_file'] = verify_portable_file(
                    Path(budget['path']), transaction_ids=[f'tx-{i:06}' for i in range(args.runs)],
                    accounts=args.runs, total=budget['transactions'])
            for _ in range(args.runs):
                cli('Missing fictional budget', 'trnx', 'list', '--help', plain=True, sample='help_after')
    report = {}
    for key, rows in samples.items():
        times = sorted(row['wall_ms'] for row in rows)
        report[key] = {'n': len(rows), 'median_ms': round(median(times), 2),
                       'p95_ms': round(times[math.ceil(.95 * len(times)) - 1], 2),
                       'max_ms': round(max(times), 2), 'samples': rows}
    final_source = source_identity()
    print(json.dumps({'source_commit': final_source['source_commit'], 'source_before': initial_source,
                      'source_after': final_source, 'source_changed_during_run': initial_source != final_source,
                      'python': platform.python_version(), 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
                      'versions': {name: version(name) for name in ('whab', 'whab_plugin_cli', 'whab_plugin_sqlite')},
                      'transactions': sizes, 'accounts': max(100, args.runs), 'categories': 57, 'runs': args.runs,
                      'seed': 20261110, 'cold': cold, 'saved_results_verified': True,
                      'durability_verification': {'checks': durability_checks,
                          'scope': 'Public budget-copy status asserted saved after every timed mutation, cold activation and categorization retry/refusal. These small reads run outside CLI wall timers and perform no checkpoint or full-file decode; they can affect inter-command pacing. Final independent saved-container restoration remains separate.'},
                      'commands': report}, indent=2))


if __name__ == '__main__':
    main()
