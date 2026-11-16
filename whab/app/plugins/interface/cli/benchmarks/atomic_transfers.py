"""Reproducible fresh-process transfer-pair benchmark, fictional budgets only.

From whab/app:
  .venv/bin/python plugins/interface/cli/benchmarks/atomic_transfers.py --runs 25 --transactions 100000 --output /tmp/whab-transfer-benchmark.json

Defaults measure 100 fresh bulk CLI processes (25 each at 1/10/50/100 pairs).
Each size also measures an equivalent complete workflow of separate one-pair
commands. Use --separate-runs for more baseline samples. Provider calls are absent.
All fixture/runtime files are temporary; no production home or registry is used.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from copy import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter, sleep
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'scripts'))
from benchmark_atomic_bulk import host, summarize, verify
from benchmark_cli_matrix import fixture

from whab.budgets.container import decode_container, encode_container
from whab.budgets.document import atomic_write
from whab.domain.history import financial
from whab.initialization import initialize_home
from whab.plugins.local import local_connection


def source_digest():
    app = Path(__file__).resolve().parents[4]
    digest = hashlib.sha256()
    for directory in ('src/whab', 'plugins/storage/sqlite/src', 'plugins/storage/json/src', 'plugins/interface/cli/src'):
        for path in sorted((app / directory).rglob('*.py')):
            digest.update(str(path.relative_to(app)).encode())
            digest.update(path.read_bytes())
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()


def transfer_fixture(root, total, pairs):
    from whab_plugin_sqlite import Store
    budget = fixture(root, total, 100)
    document = decode_container(Path(budget['path']).read_bytes())
    store = Store(':memory:', _authority=False)
    try:
        store.restore_budget_files('personal', {name.removeprefix('plugins/sqlite/'): raw for name, raw in document.files.items()})
        snapshot = store.export_budget('personal')
        for index, row in enumerate(snapshot['transactions'][:pairs * 2]):
            row.update(accountId='account-' + str(index % 2), amount=(10000 + index // 2) * (1 if index % 2 == 0 else -1), date='2026-11-01')
        snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
        store.close()
        store = Store(':memory:', _authority=False)
        store.restore_budget('personal', snapshot)
        files = {'plugins/sqlite/' + name: raw for name, raw in store.export_budget_files().items()}
        atomic_write(Path(budget['path']), encode_container(document.manifest, files))
    finally:
        store.close()
    return budget


def segment(size, args):
    executable = str(Path(sys.executable).parent / 'whab')
    bulk_samples, separate_samples, expected = [], [], {}
    with TemporaryDirectory(prefix='whab-transfer-benchmark-') as directory:
        root = Path(directory)
        home, metrics = root / 'runtime', root / 'counts.json'
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
        env.update(HOME=directory, WHAB_HOME=str(home), WHAB_ENV='sandbox', WHAB_UI='', WHAB_PLUGINS='', WHAB_STORAGE='sqlite', ALLOW_LIVE_PLAID='false')
        cursor = 0
        def next_pairs():
            nonlocal cursor
            result = [{'outId': f'tx-{2 * index:06}', 'inId': f'tx-{2 * index + 1:06}'} for index in range(cursor, cursor + size)]
            cursor += size
            return result
        def counts():
            return json.loads(metrics.read_text())
        def cli(*parts, stdin=None):
            started = perf_counter()
            result = subprocess.run([executable, '--home', str(home), '--environment', 'sandbox', *parts], input=stdin,
                                    env=env, capture_output=True, text=True, timeout=180)
            duration = (perf_counter() - started) * 1000
            assert result.returncode == 0, result.stderr
            data = json.loads(result.stdout)
            for row in data['data']['changes']:
                expected[row['id']] = {key: row['after'][key] for key in ('id', 'accountId', 'date', 'merchant', 'amount', 'note', 'categoryId', 'transferPeerId', 'reviewed', 'isTransfer')}
            return data, duration
        def bulk(pairs):
            return cli(budget['name'], 'trnx', 'match-transfers', '--file', '-', '--agent', 'Benchmark', stdin=json.dumps(pairs))
        with patch.dict(os.environ, env, clear=True):
            initialize_home(home)
            budget = transfer_fixture(root, args.transactions, size * (1 + args.runs + args.separate_runs))
            with (root / 'host.log').open('w') as log:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--host', str(home), str(metrics)], env=env, stdout=log, stderr=log)
                try:
                    started = perf_counter()
                    deadline = monotonic() + 120
                    while not (home / 'run/sandbox/host.json').exists():
                        assert process.poll() is None and monotonic() < deadline, 'Fictional host readiness failed'
                        sleep(.05)
                    with local_connection(home, 'sandbox', timeout=120) as connection:
                        opened = connection.post('/api/budget/open', content=Path(budget['path']).read_bytes(), headers={'Content-Type': 'application/zip'})
                        opened.raise_for_status()
                        cold = {'activation_ms': (perf_counter() - started) * 1000}
                        _, cold['first_mutation_ms'] = bulk(next_pairs())
                        history_count = 1
                        for _ in range(args.runs):
                            before = counts()
                            data, duration = bulk(next_pairs())
                            after = counts()
                            sample = {'wall_ms': duration, 'load_average': os.getloadavg(), 'checkpoints': after['checkpoints'] - before['checkpoints'], 'native_commits': after['generation'] - before['generation']}
                            assert sample['checkpoints'] == sample['native_commits'] == 1, sample
                            assert data['data']['count'] == size * 2
                            bulk_samples.append(sample)
                            history_count += 1
                        for _ in range(args.separate_runs):
                            before, duration = counts(), 0
                            for pair in next_pairs():
                                _, elapsed = cli(budget['name'], 'trnx', 'match-transfer', pair['outId'], pair['inId'], '--agent', 'Benchmark')
                                duration += elapsed
                            after = counts()
                            sample = {'wall_ms': duration, 'load_average': os.getloadavg(), 'checkpoints': after['checkpoints'] - before['checkpoints'], 'native_commits': after['generation'] - before['generation']}
                            assert sample['checkpoints'] == sample['native_commits'] == size, sample
                            separate_samples.append(sample)
                            history_count += size
                        actual = home / 'budgets' / budget['id'] / 'sandbox' / 'opened.whab'
                        restored = verify(actual, args.transactions, expected, history_count)
                finally:
                    process.terminate()
                    process.wait(timeout=60)
        return {'cold': cold, 'bulk_samples': bulk_samples, 'separate_samples': separate_samples, 'restored': restored}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runs', type=int, default=25, help='Fresh bulk processes per size; default 25 across 4 sizes means 100 processes')
    parser.add_argument('--transactions', type=int, default=100000, help='Fictional populated ledger size, default 100000')
    parser.add_argument('--sizes', default='1,10,50,100', help='Comma-separated pair counts, each 1–100')
    parser.add_argument('--separate-runs', type=int, default=1, help='Equivalent separate-pair workflows per size; 0 reruns only primary bulk samples; baseline sample count reported')
    parser.add_argument('--output', type=Path, required=True, help='Raw results JSON path outside source checkout')
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(',')]
    if not 1 <= args.runs <= 1000 or not sizes or any(not 1 <= size <= 100 for size in sizes) or not 0 <= args.separate_runs <= 10:
        parser.error('Use 1–1000 runs, 1–100 pairs, and 0–10 separate runs.')
    if args.transactions < max(10000, max(sizes) * 2 * (min(args.runs, 1000 // max(sizes)) + args.separate_runs + 1)):
        parser.error('Fictional ledger must have enough unique pairs and at least 10000 transactions.')
    result = {'python': platform.python_version(), 'platform': platform.platform(), 'transactions': args.transactions,
              'source_digest_before': source_digest(),
              'provider_latency': 'excluded: fictional data; no provider calls', 'instrumentation': 'one local metrics JSON write per successful checkpoint', 'cases': []}
    for size in sizes:
        chunks, remaining = [], args.runs
        while remaining:
            selected = copy(args)
            selected.runs = min(remaining, max(1, 1000 // size))
            selected.separate_runs = args.separate_runs if not chunks else 0
            chunks.append(segment(size, selected))
            remaining -= selected.runs
        samples = [row for chunk in chunks for row in chunk['bulk_samples']]
        singles = [row for chunk in chunks for row in chunk['separate_samples']]
        times = summarize([row['wall_ms'] for row in samples])
        separate = summarize([row['wall_ms'] for row in singles]) if singles else None
        case = {'pairs': size, 'fresh_bulk_processes': len(samples), 'separate_workflows': len(singles), 'bulk': times, 'separate': separate,
                'per_pair_p50_ms': times['p50_ms'] / size, 'per_leg_p50_ms': times['p50_ms'] / size / 2,
                'workflow_saved_p50_ms': separate['p50_ms'] - times['p50_ms'] if separate else None,
                'workflow_saved_percent': 100 * (1 - times['p50_ms'] / separate['p50_ms']) if separate else None,
                'p95_target_ms': 250, 'p95_target_met': times['p95_ms'] < 250, 'segments': chunks}
        result['cases'].append(case)
        result['source_digest_after'] = source_digest()
        result['source_unchanged'] = result['source_digest_before'] == result['source_digest_after']
        args.output.write_text(json.dumps(result, indent=2))
        print(json.dumps({key: value for key, value in case.items() if key != 'segments'}), flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--host':
        host(sys.argv[2], sys.argv[3])
    else:
        main()
