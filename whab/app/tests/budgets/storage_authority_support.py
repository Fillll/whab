"""Small durable synthetic adapter for coordinator fault tests, not a plugin."""

import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state
from whab.plugins import CommitEvidence, LocalTransferSnapshot, TransferPreview
from whab.plugins.storage_transfer import (
    canonical_storage_json,
    encode_local_state,
    evidence_record,
    local_state_digest,
    read_evidence,
    read_storage_json,
    require_primary,
    validate_role_transition,
)

BUDGET_ID = "1bbfcfce-42d1-41c3-a844-b58ba691c313"


class SyntheticStorage:
    def __init__(self, path, *, role="primary", state=None, crash=None):
        self.path, self.closed, self.crash = Path(path), False, crash
        self.close_count = 0
        if self.path.exists():
            record = read_storage_json(self.path.read_bytes())
            self.evidence = read_evidence(record["evidence"])
            self.financial = record["financial"].encode()
            self.protected = record["protected"].encode()
            self.transfer_id, self.transfer_digest = record["transfer_id"], record["transfer_digest"]
            self.transition_id, self.populated = record["transition_id"], record["populated"]
        else:
            data = state or {w: initial_state(w, "2026-01") for w in ("personal", "demo")}
            self.financial, self.protected = encode_local_state(data)
            self.evidence = CommitEvidence(BUDGET_ID, "sandbox", str(uuid4()), str(uuid4()), 0,
                                           None, role, local_state_digest(self.financial, self.protected))
            self.transfer_id = self.transfer_digest = self.transition_id = None
            self.populated = state is not None
            self._save()

    def _save(self):
        record = {"evidence": evidence_record(self.evidence), "financial": self.financial.decode(),
                  "protected": self.protected.decode(), "transfer_id": self.transfer_id,
                  "transfer_digest": self.transfer_digest, "transition_id": self.transition_id,
                  "populated": self.populated}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(canonical_storage_json(record))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def commit_evidence(self):
        if self.closed:
            raise StoreError("closed synthetic backend")
        return self.evidence

    def export_local(self):
        return LocalTransferSnapshot(self.commit_evidence(), self.financial, self.protected)

    def preview_local(self, snapshot):
        return TransferPreview(not self.populated, True)

    def import_local(self, transfer_id, snapshot):
        if self.transfer_id == transfer_id and self.transfer_digest == snapshot.digest:
            return self.evidence
        if self.evidence.role != "staged" or self.populated:
            raise StoreError("destination occupied")
        self.financial, self.protected = snapshot.financial, snapshot.protected
        self.evidence = replace(snapshot.source, instance_id=self.evidence.instance_id, role="staged")
        self.transfer_id, self.transfer_digest = transfer_id, snapshot.digest
        self.transition_id, self.populated = transfer_id, True
        self._save()
        return self.evidence

    def set_role(self, transition_id, expected, role, *, authority_epoch=None):
        if self.commit_evidence() != expected:
            raise StoreError("stale expected evidence")
        epoch = validate_role_transition(self.evidence, transition_id, role, authority_epoch, self.transition_id)
        self.evidence = replace(self.evidence, role=role, authority_epoch=epoch)
        self.transition_id = transition_id
        self._save()
        if self.crash and role == "fenced":
            self.crash("fence")
        return self.evidence

    def mutate(self):
        require_primary(self.commit_evidence())
        self.evidence = replace(self.evidence, generation=self.evidence.generation + 1,
                                operation_id=str(uuid4()))
        self._save()

    def close(self):
        self.close_count += 1
        self.closed = True
