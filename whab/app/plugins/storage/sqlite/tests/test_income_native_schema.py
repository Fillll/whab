"""Explicit financial treatment activates a sticky older-reader compatibility fence."""

import pytest
from whab_plugin_sqlite import Store


@pytest.mark.parametrize("kind", ["income", "transfer", "opening_balance"])
def test_income_fence_read_only_readiness_and_undo_retains_native_identity(tmp_path, kind):
    path = tmp_path / 'fictional.sqlite'
    store = Store(path, month='2026-11')
    before = store.commit_evidence()
    assert store._authority_metadata()['formatVersion'] == 2
    category = store.add_category('personal', {'name': 'Salary', 'group': 'Income only', 'target': 0, 'kind': kind})
    expected_format = 5
    assert store._authority_metadata()['formatVersion'] == expected_format
    store.classify_category('personal', category, 'expense')
    # Even removing the last income kind cannot turn an older interpreter back on.
    assert store._authority_metadata()['formatVersion'] == expected_format
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


@pytest.mark.parametrize('kind', ['income', 'opening_balance'])
def test_income_fence_also_survives_complete_local_storage_transfer(tmp_path, kind):
    source = Store(tmp_path / 'source.sqlite', month='2026-11')
    destination = Store(tmp_path / 'destination.sqlite', month='2026-11', initial_role='staged')
    try:
        source.add_category('personal', {'name': 'Salary', 'group': 'Finance', 'target': 0, 'kind': kind})
        destination.import_local('fictional-income-transfer', source.export_local())
        assert destination._authority_metadata()['formatVersion'] == 5
        before, after = source.export_budget(), destination.export_budget()
        before['categories'].sort(key=lambda row: row['id'])
        after['categories'].sort(key=lambda row: row['id'])
        assert after == before
    finally:
        source.close()
        destination.close()


def test_opening_fence_survives_later_income_write_undo_and_history_only_file_restore(tmp_path):
    source = Store(tmp_path / 'source.sqlite', month='2026-11')
    destination = Store(tmp_path / 'restored.sqlite', month='2026-11')
    local = Store(tmp_path / 'local.sqlite', month='2026-11', initial_role='staged')
    try:
        opening = source.add_category('personal', {'name': 'Beginning', 'group': 'Finance', 'kind': 'opening_balance'})
        source.add_category('personal', {'name': 'Salary', 'group': 'Finance', 'kind': 'income'})
        assert source._authority_metadata()['formatVersion'] == 5
        with source.history_scope({'operation': 'delete_category'}):
            source.delete_category('personal', opening)
        assert all(row.get('kind') != 'opening_balance' for row in source.categories('personal'))
        files = source.export_budget_files('personal')
        destination.restore_budget_files('personal', files)
        assert destination._authority_metadata()['formatVersion'] == 5
        local.import_local('fictional-opening-history', source.export_local())
        assert local._authority_metadata()['formatVersion'] == 5
        entry = next(row for row in source.budget_history('personal')['entries'] if row['operation'] == 'delete_category')
        for action in ('undo', 'redo'):
            plan = source.history_preview('personal', action, entry['id'])
            source.history_apply('personal', action, entry['id'], plan['fingerprint'])
            assert source._authority_metadata()['formatVersion'] == 5
    finally:
        source.close()
        destination.close()
        local.close()
