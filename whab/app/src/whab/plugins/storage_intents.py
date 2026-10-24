"""Payload-free host bindings from caller identities to native storage receipts."""

from copy import deepcopy

from whab.domain.errors import StoreError

from .effect_store import EffectStore
from .storage_transfer import (
    CommitEvidence,
    OperationReceipt,
    StorageOperation,
    evidence_record,
    read_evidence,
    storage_identifier,
)

MAX_REQUESTS = 1024


def _hash(value):
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise StoreError("Invalid private storage request binding.")


def _validate_record(row):
    if type(row) is not dict or row.keys() != {"operation_id", "fingerprint", "before", "request"}:
        raise StoreError("Invalid private storage request binding.")
    storage_identifier(row["operation_id"])
    _hash(row["fingerprint"])
    read_evidence(row["before"])
    request = row["request"]
    if request is not None:
        if type(request) is not dict or request.keys() != {"id", "fingerprint", "response_revision"}:
            raise StoreError("Invalid private storage request binding.")
        storage_identifier(request["id"])
        storage_identifier(request["response_revision"])
        _hash(request["fingerprint"])


def _validate(body):
    if (type(body) is not dict or body.keys() != {"format", "pending", "completed"}
            or type(body["format"]) is not int or body["format"] != 1
            or type(body["completed"]) is not list or len(body["completed"]) > MAX_REQUESTS):
        raise StoreError("Invalid private storage request journal.")
    seen, requests = set(), set()
    rows = body["completed"] + ([body["pending"]] if body["pending"] is not None else [])
    for row in rows:
        _validate_record(row)
        if row["operation_id"] in seen or row["request"] is not None and row["request"]["id"] in requests:
            raise StoreError("Conflicting private storage request identity.")
        seen.add(row["operation_id"])
        if row["request"] is not None:
            requests.add(row["request"]["id"])


def validate_receipt(row, receipt, current):
    """Require native evidence for this exact caller/operation binding."""
    before = read_evidence(row["before"])
    if (type(receipt) is not OperationReceipt or type(current) is not CommitEvidence
            or before.role != "primary" or receipt.evidence.role != "primary" or current.role != "primary"
            or receipt.operation_id != row["operation_id"] or receipt.fingerprint != row["fingerprint"]
            or receipt.evidence.budget_id != before.budget_id or receipt.evidence.environment != before.environment
            or receipt.evidence.instance_id != before.instance_id or receipt.evidence.authority_epoch != before.authority_epoch
            or receipt.evidence.generation != before.generation + 1
            or current.budget_id != before.budget_id or current.environment != before.environment
            or current.instance_id != before.instance_id or current.authority_epoch != before.authority_epoch
            or receipt.evidence.generation > current.generation
            or current.generation == receipt.evidence.generation and current != receipt.evidence):
        raise StoreError("Native storage receipt does not match the private request binding.")
    return receipt


class StorageIntents:
    """One native instance, retained across compatible worker code upgrades.

    Only identities, revisions, precondition evidence and hashes are persisted.
    Results and private payloads remain in native atomic operation receipts.
    """

    def __init__(self, root, plugin_id, evidence, *, fault=None):
        self.scope = {"budget_id": evidence.budget_id, "environment": evidence.environment,
                      "plugin_id": plugin_id, "instance_id": evidence.instance_id}
        self.store = EffectStore(root, "storage-intents.json", self.scope,
                                 {"format": 1, "pending": None, "completed": []}, _validate,
                                 limit=2 * 1024 * 1024, fault=fault)

    def snapshot(self):
        body = self.store.snapshot()
        for row in body["completed"] + ([body["pending"]] if body["pending"] else []):
            evidence = read_evidence(row["before"])
            if any(getattr(evidence, key) != self.scope[key] for key in ("budget_id", "environment", "instance_id")):
                raise StoreError("Private storage requests belong to another native instance.")
        return body

    def request(self, request_id, fingerprint):
        for row in self.snapshot()["completed"]:
            if row["request"] is not None and row["request"]["id"] == request_id:
                if row["request"]["fingerprint"] != fingerprint:
                    raise StoreError("This request ID was already used for a different command.")
                return row
        pending = self.snapshot()["pending"]
        if pending is not None:
            if pending["request"] is not None and pending["request"]["id"] == request_id and pending["request"]["fingerprint"] != fingerprint:
                raise StoreError("This request ID was already used for a different command.")
            raise StoreError("Storage request recovery is required before another mutation.")
        return None

    def validate_current(self, current):
        """A reopened native snapshot cannot precede an acknowledged command.

        Completed records already carry a durable minimum generation without
        adding payloads or a second native commit authority to this journal.
        """
        if type(current) is not CommitEvidence or any(getattr(current, key) != self.scope[key]
                for key in ("budget_id", "environment", "instance_id")):
            raise StoreError("Native storage does not match its retained request identity.")
        if any(current.generation <= read_evidence(row["before"]).generation
               for row in self.snapshot()["completed"]):
            raise StoreError("Native storage precedes an acknowledged request. Preserve its retained evidence.")

    def begin(self, operation, before, request=None):
        if type(operation) is not StorageOperation or type(before) is not CommitEvidence:
            raise StoreError("Native storage operations require complete evidence.")
        self.validate_current(before)
        body = self.snapshot()
        if body["pending"] is not None:
            raise StoreError("Storage request recovery is required before another mutation.")
        if any(row["operation_id"] == operation.operation_id for row in body["completed"]):
            raise StoreError("This durable storage operation already has a request binding.")
        row = {"operation_id": operation.operation_id, "fingerprint": operation.fingerprint,
               "before": evidence_record(before), "request": deepcopy(request)}
        body["pending"] = row
        self.store.commit(body)
        return row

    def finish(self, receipt, current):
        body = self.snapshot()
        row = body["pending"]
        if row is None:
            raise StoreError("Storage has no pending request to complete.")
        validate_receipt(row, receipt, current)
        body["completed"].append(row)
        body["completed"] = body["completed"][-MAX_REQUESTS:]
        body["pending"] = None
        self.store.commit(body)

    def not_committed(self, current):
        """Caller has reaped old financial work and reopened the sole writer."""
        body = self.snapshot()
        if body["pending"] is None or current != read_evidence(body["pending"]["before"]):
            raise StoreError("Storage does not prove that the pending operation was uncommitted.")
        body["pending"] = None
        self.store.commit(body)

    def close(self):
        self.store.close()
