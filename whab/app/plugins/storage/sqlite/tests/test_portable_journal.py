"""Complete portable journals reject corruption and retain committed state on reopen."""

import sqlite3
from copy import deepcopy
from hashlib import sha256

import pytest
from whab_plugin_sqlite import Store, portable
from whab_plugin_sqlite import portable_journal as journal

from tests.receipt_images import png
from whab.domain.history import append as append_history
from whab.plugins import StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json


def frame(payload):
    return len(payload).to_bytes(8, 'big') + sha256(payload).digest() + payload


def request(store, transaction_id, note, identifier):
    evidence = store.commit_evidence()
    operation = StorageOperation(identifier, evidence.authority_epoch, evidence.generation,
                                 'personal', 'edit_transaction', canonical_storage_json({
                                     'transactionId': transaction_id, 'note': note,
                                 }))
    return store.execute_operation(operation)


@pytest.fixture
def saved(tmp_path):
    store = Store(tmp_path / 'fictional.sqlite', month='2026-11')
    account = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})
    ids = [store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01',
             'merchant': 'Fictional shop', 'amount': 100, 'note': 'Original'}) for _ in range(2)]
    store.save_receipt('personal', ids[0], png())
    yield store, account, ids
    store.close()


def test_edits_append_only_suffixes_and_complete_file_reopens_with_history_and_receipt(saved, tmp_path):
    store, _, ids = saved
    original = store.export_budget_files()['budget.sqlite']
    assert journal.read(original)[1] == []
    request(store, ids[0], 'First saved note', 'first')
    first = store.export_budget_files()['budget.sqlite']
    request(store, ids[1], 'Second saved note', 'second')
    final = store.export_budget_files()['budget.sqlite']
    assert first.startswith(original) and final.startswith(first)
    assert len(journal.read(final)[1]) == 2
    expected = store.export_budget('personal')
    assert portable.read_files({'budget.sqlite': final}) == expected
    assert store.export_budget_files()['budget.sqlite'] == final
    target = Store(tmp_path / 'restored.sqlite', month='2026-11')
    try:
        target.restore_budget_files('personal', {'budget.sqlite': final})
        assert target.export_budget('personal') == expected
        assert target.get_receipt('personal', ids[0]) == png()
    finally:
        target.close()
    reopened = Store(tmp_path / 'restored.sqlite', month='2026-11')
    try:
        assert reopened.export_budget('personal') == expected
        assert reopened.get_receipt('personal', ids[0]) == png()
    finally:
        reopened.close()


def test_first_audited_edit_appends_to_validated_legacy_ledger_without_rebuilding(saved, monkeypatch):
    store, _, ids = saved
    # An old ledger may have complete financial records but no audit history.
    store.db.execute("DELETE FROM budget_history WHERE workspace='personal'")
    original = store.export_budget_files()['budget.sqlite']
    assert 'history' not in portable.read_files({'budget.sqlite': original})
    request(store, ids[0], 'First audited note', 'first-legacy-edit')
    def no_rebuild(*args, **kwargs):
        raise AssertionError('A verified initial snapshot must not be rebuilt')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'export_budget', no_rebuild)
        saved = store.export_budget_files()['budget.sqlite']
    assert saved.startswith(original)
    assert len(journal.read(saved)[1]) == 1
    assert portable.read_files({'budget.sqlite': saved}) == store.export_budget('personal')


def test_reordered_or_repeated_valid_frames_cannot_replay_against_another_history(saved):
    store, _, ids = saved
    original = store.export_budget_files()['budget.sqlite']
    request(store, ids[0], 'First', 'first')
    first = store.export_budget_files()['budget.sqlite']
    request(store, ids[1], 'Second', 'second')
    second = store.export_budget_files()['budget.sqlite']
    a, b = first[len(original):], second[len(first):]
    for altered in (original + b + a, original + a + a, original + b):
        with pytest.raises(StoreError):
            portable.read_files({'budget.sqlite': altered})
    # Complete old snapshots remain supported and represent exactly their own state.
    assert portable.read_files({'budget.sqlite': journal.read(original)[0]}) == portable.read_files({'budget.sqlite': original})


def test_compaction_and_unknown_changes_keep_all_history(saved, monkeypatch):
    store, _, ids = saved
    store.export_budget_files()
    monkeypatch.setattr(journal, 'MAX_FRAMES', 1)
    request(store, ids[0], 'Before compaction', 'first')
    first = store.export_budget_files()['budget.sqlite']
    assert len(journal.read(first)[1]) == 1
    request(store, ids[1], 'After compaction', 'second')
    compacted = store.export_budget_files()['budget.sqlite']
    assert journal.read(compacted)[1] == []
    assert portable.read_files({'budget.sqlite': compacted}) == store.export_budget('personal')
    store.add_category('personal', {'name': 'Fictional new category', 'group': 'Fictional'})
    rebuilt = store.export_budget_files()['budget.sqlite']
    assert journal.read(rebuilt)[1] == []
    assert portable.read_files({'budget.sqlite': rebuilt}) == store.export_budget('personal')


def test_external_sql_and_failed_commit_never_reuse_an_uncommitted_journal(saved, monkeypatch):
    store, _, ids = saved
    original = store.export_budget_files()
    before = store.export_budget('personal')
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('Fictional commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(store, '_save_authority', fail)
        with pytest.raises(sqlite3.OperationalError, match='Fictional commit failure'):
            request(store, ids[0], 'Never committed', 'failure')
    assert store.operation_receipt('failure') is None
    assert portable.read_files(original) == portable.read_files(store.export_budget_files()) == before
    with sqlite3.connect(store.database_path) as external:
        external.execute('UPDATE transactions SET note=? WHERE id=?', ('Changed externally', ids[0]))
    actual = portable.read_files(store.export_budget_files())
    assert next(row for row in actual['transactions'] if row['id'] == ids[0])['note'] == 'Changed externally'
    assert journal.read(store.export_budget_files()['budget.sqlite'])[1] == []


def test_journal_cannot_leave_a_receipt_pointing_at_deleted_transaction(saved):
    store, _, ids = saved
    raw = store.export_budget_files()['budget.sqlite']
    before = store.export_budget('personal')
    after = deepcopy(before)
    next(row for row in after['transactions'] if row['id'] == ids[0])['deleted'] = True
    entry = append_history(before.get('history'), before, after, {'operation': 'edit_transaction'})['entries'][-1]
    value = journal.append(raw, [entry], 0)
    with pytest.raises(StoreError, match='active transaction'):
        portable.read_files({'budget.sqlite': value})


def fake_snapshot():
    return journal.snapshot(b'SQLite format 3\x00' + b'0' * 100)


@pytest.mark.parametrize('payload', [b'{}', b'[]', b'[NaN]', b'[{"x":1,"x":2}]', b'[', b'\xff', b'[' * 1100])
def test_frames_require_bounded_valid_json_arrays(payload):
    with pytest.raises(StoreError, match='invalid or damaged'):
        journal.read(fake_snapshot() + frame(payload))


@pytest.mark.parametrize('alter', [
    lambda raw: None,
    lambda raw: b'no format',
    lambda raw: journal.MAGIC,
    lambda raw: raw[:len(journal.MAGIC)] + (99).to_bytes(8, 'big') + raw[len(journal.MAGIC) + 8:],
    lambda raw: raw[:len(journal.MAGIC)] + (len(raw) * 2).to_bytes(8, 'big') + raw[len(journal.MAGIC) + 8:],
    lambda raw: raw[:-1] + b'x',
    lambda raw: journal.snapshot(raw),
    lambda raw: raw + b'truncated',
    lambda raw: raw + bytes(40),
    lambda raw: raw + (journal.MAX_JOURNAL_BYTES + 1).to_bytes(8, 'big') + bytes(32),
    lambda raw: raw + frame(b'[1]')[:-1],
    lambda raw: raw + frame(b'[1]')[:-1] + b'2',
])
def test_corrupt_lengths_checksums_truncation_and_nested_envelopes_are_rejected(alter):
    with pytest.raises(StoreError, match='invalid or damaged'):
        journal.read(alter(fake_snapshot()))


def test_frame_count_and_byte_bounds_apply_on_read_and_write(monkeypatch):
    raw = fake_snapshot()
    entry = {'operation': 'edit_transaction', 'changes': [
        {'path': ['transactions', 'fictional'], 'before': {'note': 'old'}, 'after': {'note': 'new'}},
    ]}
    appended = journal.append(raw, [entry], 0)
    assert journal.read(appended)[1] == [entry]
    with monkeypatch.context() as patch:
        patch.setattr(journal, 'MAX_FRAMES', 0)
        assert journal.append(raw, [entry], 0) is None
        with pytest.raises(StoreError):
            journal.read(appended)
    with monkeypatch.context() as patch:
        patch.setattr(journal, 'MAX_JOURNAL_BYTES', 1)
        assert journal.append(raw, [entry], 0) is None
        with pytest.raises(StoreError):
            journal.read(appended)
    with monkeypatch.context() as patch:
        patch.setattr(journal, 'MAX_BYTES', len(raw))
        assert journal.append(raw, [entry], 0) is None
        with pytest.raises(StoreError):
            journal.snapshot(raw)
        with pytest.raises(StoreError):
            journal.read(appended)
    assert journal.append(b'SQLite format 3\x00', [entry], 0) is None
    for entries in ([], [{**entry, 'snapshot': {}}], [{**entry, 'operation': 'add_category'}],
                    [{**entry, 'changes': [{'path': ['categories', 'c'], 'before': {}, 'after': {}}]}],
                    [{**entry, 'changes': [{'path': ['transactions', 't'], 'before': None, 'after': {}}]}],
                    [{**entry, 'changes': [{'path': ['transactions', 't'], 'before': {}, 'after': None}]}]):
        assert journal.append(raw, entries, 0) is None
