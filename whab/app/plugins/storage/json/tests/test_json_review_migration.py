"""Legacy JSON correction retains exact audit history and durable recovery."""
from contextlib import closing

import pytest
from whab_plugin_json import Store, codec

from whab.plugins import StoreError


def legacy(path):
    with closing(Store(path, month='2026-11')) as store:
        account = store.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 10000})
        tx = store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional import', 'amount': -100, 'categoryId': None})
    data = codec.decode(path.read_bytes())
    state = data['workspaces']['personal']
    state['categories'] = {key: row for key, row in state['categories'].items() if not row.get('systemKind')}
    state['transactions'][tx]['reviewed'] = True
    raw, _ = codec.encode(data)
    path.write_bytes(raw)
    return tx, state['history'], data['generation']


@pytest.mark.parametrize('boundary', ['before_replace', 'replaced'])
def test_interrupted_migration_recovers_without_history_duplication(tmp_path, boundary):
    path = tmp_path / 'fictional.json'
    tx, history, generation = legacy(path)
    def interrupted(stage):
        if stage == boundary:
            raise OSError('Synthetic migration interruption')
    with pytest.raises(StoreError):
        Store(path, month='2026-11', commit_hook=interrupted)
    with closing(Store(path, month='2026-11')) as store:
        saved = store.export_budget()
        row = next(row for row in saved['transactions'] if row['id'] == tx)
        assert row['categoryId'] is None and row['reviewed'] is False
        assert saved['history']['entries'][:len(history['entries'])] == history['entries']
        assert sum(row['operation'] == 'migrate_system_categories' for row in saved['history']['entries']) == 1
        assert store._data['generation'] == generation + 1
    with closing(Store(path, month='2026-11')) as store:
        assert store.export_budget() == saved
        assert store._data['generation'] == generation + 1
