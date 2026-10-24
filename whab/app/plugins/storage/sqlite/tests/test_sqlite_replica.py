"""Replica data, receipts and watermarks share one native commit."""

from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_sqlite import Store

from whab.plugins import StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json


def close(backend):
    import sqlite3

    try:
        backend.close()
    except sqlite3.ProgrammingError:
        pass


@pytest.fixture
def pair(tmp_path):
    source = Store(tmp_path / "primary.sqlite", month="2026-10")
    copy = Store(tmp_path / "copy.sqlite", month="2026-10", initial_role="copy")
    yield source, copy
    source.close()
    close(copy)


def save(source, identifier="fictional-command"):
    current = source.commit_evidence()
    command = StorageOperation(identifier, current.authority_epoch, current.generation, "personal", "add_account",
                               canonical_storage_json({"name": "Fictional", "type": "depository", "balance": 123}))
    return command, source.execute_operation(command)


def apply(copy, source, replica=None, *, namespace=0, digest="1" * 64, expected=None):
    return copy.apply_replica(replica or str(uuid4()), source.export_local(), digest,
                              expected or copy.commit_evidence(), namespace_generation=namespace)


def test_replica_reopen_full_state_receipt_replay_and_namespace_only_update(pair):
    source, copy = pair
    command, result = save(source)
    source.save_item("fictional-item", "encrypted-fictional-token", "Fictional Bank")
    before = copy.commit_evidence()
    assert copy.replica_watermark() is None
    assert copy.preview_local(source.export_local()).supported
    with pytest.raises(StoreError):
        copy.import_local("not-a-migration", source.export_local())
    watermark = apply(copy, source)
    evidence = copy.commit_evidence()
    assert evidence.instance_id == before.instance_id and evidence.role == "copy"
    assert evidence.generation == source.commit_evidence().generation
    assert evidence.authority_epoch == source.commit_evidence().authority_epoch
    assert copy.operation_receipt(command.operation_id) == result.receipt
    assert copy.get_item("fictional-item")["token"] == "encrypted-fictional-token"
    actual, expected = copy.export_local(), source.export_local()
    assert (actual.financial, actual.protected, actual.receipts) == (expected.financial, expected.protected, expected.receipts)
    assert apply(copy, source, watermark.replica_id, expected=before) == watermark
    with pytest.raises(StoreError):
        copy.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 1})
    path = copy.database_path
    close(copy)
    reopened = Store(path, initial_role="primary")
    try:
        assert reopened.replica_watermark() == watermark
        assert reopened.commit_evidence() == evidence
        advanced = apply(reopened, source, watermark.replica_id, namespace=1, digest="2" * 64)
        assert advanced.namespace_generation == 1 and advanced.source_generation == watermark.source_generation
        assert reopened.commit_evidence() == evidence
        with pytest.raises(StoreError):
            apply(reopened, source, watermark.replica_id, namespace=0)
        save(source, "next-command")
        newest = apply(reopened, source, watermark.replica_id, namespace=1, digest="2" * 64)
        assert newest.source_generation > watermark.source_generation
        assert len(reopened.accounts("personal")) == 2
    finally:
        close(reopened)


def test_stale_jobs_different_source_epoch_namespace_and_expected_evidence_never_overwrite(pair):
    source, copy = pair
    old_snapshot = source.export_local()
    watermark = apply(copy, source)
    previous = copy.commit_evidence()
    save(source)
    watermark = apply(copy, source, watermark.replica_id, namespace=1, digest="2" * 64)
    evidence = copy.commit_evidence()
    with pytest.raises(StoreError):
        copy.apply_replica(watermark.replica_id, old_snapshot, "2" * 64, evidence, namespace_generation=1)
    with pytest.raises(StoreError):
        apply(copy, source, str(uuid4()), namespace=1, digest="2" * 64)
    with pytest.raises(StoreError):
        apply(copy, source, watermark.replica_id, namespace=1, digest="3" * 64)
    with pytest.raises(StoreError):
        apply(copy, source, watermark.replica_id, namespace=2, digest="3" * 64, expected=previous)
    snapshot = source.export_local()
    changed_epoch = replace(snapshot, source=replace(snapshot.source, authority_epoch=str(uuid4())))
    with pytest.raises(StoreError):
        copy.apply_replica(watermark.replica_id, changed_epoch, "2" * 64, evidence, namespace_generation=1)
    assert copy.replica_watermark() == watermark
    assert copy.commit_evidence() == evidence


def test_promotion_requires_exact_watermark_and_prepared_identity_then_retains_new_primary_writes(pair):
    source, copy = pair
    save(source)
    watermark = apply(copy, source)
    evidence = copy.commit_evidence()
    assert copy.abort_promotion("before-preparation", evidence) == evidence
    with pytest.raises(StoreError):
        copy.set_role("promote", evidence, "primary", authority_epoch=str(uuid4()))
    for expected, supplied in ((None, watermark), (replace(evidence, state_digest="0" * 64), watermark),
                               (evidence, None), (evidence, replace(watermark, namespace_digest="0" * 64))):
        with pytest.raises(StoreError):
            copy.prepare_promotion("promote", expected, supplied)
    assert copy.prepare_promotion("promote", evidence, watermark) == evidence
    assert copy.prepare_promotion("promote", evidence, watermark) == evidence
    with pytest.raises(StoreError):
        copy.prepare_promotion("different", evidence, watermark)
    with pytest.raises(StoreError):
        apply(copy, source, watermark.replica_id)
    with pytest.raises(StoreError):
        copy.abort_promotion("different", evidence)
    with pytest.raises(StoreError):
        copy.abort_promotion("promote", None)
    assert copy.abort_promotion("promote", evidence) == evidence
    assert copy.abort_promotion("promote", evidence) == evidence
    assert apply(copy, source, watermark.replica_id) == watermark
    copy.prepare_promotion("promote-again", evidence, watermark)
    path = copy.database_path
    close(copy)
    reopened = Store(path)
    try:
        assert reopened.prepare_promotion("promote-again", evidence, watermark) == evidence
        epoch = str(uuid4())
        active = reopened.set_role("promote-again", evidence, "primary", authority_epoch=epoch)
        assert reopened.set_role("promote-again", evidence, "primary", authority_epoch=epoch) == active
        assert active.instance_id == evidence.instance_id and active.generation == evidence.generation
        assert active.authority_epoch == epoch and active.role == "primary"
        with pytest.raises(StoreError):
            reopened.abort_promotion("promote-again", active)
        save(reopened, "after-promotion")
        assert len(reopened.accounts("personal")) == 2
        assert reopened.replica_watermark() == watermark
        close(reopened)
        final = Store(path)
        try:
            assert final.commit_evidence().generation == active.generation + 1
            assert len(final.accounts("personal")) == 2
            assert final.replica_watermark() == watermark
        finally:
            final.close()
    finally:
        close(reopened)


def test_primary_and_pristine_unattached_copy_cannot_prepare_promotion(pair):
    source, copy = pair
    for backend in (source, copy):
        with pytest.raises(StoreError):
            backend.prepare_promotion("promote", backend.commit_evidence(), None)
    with pytest.raises(StoreError):
        apply(source, source)


def test_authority_one_upgrade_is_additive_preserves_identity_data_and_receipts(pair):
    from whab.plugins.storage_transfer import read_storage_json

    source, _ = pair
    request, result = save(source)
    source.save_item("fictional-item", "encrypted-fictional-token", "Fictional Bank")
    evidence = source.commit_evidence()
    metadata = read_storage_json(source.db.execute("SELECT record FROM storage_authority").fetchone()[0])
    metadata.update(formatVersion=1)
    metadata.pop("replica")
    metadata.pop("promotionId")
    source.db.execute("UPDATE storage_authority SET record=?", (canonical_storage_json(metadata),))
    path = source.database_path
    close(source)
    reopened = Store(path)
    try:
        assert reopened.commit_evidence() == evidence
        assert reopened.operation_receipt(request.operation_id) == result.receipt
        assert reopened.get_item("fictional-item")["token"] == "encrypted-fictional-token"
        assert reopened.replica_watermark() is None
        metadata = read_storage_json(reopened.db.execute("SELECT record FROM storage_authority").fetchone()[0])
        assert metadata["formatVersion"] == 2
    finally:
        reopened.close()


@pytest.mark.parametrize("committed", [False, True])
def test_replica_lost_commit_response_keeps_data_and_watermark_atomic(pair, committed):
    from whab_plugin_sqlite.authority import CommitUncertain

    source, copy = pair
    save(source)
    snapshot, expected = source.export_local(), copy.commit_evidence()
    replica = str(uuid4())

    class LostReply:
        def __init__(self, connection):
            self.connection = connection
            self.armed = True

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, arguments=()):
            if sql == "COMMIT" and self.armed:
                self.armed = False
                if committed:
                    self.connection.execute(sql, arguments)
                raise OSError("fictional replica commit response loss")
            return self.connection.execute(sql, arguments)

    copy.db = LostReply(copy.db)
    with pytest.raises(CommitUncertain if committed else OSError):
        copy.apply_replica(replica, snapshot, "1" * 64, expected, namespace_generation=0)
    path = copy.database_path
    close(copy)
    reopened = Store(path)
    try:
        assert bool(reopened.accounts("personal")) is committed
        assert (reopened.replica_watermark() is not None) is committed
        watermark = reopened.apply_replica(replica, snapshot, "1" * 64, expected, namespace_generation=0)
        assert reopened.replica_watermark() == watermark
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()


def test_replica_native_write_failure_rolls_back_financial_data_and_watermark(pair):
    import sqlite3

    source, copy = pair
    save(source)
    original = copy.export_local()
    copy.db.execute("CREATE TRIGGER replica_fault BEFORE UPDATE ON storage_authority BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        apply(copy, source)
    assert copy.export_local() == original
    assert copy.replica_watermark() is None
    assert copy.accounts("personal") == []


@pytest.mark.parametrize("corruption", ["same-instance", "epoch", "generation", "operation", "digest", "promotion-id", "promotion-role", "promotion-missing-watermark"])
def test_corrupt_native_copy_evidence_stops_reopen_without_rewriting(pair, corruption):
    from pathlib import Path

    from whab.plugins.storage_transfer import read_storage_json

    source, copy = pair
    save(source)
    apply(copy, source)
    metadata = read_storage_json(copy.db.execute("SELECT record FROM storage_authority").fetchone()[0])
    if corruption == "same-instance":
        metadata["replica"]["source_instance_id"] = metadata["instanceId"]
    elif corruption == "epoch":
        metadata["authorityEpoch"] = str(uuid4())
    elif corruption == "generation":
        metadata["generation"] += 1
    elif corruption == "operation":
        metadata["operationId"] = "other-operation"
    elif corruption == "digest":
        metadata["replica"]["state_digest"] = "0" * 64
    else:
        metadata["promotionId"] = "promotion"
        metadata["transitionId"] = "promotion" if corruption != "promotion-id" else None
        if corruption == "promotion-role":
            metadata["role"] = "primary"
        elif corruption == "promotion-missing-watermark":
            metadata["replica"] = None
    copy.db.execute("UPDATE storage_authority SET record=?", (canonical_storage_json(metadata),))
    path = Path(copy.database_path)
    close(copy)
    raw = path.read_bytes()
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == raw


def test_imported_copy_without_replica_watermark_cannot_activate(pair, tmp_path):
    source, _ = pair
    staged = Store(tmp_path / "staged.sqlite", month="2026-10", initial_role="staged")
    try:
        transferred = staged.import_local("migration", source.export_local())
        copied = staged.set_role("migration", transferred, "copy")
        with pytest.raises(StoreError, match="prepared"):
            staged.set_role("migration", copied, "primary", authority_epoch=str(uuid4()))
    finally:
        staged.close()


@pytest.mark.parametrize("corruption", ["transfer-digest", "older-result"])
def test_copy_transfer_digest_covers_retained_durable_command_results(pair, corruption):
    from pathlib import Path

    from whab.plugins.storage_transfer import read_storage_json

    source, copy = pair
    save(source)
    save(source, "newest-command")
    apply(copy, source)
    if corruption == "transfer-digest":
        metadata = read_storage_json(copy.db.execute("SELECT record FROM storage_authority").fetchone()[0])
        metadata["replica"]["transfer_digest"] = "0" * 64
        copy.db.execute("UPDATE storage_authority SET record=?", (canonical_storage_json(metadata),))
    else:
        metadata = read_storage_json(copy.db.execute("SELECT record FROM storage_operations WHERE operation_id='fictional-command'").fetchone()[0])
        metadata["result"] = '"different-result"'
        copy.db.execute("UPDATE storage_operations SET record=? WHERE operation_id='fictional-command'", (canonical_storage_json(metadata),))
    path = Path(copy.database_path)
    close(copy)
    raw = path.read_bytes()
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("boundary", ["metadata-written", "committed"])
def test_process_termination_during_replica_commit_recovers_one_complete_generation(pair, boundary):
    import signal
    import subprocess
    import sys

    source, copy = pair
    save(source)
    replica = str(uuid4())
    path, primary = copy.database_path, source.database_path
    close(copy)
    script = '''
import os, signal, sys
from whab_plugin_sqlite import Store
source = Store(sys.argv[1])
copy = Store(sys.argv[2])
expected, snapshot = copy.commit_evidence(), source.export_local()
class Terminate:
    def __init__(self, connection):
        self.connection = connection
    def __getattr__(self, name):
        return getattr(self.connection, name)
    def execute(self, statement, arguments=()):
        result = self.connection.execute(statement, arguments)
        if (sys.argv[3] == "metadata-written" and statement.startswith("UPDATE storage_authority")) or (sys.argv[3] == "committed" and statement == "COMMIT"):
            os.kill(os.getpid(), signal.SIGKILL)
        return result
copy.db = Terminate(copy.db)
copy.apply_replica(sys.argv[4], snapshot, "1" * 64, expected, namespace_generation=0)
'''
    result = subprocess.run([sys.executable, "-c", script, primary, path, boundary, replica], capture_output=True)
    assert result.returncode == -signal.SIGKILL
    reopened = Store(path)
    try:
        committed = boundary == "committed"
        assert bool(reopened.accounts("personal")) is committed
        assert (reopened.replica_watermark() is not None) is committed
        watermark = apply(reopened, source, replica)
        assert reopened.replica_watermark() == watermark
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()
