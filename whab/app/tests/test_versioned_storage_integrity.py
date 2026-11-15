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
        modern = replace(evidence, digest_version=2)
        assert read_evidence(evidence_record(modern)) == modern
        for version in (0, 3, True, '2'):
            with pytest.raises(StoreError):
                read_evidence({**evidence_record(evidence), 'digest_version': version})
    finally:
        store.close()
