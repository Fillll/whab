"""Cross-backend migration and copies retain their negotiated integrity version."""

from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from whab.plugins import StoreError
from whab.plugins.storage_replica import read_replica_watermark, replica_record
from whab.plugins.storage_transfer import evidence_record, read_evidence


@pytest.mark.parametrize('role', ['staged', 'copy'])
def test_merkle_sqlite_source_transfers_to_json_and_reopens_with_exact_evidence(tmp_path, role):
    source = SqliteStore(tmp_path / 'fictional.sqlite', month='2026-10')
    source.add_account('personal', {'name': 'Fictional savings', 'type': 'depository', 'balance': 123})
    destination = JsonStore(tmp_path / 'fictional.json', month='2026-10', initial_role=role)
    before = destination.commit_evidence()
    try:
        snapshot = source.export_local()
        assert snapshot.source.digest_version == 2
        assert destination.preview_local(snapshot).supported
        if role == 'copy':
            watermark = destination.apply_replica(str(uuid4()), snapshot, 'a' * 64, before, namespace_generation=0)
            assert watermark.digest_version == 2
            assert read_replica_watermark(replica_record(watermark)) == watermark
        else:
            destination.import_local('fictional-transfer', snapshot)
        evidence = destination.commit_evidence()
        assert evidence.state_digest == snapshot.source.state_digest and evidence.digest_version == 2
        destination.close()
        destination = JsonStore(tmp_path / 'fictional.json')
        assert destination.commit_evidence() == evidence
        assert destination.export_local().source == evidence
        if role == 'staged':
            destination.set_role('fictional-transfer', evidence, 'primary', authority_epoch=str(uuid4()))
            destination.add_account('personal', {'name': 'Fictional second', 'type': 'depository', 'balance': 0})
            assert destination.commit_evidence().digest_version == 2
    finally:
        source.close()
        destination.close()


def test_evidence_legacy_serialization_is_unchanged_and_unknown_algorithms_fail(tmp_path):
    store = SqliteStore(tmp_path / 'fictional.sqlite', month='2026-10')
    try:
        evidence = store.commit_evidence()
        assert 'digest_version' not in evidence_record(evidence)
        assert read_evidence(evidence_record(evidence)) == evidence
        from whab.plugins.storage_worker_codec import decode, encode
        assert b"digest_version" not in encode(evidence)
        assert decode(encode(evidence)) == evidence
        modern = replace(evidence, digest_version=2)
        assert read_evidence(evidence_record(modern)) == modern
        assert b"digest_version" in encode(modern)
        assert decode(encode(modern)) == modern
        for version in (0, 3, True, '2'):
            with pytest.raises(StoreError):
                read_evidence({**evidence_record(evidence), 'digest_version': version})
    finally:
        store.close()


def test_same_generation_receipt_algorithm_is_bound_even_with_identical_hash_text(tmp_path):
    from whab.plugins.storage_transfer import (
        LocalTransferSnapshot,
        StorageOperation,
        canonical_storage_json,
        check_storage_operation,
    )

    store = SqliteStore(tmp_path / 'fictional-receipt.sqlite', month='2026-10')
    try:
        initial = store.commit_evidence()
        command = StorageOperation('fictional-request', initial.authority_epoch, initial.generation, 'personal', 'add_account',
                                   canonical_storage_json({'name': 'Fictional', 'type': 'depository', 'balance': 0}))
        receipt = store.execute_operation(command).receipt
        mislabeled = replace(receipt, evidence=replace(receipt.evidence, digest_version=1))
        with pytest.raises(StoreError):
            check_storage_operation(store.commit_evidence(), command, mislabeled)
        snapshot = store.export_local()
        with pytest.raises(StoreError):
            LocalTransferSnapshot(snapshot.source, snapshot.financial, snapshot.protected, (mislabeled,))
    finally:
        store.close()


def test_json_reopen_rejects_same_generation_receipt_algorithm_corruption(tmp_path):
    from whab_plugin_json import codec

    from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json

    source = SqliteStore(tmp_path / 'fictional-source.sqlite', month='2026-10')
    destination = JsonStore(tmp_path / 'fictional-target.json', month='2026-10', initial_role='staged')
    try:
        evidence = source.commit_evidence()
        command = StorageOperation('fictional-request', evidence.authority_epoch, evidence.generation, 'personal', 'add_account',
                                   canonical_storage_json({'name': 'Fictional', 'type': 'depository', 'balance': 0}))
        source.execute_operation(command)
        destination.import_local('fictional-transfer', source.export_local())
        raw = codec.parse(destination.path.read_bytes())
        raw['authority']['receipts'][-1]['evidence']['digest_version'] = 1
        with pytest.raises(StoreError):
            codec.decode(canonical_storage_json(raw))
    finally:
        source.close()
        destination.close()


@pytest.mark.parametrize('destination_class', [SqliteStore, JsonStore], ids=['sqlite', 'json'])
@pytest.mark.parametrize('role', ['staged', 'copy'])
def test_legacy_transfer_format_five_keeps_sha_evidence_through_copy_reopen_and_migration(tmp_path, destination_class, role):
    import json

    from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json

    source = JsonStore(tmp_path / 'legacy-transfer.json', month='2026-11')
    target_path = tmp_path / 'destination'
    destination = destination_class(target_path, month='2026-11', initial_role=role)
    try:
        accounts = [source.add_account('personal', {'name': name, 'type': 'depository', 'balance': 10000})
                    for name in ('Fictional cash', 'Fictional savings')]
        ids = [source.add_transaction('personal', {'accountId': account, 'date': '2026-11-01',
                'merchant': 'Fictional transfer', 'amount': amount}) for account, amount in zip(accounts, (100, -100))]
        pairs = [{'outId': ids[0], 'inId': ids[1]}]
        evidence = source.commit_evidence()
        operation = StorageOperation('legacy-pair', evidence.authority_epoch, evidence.generation,
            'personal', 'apply_transfer_matches', canonical_storage_json({
                'pairs': pairs, 'reviewed': False,
                'fingerprint': source.preview_transfer_matches('personal', pairs)['fingerprint'],
            }))
        receipt = source.execute_operation(operation).receipt
        assert json.loads(source.path.read_bytes())['formatVersion'] == 5
        assert receipt.evidence.digest_version == 1
        source.close()
        source = JsonStore(tmp_path / 'legacy-transfer.json')
        assert source.commit_evidence() == receipt.evidence
        snapshot = source.export_local()
        if role == 'copy':
            watermark = destination.apply_replica(str(uuid4()), snapshot, 'a' * 64,
                destination.commit_evidence(), namespace_generation=0)
            assert watermark.digest_version == 1
        else:
            destination.import_local('legacy-copy', snapshot)
        copied = destination.commit_evidence()
        assert copied.digest_version == 1 and copied.state_digest == snapshot.source.state_digest
        def native_version():
            return (destination._authority_metadata()['formatVersion'] if destination_class is SqliteStore
                    else json.loads(destination.path.read_bytes())['formatVersion'])
        assert native_version() == 5
        destination.close()
        destination = destination_class(target_path, read_only=True)
        assert native_version() == 5 and destination.commit_evidence() == copied
        destination.close()
        destination = destination_class(target_path)
        if role == 'copy':
            destination.prepare_promotion('legacy-promote', copied, watermark)
        transition = 'legacy-promote' if role == 'copy' else 'legacy-copy'
        destination.set_role(transition, copied, 'primary', authority_epoch=str(uuid4()))
        assert destination.execute_operation(operation).receipt == receipt
        before = destination.commit_evidence()
        destination.edit_transaction('personal', ids[0], note='First subsequent edit')
        after = destination.commit_evidence()
        assert after.generation == before.generation + 1
        assert after.digest_version == (2 if destination_class is SqliteStore else 1)
        assert native_version() == (6 if destination_class is SqliteStore else 5)
        assert all(row['transferPairValid'] for row in destination.transactions('personal'))
        assert destination.execute_operation(operation).receipt == receipt
        destination.close()
        destination = destination_class(target_path)
        assert destination.commit_evidence() == after
        assert destination.execute_operation(operation).receipt == receipt
    finally:
        source.close()
        destination.close()
