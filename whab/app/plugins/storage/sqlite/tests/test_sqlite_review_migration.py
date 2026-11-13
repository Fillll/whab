"""Recoverable synthetic legacy migration owned by SQLite, without bank I/O."""
import json
import sqlite3
from contextlib import closing

import pytest
from whab_plugin_sqlite import Store

from whab.plugins import StoreError


def legacy(path):
    store = Store(path, month='2026-11')
    account = store.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 10000})
    tx = store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional imported row', 'amount': -100, 'categoryId': None})
    # Simulate a prior version's current state; don't rewrite the existing audit.
    store.db.execute("DELETE FROM categories WHERE workspace='personal' AND system_kind IS NOT NULL")
    store.db.execute("UPDATE transactions SET reviewed=1 WHERE workspace='personal' AND id=?", (tx,))
    history = store.export_budget()['history']
    generation = store.commit_evidence().generation
    store.close()
    return tx, history, generation


def test_legacy_open_correction_is_audited_and_reopen_is_idempotent(tmp_path):
    path = tmp_path / 'fictional.db'
    tx, history, generation = legacy(path)
    with closing(Store(path, month='2026-11')) as store:
        assert next(row for row in store.transactions('personal') if row['id'] == tx)['reviewed'] is False
        saved = store.export_budget()
        assert saved['history']['entries'][:len(history['entries'])] == history['entries']
        assert saved['history']['entries'][-1]['operation'] == 'migrate_system_categories'
        assert store.commit_evidence().generation == generation + 1
    with closing(Store(path, month='2026-11')) as store:
        assert store.export_budget() == saved
        assert store.commit_evidence().generation == generation + 1


def test_interrupted_migration_rolls_back_then_reopens(tmp_path, monkeypatch):
    path = tmp_path / 'fictional.db'
    tx, history, _ = legacy(path)
    original = Store._write_state
    def interrupted(self, state):
        original(self, state)
        raise OSError('Synthetic interruption before native commit')
    with monkeypatch.context() as patch:
        patch.setattr(Store, '_write_state', interrupted)
        with pytest.raises(OSError, match='Synthetic interruption'):
            Store(path, month='2026-11')
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT reviewed FROM transactions WHERE workspace='personal' AND id=?", (tx,)).fetchone()[0] == 1
        assert json.loads(db.execute("SELECT record FROM budget_history WHERE workspace='personal'").fetchone()[0]) == history
    with closing(Store(path, month='2026-11')) as store:
        assert not next(row for row in store.transactions('personal') if row['id'] == tx)['reviewed']


def test_system_metadata_corruption_is_not_silently_adopted(tmp_path):
    path = tmp_path / 'fictional.db'
    with closing(Store(path, month='2026-11')) as store:
        store.db.execute("UPDATE categories SET name='User category collision' WHERE workspace='personal' AND system_kind='income'")
    with pytest.raises(StoreError, match='Reserved system category identity'):
        Store(path, month='2026-11')
