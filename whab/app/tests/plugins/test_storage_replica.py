"""Public copy validation rejects stale jobs before they reach native writes."""

from dataclasses import replace
from uuid import uuid4

import pytest

from tests.plugins.test_storage_transfer import evidence, snapshot
from whab.domain.errors import StoreError
from whab.plugins import ReplicaWatermark, StorageReplica
from whab.plugins.storage_replica import (
    read_replica_watermark,
    replica_record,
    validate_replica_apply,
)


def first():
    transfer = snapshot()
    empty = replace(evidence(), role="copy")
    replica_id = str(uuid4())
    watermark, replayed = validate_replica_apply(empty, None, replica_id, transfer, "1" * 64, empty,
                                                namespace_generation=2, empty=True)
    assert not replayed
    copied = replace(transfer.source, instance_id=empty.instance_id, role="copy")
    return empty, transfer, copied, watermark


def apply(current, previous, transfer, **changes):
    values = {"replica_id": previous.replica_id, "snapshot": transfer, "namespace_digest": previous.namespace_digest,
              "expected": current, "namespace_generation": previous.namespace_generation, "empty": False}
    values.update(changes)
    return validate_replica_apply(current, previous, **values)


def test_public_records_roundtrip_and_exact_replay_before_stale_expected_evidence():
    old, transfer, current, watermark = first()
    assert read_replica_watermark(replica_record(watermark)) == watermark
    assert apply(current, watermark, transfer, expected=old) == (watermark, True)
    assert StorageReplica is not None


def test_namespace_only_update_advances_independent_counter_and_keeps_financial_generation():
    old, transfer, current, watermark = first()
    updated, replayed = apply(current, watermark, transfer, namespace_generation=3, namespace_digest="2" * 64)
    assert not replayed
    assert updated.source_generation == watermark.source_generation
    assert updated.namespace_generation == 3


@pytest.mark.parametrize("changes", [{"namespace_generation": 1}, {"namespace_digest": "2" * 64},
                                     {"replica_id": str(uuid4())}, {"expected": evidence()},
                                     {"promotion_id": "pending-promotion"}, {"empty": 1}])
def test_rejects_stale_or_mismatched_copy_jobs(changes):
    old, transfer, current, watermark = first()
    with pytest.raises(StoreError):
        apply(current, watermark, transfer, **changes)


@pytest.mark.parametrize("changes", [{"authority_epoch": str(uuid4())}, {"generation": 1},
                                     {"instance_id": str(uuid4())}, {"role": "retired"}])
def test_copy_cannot_change_source_authority_or_apply_nonprimary_snapshot(changes):
    old, transfer, current, watermark = first()
    with pytest.raises(StoreError):
        apply(current, watermark, replace(transfer, source=replace(transfer.source, **changes)))


@pytest.mark.parametrize("changes", [{"authority_epoch": str(uuid4())}, {"generation": 99},
                                     {"operation_id": "wrong"}, {"state_digest": "0" * 64}, {"role": "primary"}])
def test_replay_never_hides_invalid_current_native_copy_data(changes):
    old, transfer, current, watermark = first()
    with pytest.raises(StoreError):
        apply(replace(current, **changes), watermark, transfer)


def test_first_attachment_requires_pristine_copy_and_complete_expected_evidence():
    current = replace(evidence(), role="copy")
    with pytest.raises(StoreError):
        validate_replica_apply(current, None, str(uuid4()), snapshot(), "1" * 64, current,
                               namespace_generation=0, empty=False)
    with pytest.raises(StoreError):
        validate_replica_apply(current, None, str(uuid4()), snapshot(), "1" * 64, replace(current, generation=99),
                               namespace_generation=0, empty=True)


@pytest.mark.parametrize("changes", [{"replica_id": "invalid"}, {"source_generation": True},
                                     {"namespace_generation": -1}, {"namespace_digest": "private-value"},
                                     {"source_operation_id": "\x00"}])
def test_invalid_watermark_fields_are_sanitized(changes):
    _, _, _, watermark = first()
    with pytest.raises(StoreError) as error:
        ReplicaWatermark(**{**replica_record(watermark), **changes})
    assert "private-value" not in str(error.value)
