"""Replica data, receipts and watermarks share one native commit."""

from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_json import Store

from whab.plugins import StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json


@pytest.fixture
def pair(tmp_path):
    source = Store(tmp_path / "primary.json", month="2026-10")
    copy = Store(tmp_path / "copy.json", month="2026-10", initial_role="copy")
    yield source, copy
    source.close()
    copy.close()


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
    path = copy.path
    copy.close()
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
        reopened.close()


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
    path = copy.path
    copy.close()
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
        reopened.close()
        final = Store(path)
        try:
            assert final.commit_evidence().generation == active.generation + 1
            assert len(final.accounts("personal")) == 2
            assert final.replica_watermark() == watermark
        finally:
            final.close()
    finally:
        reopened.close()


def test_primary_and_pristine_unattached_copy_cannot_prepare_promotion(pair):
    source, copy = pair
    for backend in (source, copy):
        with pytest.raises(StoreError):
            backend.prepare_promotion("promote", backend.commit_evidence(), None)
    with pytest.raises(StoreError):
        apply(source, source)


def test_schema_two_upgrade_preserves_exact_private_original_and_identity(pair):
    import json

    source, _ = pair
    save(source)
    source.save_item("fictional-item", "encrypted-fictional-token", "Fictional Bank")
    evidence = source.commit_evidence()
    path = source.path
    value = json.loads(path.read_bytes())
    value["formatVersion"] = 2
    value["authority"].pop("replica")
    value["authority"].pop("promotionId")
    source.close()
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    reopened = Store(path)
    try:
        assert reopened.commit_evidence() == evidence
        assert reopened.replica_watermark() is None
        assert reopened.get_item("fictional-item")["token"] == "encrypted-fictional-token"
        assert json.loads(path.read_bytes())["formatVersion"] == 3
        assert path.with_name(path.name + ".schema2-recovery").read_bytes() == raw
    finally:
        reopened.close()


@pytest.mark.parametrize("boundary", ["temporary_flushed", "before_replace", "replaced", "directory_flushed", "published"])
def test_replica_native_commit_fault_preserves_data_and_watermark_together(pair, boundary):
    source, copy = pair
    save(source)
    snapshot, expected = source.export_local(), copy.commit_evidence()
    replica = str(uuid4())

    def fail(name):
        if name == boundary:
            raise OSError("fictional replica commit fault")

    copy._hook = fail
    with pytest.raises(StoreError):
        copy.apply_replica(replica, snapshot, "1" * 64, expected, namespace_generation=0)
    path = copy.path
    copy.close()
    reopened = Store(path)
    try:
        committed = boundary in {"replaced", "directory_flushed", "published"}
        assert bool(reopened.accounts("personal")) is committed
        assert (reopened.replica_watermark() is not None) is committed
        copied = reopened.apply_replica(replica, snapshot, "1" * 64, expected, namespace_generation=0)
        assert reopened.replica_watermark() == copied
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("corruption", ["same-instance", "epoch", "generation", "operation", "digest", "promotion-id", "promotion-role", "promotion-missing-watermark"])
def test_corrupt_native_copy_evidence_stops_reopen_without_rewriting(pair, corruption):
    import json

    source, copy = pair
    save(source)
    apply(copy, source)
    path = copy.path
    value = json.loads(path.read_bytes())
    metadata = value["authority"]
    if corruption == "same-instance":
        metadata["replica"]["source_instance_id"] = metadata["instanceId"]
    elif corruption == "epoch":
        value["authorityEpoch"] = str(uuid4())
    elif corruption == "generation":
        value["generation"] += 1
    elif corruption == "operation":
        value["operationId"] = "other-operation"
    elif corruption == "digest":
        metadata["replica"]["state_digest"] = "0" * 64
    else:
        metadata["promotionId"] = "promotion"
        metadata["transitionId"] = "promotion" if corruption != "promotion-id" else None
        if corruption == "promotion-role":
            metadata["role"] = "primary"
        elif corruption == "promotion-missing-watermark":
            metadata["replica"] = None
    copy.close()
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == raw


def test_replayed_migration_cannot_replace_a_new_primary_change(pair, tmp_path):
    source, _ = pair
    snapshot = source.export_local()
    staged = Store(tmp_path / "staged.json", month="2026-10", initial_role="staged")
    try:
        transferred = staged.import_local("migration", snapshot)
        staged.set_role("migration", transferred, "primary", authority_epoch=str(uuid4()))
        save(staged)
        with pytest.raises(StoreError, match="changed"):
            staged.import_local("migration", snapshot)
    finally:
        staged.close()


@pytest.mark.parametrize("corruption", ["transfer-digest", "older-result"])
def test_copy_transfer_digest_covers_retained_durable_command_results(pair, corruption):
    import json

    source, copy = pair
    save(source)
    save(source, "newest-command")
    apply(copy, source)
    path = copy.path
    value = json.loads(path.read_bytes())
    if corruption == "transfer-digest":
        value["authority"]["replica"]["transfer_digest"] = "0" * 64
    else:
        value["authority"]["receipts"][0]["result"] = '"different-result"'
    copy.close()
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("boundary", ["temporary_flushed", "before_replace", "replaced", "directory_flushed", "published"])
def test_process_termination_during_replica_commit_recovers_one_complete_generation(pair, boundary):
    import signal
    import subprocess
    import sys

    source, copy = pair
    save(source)
    replica = str(uuid4())
    path, primary = copy.path, source.path
    copy.close()
    script = '''
import os, signal, sys
from whab_plugin_json import Store
source = Store(sys.argv[1])
copy = Store(sys.argv[2])
def terminate(name):
    if name == sys.argv[3]:
        os.kill(os.getpid(), signal.SIGKILL)
copy._hook = terminate
copy.apply_replica(sys.argv[4], source.export_local(), "1" * 64, copy.commit_evidence(), namespace_generation=0)
'''
    # Release the source's exclusive lock before opening it in the child.
    source.close()
    result = subprocess.run([sys.executable, "-c", script, str(primary), str(path), boundary, replica], capture_output=True)
    assert result.returncode == -signal.SIGKILL
    reopened = Store(path)
    primary_store = Store(primary)
    try:
        committed = boundary in {"replaced", "directory_flushed", "published"}
        assert bool(reopened.accounts("personal")) is committed
        assert (reopened.replica_watermark() is not None) is committed
        watermark = apply(reopened, primary_store, replica)
        assert reopened.replica_watermark() == watermark
        assert len(reopened.accounts("personal")) == 1
    finally:
        reopened.close()
        primary_store.close()
