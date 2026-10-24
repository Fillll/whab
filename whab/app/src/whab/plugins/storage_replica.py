"""Optional same-device copy controls; replicas never accept user mutations."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from whab.domain.errors import StoreError

from .storage_transfer import CommitEvidence, LocalTransferSnapshot, storage_identifier


def _invalid():
    raise StoreError("Invalid storage replica evidence.")


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        _invalid()


def _hash(value):
    if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        _invalid()


@dataclass(frozen=True, slots=True)
class ReplicaWatermark:
    """Bound to native copy data in the same commit, never just a queued job."""

    replica_id: str
    source_instance_id: str
    source_authority_epoch: str
    source_generation: int
    source_operation_id: str | None
    state_digest: str
    transfer_digest: str
    namespace_generation: int
    namespace_digest: str

    def __post_init__(self):
        for value in (self.replica_id, self.source_instance_id, self.source_authority_epoch):
            _uuid(value)
        for value in (self.source_generation, self.namespace_generation):
            if type(value) is not int or not 0 <= value <= 9007199254740991:
                _invalid()
        if self.source_operation_id is not None:
            storage_identifier(self.source_operation_id)
        for value in (self.state_digest, self.transfer_digest, self.namespace_digest):
            _hash(value)


def replica_record(watermark):
    if type(watermark) is not ReplicaWatermark:
        _invalid()
    return {name: getattr(watermark, name) for name in ReplicaWatermark.__slots__}


def read_replica_watermark(value):
    if type(value) is not dict or value.keys() != set(ReplicaWatermark.__slots__):
        _invalid()
    return ReplicaWatermark(**value)


def validate_replica_apply(current, previous, replica_id, snapshot, namespace_digest, expected, *,
                           namespace_generation, empty, promotion_id=None):
    """Return ``(new_watermark, replayed)`` inside a native atomic boundary.

    Exact durable replay precedes expected-evidence CAS, as a missing response is
    not rollback evidence. Scope, role and current-data coherence always precede
    replay. Only a pristine unattached copy may bind its first source/epoch.
    """
    if type(current) is not CommitEvidence or type(expected) is not CommitEvidence or type(snapshot) is not LocalTransferSnapshot:
        _invalid()
    source = snapshot.source
    if (current.role != "copy" or source.role != "primary" or promotion_id is not None
            or current.budget_id != source.budget_id or current.environment != source.environment
            or current.instance_id == source.instance_id
            or expected.instance_id != current.instance_id
            or expected.budget_id != current.budget_id or expected.environment != current.environment):
        raise StoreError("This copy cannot accept the requested replica update.")
    target = ReplicaWatermark(replica_id, source.instance_id, source.authority_epoch, source.generation,
                              source.operation_id, source.state_digest, snapshot.digest,
                              namespace_generation, namespace_digest)
    if type(empty) is not bool:
        _invalid()
    if previous is None:
        if not empty:
            raise StoreError("A replica attachment requires an untouched copy destination.")
    else:
        if type(previous) is not ReplicaWatermark:
            _invalid()
        if (current.authority_epoch != previous.source_authority_epoch
                or current.generation != previous.source_generation
                or current.operation_id != previous.source_operation_id or current.state_digest != previous.state_digest
                or current.instance_id == previous.source_instance_id):
            raise StoreError("Native copy data does not match its persisted watermark.")
        if (replica_id != previous.replica_id or source.instance_id != previous.source_instance_id
                or source.authority_epoch != previous.source_authority_epoch):
            raise StoreError("This replica belongs to a different source or authority epoch; attach a new copy explicitly.")
        if source.generation < previous.source_generation or namespace_generation < previous.namespace_generation:
            raise StoreError("An older replica job cannot replace newer copy data.")
        if source.generation == previous.source_generation and (
            target.state_digest != previous.state_digest or target.transfer_digest != previous.transfer_digest
        ):
            raise StoreError("Replica data changed without advancing the primary generation.")
        if namespace_generation == previous.namespace_generation and namespace_digest != previous.namespace_digest:
            raise StoreError("Replica namespace changed without advancing its generation.")
        if (source.generation, namespace_generation) == (previous.source_generation, previous.namespace_generation):
            if target != previous:
                raise StoreError("Replica replay does not match its durable watermark.")
            return previous, True
    if current != expected:
        raise StoreError("The replica destination changed before this update.")
    return target, False


class StorageReplica(Protocol):
    """Host-private controls on a fenced, single-source same-device copy."""

    def replica_watermark(self) -> ReplicaWatermark | None: ...

    def apply_replica(self, replica_id: str, snapshot: LocalTransferSnapshot, namespace_digest: str,
                      expected: CommitEvidence, *, namespace_generation: int) -> ReplicaWatermark: ...

    def prepare_promotion(self, transition_id: str, expected: CommitEvidence,
                          watermark: ReplicaWatermark) -> CommitEvidence: ...

    def abort_promotion(self, transition_id: str, expected: CommitEvidence) -> CommitEvidence: ...
