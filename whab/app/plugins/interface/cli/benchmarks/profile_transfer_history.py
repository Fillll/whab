"""Diagnose audit reserve growth on fictional native storage only.

From whab/app:
  .venv/bin/python plugins/interface/cli/benchmarks/profile_transfer_history.py --output /tmp/whab-transfer-history-profile.json

This is diagnostic instrumentation, not a fresh-process latency benchmark. It
measures a native operation and portable-image export, without CLI/HTTP or a
host durable-container checkpoint. cProfile adds overhead. No production data,
provider calls, storage limit changes or implementation edits are involved.
"""

import argparse
import cProfile
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_transfers import transfer_fixture
from whab_plugin_sqlite import Store, history_writes, portable

from whab.budgets.container import decode_container
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json

current = []
original_history = history_writes.save_history
original_image = portable._build_image

def history(store, workspace, journal, previous, *, cache_valid):
    before = getattr(store, '_history_blob_positions', {}).get(workspace)
    start = perf_counter()
    result = original_history(store, workspace, journal, previous, cache_valid=cache_valid)
    after = getattr(store, '_history_blob_positions', {}).get(workspace)
    current.append({'stage': 'history', 'store': type(store).__name__, 'cache_valid': cache_valid,
                    'ms': 1000 * (perf_counter()-start), 'before_bytes': before[1:3] if before else None,
                    'after_bytes': after[1:3] if after else None})
    return result

def image(raw, previous, journal, hints):
    start = perf_counter()
    result = original_image(raw, previous, journal, hints)
    current.append({'stage': 'portable_image', 'ms': 1000 * (perf_counter()-start),
                    'before_size': len(raw), 'after_size': len(result[0]) if result else None})
    return result

history_writes.save_history = history
portable._build_image = image
parser=argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('--transactions', type=int, default=100000, help='Fictional populated ledger size, default 100000')
parser.add_argument('--pairs', type=int, default=50, help='Explicit pairs per operation, 1–100; default 50')
parser.add_argument('--runs', type=int, default=20, help='Warm native operations after one cold operation; pairs*runs <=1000; default 20')
parser.add_argument('--output', type=Path, required=True, help='Raw diagnostic JSON; growth profiles use the same directory and stem')
args=parser.parse_args()
if not 1 <= args.pairs <= 100 or not 1 <= args.runs <= 100 or args.pairs*args.runs > 1000 or args.transactions < max(10000,2*args.pairs*(args.runs+2)):
    parser.error('Use 1–100 pairs, 1–100 runs, <=1000 warm pairs, and at least 10000 fictional transactions.')
rows=[]
with TemporaryDirectory(prefix='whab-transfer-native-profile-') as temp:
    root=Path(temp)
    fixture=transfer_fixture(root, args.transactions, args.pairs*(args.runs+2))
    document=decode_container(Path(fixture['path']).read_bytes())
    store=Store(root/'native.sqlite')
    try:
        store.restore_budget_files('personal', {name.removeprefix('plugins/sqlite/'): raw for name,raw in document.files.items()})
        store.export_budget_files()
        for batch in range(args.runs+1):
            pairs=[{'outId':f'tx-{index*2:06}','inId':f'tx-{index*2+1:06}'} for index in range(batch*args.pairs,(batch+1)*args.pairs)]
            preview=store.preview_transfer_matches('personal',pairs)
            evidence=store.commit_evidence()
            operation=StorageOperation(uuid4().hex,evidence.authority_epoch,evidence.generation,'personal','recorded_command',canonical_storage_json({
                'operation':'apply_transfer_matches','parameters':{'pairs':pairs,'reviewed':False,'fingerprint':preview['fingerprint']},
                'context':{'actor':'Benchmark','actorKind':'agent','operation':'match_transfers'}}))
            current=[]
            profile=cProfile.Profile()
            start=perf_counter()
            profile.enable()
            store.execute_operation(operation)
            store.export_budget_files()
            profile.disable()
            result={'batch':batch,'warm':batch>0,'ms':1000*(perf_counter()-start),'events':current}
            rows.append(result)
            args.output.write_text(json.dumps(rows,indent=2))
            if any(r['stage']=='history' and r['before_bytes'] and r['after_bytes'][1]!=r['before_bytes'][1] for r in current):
                profile.dump_stats(args.output.with_name(args.output.stem+f'-growth-{batch}.pstats'))
            print(json.dumps(result),flush=True)
    finally:
        store.close()
