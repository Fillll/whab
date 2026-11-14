"""Explicit financial treatment activates a sticky older-reader compatibility fence."""

from whab_plugin_sqlite import Store


def test_income_fence_read_only_readiness_and_undo_retains_native_identity(tmp_path):
    path = tmp_path / 'fictional.sqlite'
    store = Store(path, month='2026-11')
    before = store.commit_evidence()
    assert store._authority_metadata()['formatVersion'] == 2
    category = store.add_category('personal', {'name': 'Salary', 'group': 'Income only', 'target': 0, 'kind': 'income'})
    assert store._authority_metadata()['formatVersion'] == 3
    store.classify_category('personal', category, 'expense')
    # Even removing the last income kind cannot turn an older interpreter back on.
    assert store._authority_metadata()['formatVersion'] == 3
    current = store.commit_evidence()
    assert current.instance_id == before.instance_id and current.authority_epoch == before.authority_epoch
    store.close()
    reader = Store(path, month='2026-11', read_only=True)
    try:
        assert reader.commit_evidence() == current
        assert reader.categories('personal')[-1]['name'] == 'Salary'
    finally:
        reader.close()


def test_read_only_legacy_column_requires_explicit_writable_upgrade(tmp_path):
    import sqlite3

    import pytest

    from whab.domain.errors import StoreError

    path = tmp_path / 'legacy-fictional.sqlite'
    store = Store(path, month='2026-11')
    before = store.export_budget()
    store.close()
    # Reproduce the genuine pre-Income nullable-column schema on a fictional file.
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE categories DROP COLUMN kind')
    with pytest.raises(StoreError, match='upgrade it explicitly'):
        Store(path, month='2026-11', read_only=True)
    upgraded = Store(path, month='2026-11')
    try:
        assert upgraded.export_budget() == before
    finally:
        upgraded.close()
