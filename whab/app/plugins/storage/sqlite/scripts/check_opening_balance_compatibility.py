"""Replay genuine pre-Opening-Balance readers against isolated fictional files.

Run from whab/app with its development Python environment. Full Git history must
contain the pinned 2.78.0 commit. No network, installation or runtime home is used.
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
PREVIOUS_COMMIT = 'ec9d8d9338ed3e239eb093d9b07d97d219f24af6'
OLD_READER = '''
import importlib, json, pathlib, sys
import whab
from whab.plugins import StoreError
root, backend, filename, read_only = sys.argv[1:]
module = importlib.import_module('whab_plugin_' + backend)
for item in (whab, module):
    assert pathlib.Path(item.__file__).resolve().is_relative_to(pathlib.Path(root).resolve())
assert whab.__version__ == '2.78.0'
try:
    store = module.Store(filename, month='2026-10', read_only=read_only == 'true')
except StoreError as error:
    print(json.dumps({'backend': backend, 'readOnly': read_only == 'true', 'rejected': True, 'error': str(error)}))
else:
    store.close()
    raise AssertionError('Old reader accepted unsupported Opening Balance semantics')
'''


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    from whab_plugin_json import Store as JSONStore
    from whab_plugin_sqlite import Store as SQLiteStore

    # Use the established compatibility harness's exact-Git-object extraction.
    spec = importlib.util.spec_from_file_location('whab_compatibility', ROOT / 'scripts/check_plugin_devkit.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('WHAB_', 'PLAID_', 'CLIENT_', 'SERVER_', 'TOKEN_ENCRYPTION_', 'ALLOW_LIVE_'))}
    with tempfile.TemporaryDirectory(prefix='whab-opening-compatibility-') as temporary:
        base = Path(temporary)
        old = helper.source_at_revision(base, environment, PREVIOUS_COMMIT)
        versions = {'whab': helper.host_version(old)}
        for name, path in [('sqlite', old / 'plugins/storage/sqlite'), ('json', old / 'plugins/storage/json')]:
            versions[name] = tomllib.loads((path / 'pyproject.toml').read_text())['project']['version']
        assert versions == {'whab': '2.78.0', 'sqlite': '1.27.0', 'json': '1.19.0'}, versions
        environment['PYTHONPATH'] = os.pathsep.join(str(path) for path in (
            old / 'src', old / 'plugins/storage/sqlite/src', old / 'plugins/storage/json/src'))
        evidence = []
        for backend, store_class in [('sqlite', SQLiteStore), ('json', JSONStore)]:
            for history_only in (False, True):
                path = base / f'fictional-{backend}-{history_only}.{backend}'
                store = store_class(path, month='2026-10')
                try:
                    category = store.add_category('personal', {'name': 'Fictional Beginning', 'group': 'Finance',
                                                              'target': 0, 'kind': 'opening_balance'})
                    if history_only:
                        store.classify_category('personal', category, 'expense')
                    before = store.export_budget('personal')
                    native = store.commit_evidence()
                finally:
                    store.close()
                raw = path.read_bytes()
                for read_only in (False, True):
                    result = subprocess.run([sys.executable, '-c', OLD_READER, str(old), backend, str(path), str(read_only).lower()],
                                            cwd=base, env=environment, capture_output=True, text=True, timeout=30)
                    if result.returncode:
                        raise RuntimeError(f'{backend}, historyOnly={history_only}, readOnly={read_only}: '
                                           + (result.stderr or result.stdout))
                    row = json.loads(result.stdout)
                    assert row['rejected'] and path.read_bytes() == raw
                    evidence.append({**row, 'historyOnly': history_only, 'unchanged': True})
                current = store_class(path, month='2026-10', read_only=True)
                try:
                    assert current.export_budget('personal') == before
                    assert current.commit_evidence() == native
                finally:
                    current.close()
        print(json.dumps({'commit': PREVIOUS_COMMIT, 'versions': versions, 'checks': evidence}, indent=2))


if __name__ == '__main__':
    main()
