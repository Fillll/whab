"""Host-only provider normalization and exact durable native command evidence.

Captured commands are recovery inputs. Only the selected backend's public atomic
operation receipt decides whether a financial/protected write committed.
"""

from dataclasses import dataclass, field

from whab.plugins import CommitEvidence, OperationReceipt, OperationResult, StorageOperation
from whab.plugins.bank_continuations import NativeApplyEvidence
from whab.plugins.effect_store import EffectError
from whab.plugins.storage_transfer import (
    canonical_storage_json,
    evidence_record,
    read_evidence,
    read_storage_json,
    storage_identifier,
)

_COMMANDS = frozenset({'save_item', 'set_item_status', 'disconnect_item', 'apply_sync'})


@dataclass(frozen=True, slots=True)
class CapturedBankOperation:
    budget_id: str
    environment: str
    provider_id: str
    item_id: str
    before: CommitEvidence = field(repr=False)
    operation: StorageOperation = field(repr=False)

    def __post_init__(self):
        for value in (self.budget_id, self.provider_id, self.item_id):
            storage_identifier(value)
        if (type(self.before) is not CommitEvidence or type(self.operation) is not StorageOperation
                or self.before.budget_id != self.budget_id or self.before.environment != self.environment
                or self.before.role != 'primary' or self.operation.workspace != 'personal'
                or self.operation.name not in _COMMANDS or self.operation.authority_epoch != self.before.authority_epoch
                or self.operation.expected_generation != self.before.generation):
            raise EffectError('invalid_effect')

    def encode(self):
        operation = self.operation
        return canonical_storage_json({'formatVersion': 1, 'budget_id': self.budget_id,
            'environment': self.environment, 'provider_id': self.provider_id, 'item_id': self.item_id,
            'before': evidence_record(self.before), 'operation': {
                'operation_id': operation.operation_id, 'authority_epoch': operation.authority_epoch,
                'expected_generation': operation.expected_generation, 'workspace': operation.workspace,
                'name': operation.name, 'payload': read_storage_json(operation.payload)},
            'fingerprint': operation.fingerprint})

    @classmethod
    def read(cls, raw):
        try:
            value = read_storage_json(raw, limit=64 * 1024)
            if (type(value) is not dict or value.keys() != {'formatVersion', 'budget_id', 'environment', 'provider_id', 'item_id', 'before', 'operation', 'fingerprint'}
                    or type(value['formatVersion']) is not int or value['formatVersion'] != 1
                    or type(value['operation']) is not dict or value['operation'].keys() != {
                        'operation_id', 'authority_epoch', 'expected_generation', 'workspace', 'name', 'payload'}):
                raise ValueError()
            operation = StorageOperation(**{**value['operation'], 'payload': canonical_storage_json(value['operation']['payload'])})
            if operation.fingerprint != value['fingerprint']:
                raise ValueError()
            return cls(value['budget_id'], value['environment'], value['provider_id'], value['item_id'],
                       read_evidence(value['before']), operation)
        except Exception:
            raise EffectError('invalid_effect') from None


class BankNativeBroker:
    """Use public scoped storage commands; never clear another recovery barrier."""

    def __init__(self, tracked, bank_store, budget_id, environment, provider_id):
        self.tracked, self.bank_store = tracked, bank_store
        self.budget_id, self.environment, self.provider_id = budget_id, environment, provider_id
        if any(not callable(getattr(tracked, name, None)) for name in ('commit_evidence', 'execute_operation', 'operation_receipt')):
            raise EffectError('effect_unavailable')

    def admit(self):
        """Validate current primary ownership before starting provider HTTP."""
        with self.tracked.lock:
            pending = getattr(self.tracked, 'worker_recovery_required', None)
            if callable(pending) and pending():
                raise EffectError('apply_uncertain')
            current = self.tracked.commit_evidence()
            if (current.budget_id != self.budget_id or current.environment != self.environment
                    or current.role != 'primary'):
                raise EffectError('effect_unavailable')
            return current

    def validate(self, captured):
        if (type(captured) is not CapturedBankOperation or captured.budget_id != self.budget_id
                or captured.environment != self.environment or captured.provider_id != self.provider_id):
            raise EffectError('invalid_effect')
        payload = read_storage_json(captured.operation.payload)
        if payload['itemId'] != self.bank_store.item_identity(captured.item_id):
            raise EffectError('invalid_effect')
        if captured.operation.name == 'save_item' and payload.get('providerId', 'plaid') != self.provider_id:
            raise EffectError('invalid_effect')
        return captured

    def prepare(self, operation_id, item_id, name, payload):
        with self.tracked.lock:
            before = self.tracked.commit_evidence()
            operation = StorageOperation(operation_id, before.authority_epoch, before.generation,
                                         'personal', name, canonical_storage_json(payload))
            return self.validate(CapturedBankOperation(self.budget_id, self.environment, self.provider_id,
                                                       item_id, before, operation))

    def _receipt(self, captured, receipt):
        before = captured.before
        if (type(receipt) is not OperationReceipt or receipt.operation_id != captured.operation.operation_id
                or receipt.fingerprint != captured.operation.fingerprint
                or receipt.evidence.budget_id != self.budget_id or receipt.evidence.environment != self.environment
                or receipt.evidence.instance_id != before.instance_id or receipt.evidence.authority_epoch != before.authority_epoch
                or receipt.evidence.generation != before.generation + 1 or receipt.evidence.role != 'primary'):
            raise EffectError('apply_uncertain')
        return NativeApplyEvidence(receipt.operation_id, receipt.fingerprint, 'committed', receipt.result)

    def lookup(self, captured, *, prove_absence=False):
        self.validate(captured)
        operation = captured.operation
        unknown = NativeApplyEvidence(operation.operation_id, operation.fingerprint, 'unknown')
        with self.tracked.lock:
            try:
                receipt = self.tracked.operation_receipt(operation.operation_id)
                if receipt is not None:
                    return self._receipt(captured, receipt)
                if not prove_absence:
                    return unknown
                pending = getattr(self.tracked, 'worker_recovery_required', None)
                if callable(pending) and pending():
                    return unknown
                # Caller holds the shared admission/mutation boundary. A worker
                # with pending transport work refuses above; a validated reopened
                # backend must still show the COMPLETE original native evidence.
                if self.tracked.commit_evidence() != captured.before:
                    return unknown
                return NativeApplyEvidence(operation.operation_id, operation.fingerprint, 'not_committed')
            except Exception:
                return unknown

    def execute(self, captured):
        self.validate(captured)
        with self.tracked.lock:
            if captured.operation.name != 'save_item' and self.bank_store.get_item(captured.item_id) is None:
                raise EffectError('continuation_missing')
            result = self.tracked.execute_operation(captured.operation)
            if type(result) is not OperationResult:
                raise EffectError('apply_uncertain')
            return self._receipt(captured, result.receipt)
