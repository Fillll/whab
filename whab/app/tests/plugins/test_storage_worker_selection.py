"""Stopped-host repair never interprets a missing financial receipt as rollback."""

import hashlib
import sys
from dataclasses import replace
from importlib import metadata
from types import SimpleNamespace
from uuid import uuid4

import pytest

from whab.budgets import storage_upgrade
from whab.plugins import PluginError, StorageContext
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json, evidence_record
from whab.plugins.storage_worker import StorageWorker


@pytest.fixture(params=("sqlite", "json"))
def saved(request, tmp_path, monkeypatch):
    kind = request.param
    context = StorageContext(tmp_path, "sandbox", {})
    backend = StorageWorkerProxy(StorageWorker(kind), context, tmp_path / "plugin-workers/storage-intents")
    backend.add_account("personal", {"name": "Fictional saved account", "type": "depository", "balance": 123})
    evidence = backend.commit_evidence()
    operation = StorageOperation(operation_id="pending-unknown", authority_epoch=evidence.authority_epoch,
                                 expected_generation=evidence.generation, workspace="personal", name="add_account",
                                 payload=canonical_storage_json({"name": "Not resent", "type": "depository", "balance": 9}))
    backend.intents.begin(operation, evidence)
    backend.close()
    before = SimpleNamespace(identifier="a" * 64, distribution=f"whab_plugin_{kind}", version="0.0.1")
    replacement = SimpleNamespace(identifier="b" * 64, distribution=f"whab_plugin_{kind}",
                                  version=metadata.version(f"whab_plugin_{kind}"), plugin_id=kind,
                                  python=sys.executable, path=tmp_path / "approved")
    owner = storage_upgrade._journal(tmp_path, "default", "sandbox", kind)
    owner.commit({"format": 1, "kind": "storage-code", "active": storage_upgrade._reference(before),
                  "native": evidence_record(evidence), "pending": None})
    owner.close()
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: replacement)
    return SimpleNamespace(kind=kind, home=tmp_path, context=context, evidence=evidence, replacement=replacement,
                           journal=tmp_path / "plugin-workers/storage" / kind / "storage-upgrade.json")


def protected_bytes(saved):
    return {path.relative_to(saved.home): path.read_bytes() for root in (
        saved.home / "data", saved.home / "plugin-workers/storage-intents"
    ) for path in root.rglob("*") if path.is_file()}


def repair(saved, revision):
    return storage_upgrade.replace_saved_storage_worker(saved.home, saved.kind, (), saved.context, expected_revision=revision)


def revision(saved):
    return storage_upgrade.storage_worker_revision(saved.home, "default", "sandbox", saved.kind)


def test_exact_prior_format1_selection_and_unknown_intent_are_preserved_without_native_write(saved):
    original, before = saved.journal.read_bytes(), protected_bytes(saved)
    expected = revision(saved)
    assert expected == hashlib.sha256(original).hexdigest()
    assert repair(saved, expected) is saved.replacement
    assert protected_bytes(saved) == before
    history = saved.journal.parent / "history" / (expected + ".json")
    assert history.read_bytes() == original
    owner = storage_upgrade._journal(saved.home, "default", "sandbox", saved.kind)
    try:
        body = owner.snapshot()
        assert body["format"] == 2 and body["active"] == storage_upgrade._reference(saved.replacement)
        assert body["repair"]["expected_revision"] == expected
    finally:
        owner.close()
    backend = StorageWorkerProxy(StorageWorker(saved.kind), saved.context, saved.home / "plugin-workers/storage-intents")
    try:
        assert backend.worker_recovery_required()
        assert backend.operation_receipt("pending-unknown") is None
        assert len(backend.accounts("personal")) == 1
    finally:
        backend.close()


@pytest.mark.parametrize("boundary", ("before_replace", "after_directory_fsync"))
def test_lost_selection_publication_retries_only_exact_retained_repair(saved, monkeypatch, boundary):
    expected, before = revision(saved), protected_bytes(saved)
    opened = storage_upgrade._journal
    fired = False

    def journal(*args):
        owner = opened(*args)
        def fault(stage):
            nonlocal fired
            if not fired and stage == boundary:
                fired = True
                raise OSError("fictional private failure")
        owner.fault = fault
        return owner

    with monkeypatch.context() as interrupted:
        interrupted.setattr(storage_upgrade, "_journal", journal)
        with pytest.raises(PluginError) as failure:
            repair(saved, expected)
        assert failure.value.code == "storage_worker_repair_failed" and "fictional private" not in str(failure.value)
    durable = saved.journal.read_bytes()
    assert repair(saved, expected) is saved.replacement
    if boundary == "after_directory_fsync":
        assert saved.journal.read_bytes() == durable
    assert protected_bytes(saved) == before
    repaired = saved.journal.read_bytes()
    assert repair(saved, expected) is saved.replacement
    assert saved.journal.read_bytes() == repaired
    alternate = SimpleNamespace(**(vars(saved.replacement) | {"identifier": "c" * 64}))
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: alternate)
    with pytest.raises(PluginError) as failure:
        repair(saved, expected)
    assert failure.value.code == "storage_worker_repair_conflict"
    assert saved.journal.read_bytes() == repaired and protected_bytes(saved) == before


def test_running_owner_and_stale_revision_fail_before_candidate_executes(saved, monkeypatch):
    expected = revision(saved)
    owner = storage_upgrade._journal(saved.home, "default", "sandbox", saved.kind)
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: pytest.fail("Candidate must not execute"))
    try:
        with pytest.raises(PluginError):
            repair(saved, expected)
        with pytest.raises(PluginError):
            revision(saved)
    finally:
        owner.close()
    with pytest.raises(PluginError) as failure:
        repair(saved, "0" * 64)
    assert failure.value.code == "storage_worker_repair_conflict"


@pytest.mark.parametrize("mismatch", ("generation", "epoch", "identity"))
def test_native_evidence_mismatch_blocks_metadata_repair_without_payload_change(saved, mismatch):
    if mismatch == "identity":
        saved.context = replace(saved.context, expected_instance_id="00000000-0000-0000-0000-000000000000")
    else:
        evidence = replace(saved.evidence, **({"generation": saved.evidence.generation + 1} if mismatch == "generation"
                                             else {"authority_epoch": str(uuid4())}))
        owner = storage_upgrade._journal(saved.home, "default", "sandbox", saved.kind)
        owner.commit({**owner.snapshot(), "native": evidence_record(evidence)})
        owner.close()
    original, before = saved.journal.read_bytes(), protected_bytes(saved)
    with pytest.raises(PluginError):
        repair(saved, revision(saved))
    assert saved.journal.read_bytes() == original and protected_bytes(saved) == before


def test_unknown_newer_selection_format_is_not_reset_or_replaced(saved, monkeypatch):
    original = saved.journal.read_bytes().replace(b'"format":1,"kind":"storage-code"', b'"format":99,"kind":"storage-code"')
    assert original != saved.journal.read_bytes()
    saved.journal.write_bytes(original)
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: pytest.fail("Candidate must not execute"))
    with pytest.raises(PluginError):
        revision(saved)
    with pytest.raises(PluginError):
        repair(saved, hashlib.sha256(original).hexdigest())
    assert saved.journal.read_bytes() == original


def current_descriptor(saved, monkeypatch):
    from whab.plugins import load_plugins
    owner = storage_upgrade._journal(saved.home, "default", "sandbox", saved.kind)
    owner.commit({**owner.snapshot(), "active": storage_upgrade._reference(saved.replacement)})
    owner.close()
    monkeypatch.setattr(storage_upgrade, "_prepared", lambda *_args: saved.replacement)
    return storage_upgrade.storage_worker_descriptor(saved.home, "default", "sandbox", load_plugins((saved.kind,)).storage_plugin(saved.kind))


def test_bootstrap_child_refuses_repair_in_gap_before_controller_owns_code_journal(saved, monkeypatch):
    descriptor = current_descriptor(saved, monkeypatch)
    backend = descriptor.storage(saved.context)
    before, protected = saved.journal.read_bytes(), protected_bytes(saved)
    expected = revision(saved)
    monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: pytest.fail("Native owner must refuse repair before any preparation"))
    try:
        with pytest.raises(PluginError):
            repair(saved, expected)
        assert saved.journal.read_bytes() == before and protected_bytes(saved) == protected
    finally:
        backend.close()


def test_stale_bootstrap_record_refuses_before_any_native_open(saved, monkeypatch):
    descriptor = current_descriptor(saved, monkeypatch)
    owner = storage_upgrade._journal(saved.home, "default", "sandbox", saved.kind)
    owner.commit({**owner.snapshot(), "active": {**storage_upgrade._reference(saved.replacement), "identifier": "c" * 64}})
    owner.close()
    monkeypatch.setattr(storage_upgrade, "_worker", lambda *_args: pytest.fail("Stale code must not open or probe native storage"))
    with pytest.raises(PluginError):
        descriptor.storage(saved.context)


def test_live_factory_borrows_controller_owner_and_retired_owner_cannot_open_new_copy(saved, monkeypatch):
    descriptor = current_descriptor(saved, monkeypatch)
    backend = descriptor.storage(saved.context)
    owner = storage_upgrade._journal(saved.home, "default", "sandbox", saved.kind)
    backend._bind_storage_owner(owner)
    copy_home = saved.home / "fictional-copy"
    try:
        copied = descriptor.storage(StorageContext(copy_home, "sandbox", {}, initial_role="copy"))
        assert copied.commit_evidence().role == "copy"
        copied.close()
        with pytest.raises(PluginError):
            revision(saved)
    finally:
        backend.close()
        owner.close()
    retired_home = saved.home / "refused-retired-copy"
    with pytest.raises(PluginError):
        descriptor.storage(StorageContext(retired_home, "sandbox", {}, initial_role="copy"))
    assert not retired_home.exists()


def test_older_authority_proof_cannot_waive_same_instance_retained_minimum(saved, monkeypatch):
    from whab.budgets import storage_runtime
    monkeypatch.setattr(storage_runtime, "validated_native_binding", lambda *_args, **_kwargs: pytest.fail("Retained same-epoch evidence cannot be waived"))
    for invalid in (replace(saved.evidence, generation=saved.evidence.generation - 1),
                    replace(saved.evidence, state_digest="0" * 64)):
        with pytest.raises(PluginError):
            storage_upgrade._retained_or_authority(saved.home, saved.kind, saved.context, invalid, saved.evidence)


def test_failed_read_only_repair_constructor_retains_code_and_native_owners_until_reaped(saved, monkeypatch):
    expected, before = revision(saved), protected_bytes(saved)
    allocated = []
    original = storage_upgrade._worker

    class FailedReader:
        healthy = False
        cleanup_pending = True
        def __init__(self, prepared, *, on_create):
            allocated.append(self)
            on_create(self)
            raise PluginError("Fictional rejected native hello.", code="storage_worker_protocol")
        def close(self):
            if self.cleanup_pending:
                raise PluginError("Fictional pending reap.", code="storage_cleanup_pending")

    monkeypatch.setattr(storage_upgrade, "_worker", FailedReader)
    try:
        with pytest.raises(PluginError):
            repair(saved, expected)
        assert protected_bytes(saved) == before
        with pytest.raises(PluginError):
            revision(saved)
        with pytest.raises(PluginError):
            repair(saved, expected)
        assert len(allocated) == 1
    finally:
        for reader in allocated:
            reader.cleanup_pending = False
        monkeypatch.setattr(storage_upgrade, "_worker", original)
        repair(saved, expected)
    assert protected_bytes(saved) == before
    assert revision(saved) != expected
