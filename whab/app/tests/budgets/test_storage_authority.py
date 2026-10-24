"""Authority survives lost responses and process death at durable boundaries."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from functools import wraps
from hashlib import sha256
from uuid import uuid4

import pytest

from tests.budgets.storage_authority_support import BUDGET_ID, SyntheticStorage
from tests.plugins.test_storage_transfer import states
from whab.budgets.runtime import BudgetRuntime, RuntimeTransitionError
from whab.budgets.storage_authority import (
    AuthorityJournal,
    NamespaceEvidence,
    PreparedStorage,
    StorageAuthorityCoordinator,
    StorageAuthorityError,
    resolve_authority,
)
from whab.plugins.storage_transfer import read_storage_json


def run_async(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


def setup(tmp_path, *, journal_class=AuthorityJournal):
    original = tmp_path / "original.whab"
    original.write_bytes(b"fictional-original-container-bytes")
    opaque = tmp_path / "unavailable-plugin.data"
    opaque.write_bytes(b"fictional-opaque-unknown-plugin-state")
    namespace = NamespaceEvidence(sha256(opaque.read_bytes()).hexdigest(),
                                  sha256(original.read_bytes()).hexdigest(), str(uuid4()))
    source = SyntheticStorage(tmp_path / "source.json", state=states())
    journal = journal_class(tmp_path / "control" / "authority.json", BUDGET_ID, "sandbox")
    runtime = BudgetRuntime(BUDGET_ID, {"storage": source, "retained": object()})
    coordinator = StorageAuthorityCoordinator(runtime, journal, lambda: namespace)
    destination_path = tmp_path / "destination.json"
    def factory():
        return SyntheticStorage(destination_path, role="staged")
    def builder(active, target):
        return {**active.services, "storage": target}
    return source, journal, runtime, coordinator, namespace, factory, builder


@run_async
async def test_version_one_journal_upgrades_explicitly_without_payload_or_authority_change(tmp_path):
    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path)
    await coordinator.migrate("first", "second", factory, factory, builder)
    current = journal.read()
    legacy = {key: value for key, value in current.items() if key not in {"kind", "replica"}}
    legacy["formatVersion"] = 1
    journal.path.write_text(json.dumps(legacy))
    upgraded = journal.read()
    assert upgraded == current
    assert upgraded["formatVersion"] == 2 and upgraded["kind"] == "migration" and upgraded["replica"] is None
    assert journal.path.read_text() == json.dumps(legacy)


@run_async
async def test_handover_waits_for_leases_retains_services_and_preserves_originals(tmp_path):
    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path)
    held, release = asyncio.Event(), asyncio.Event()
    retained = runtime._active.services["retained"]

    async def old_request():
        async with runtime.request() as lease:
            assert lease.service("storage") is source
            held.set()
            await release.wait()
            source.mutate()

    request = asyncio.create_task(old_request())
    await held.wait()
    transition = asyncio.create_task(coordinator.migrate("sqlite", "json", factory, factory, builder))
    await runtime.wait_for_phase("draining")
    assert not transition.done()
    assert source.commit_evidence().role == "primary"
    other = BudgetRuntime("other", {"storage": object()})
    async with other.request():
        assert other.status()["admission"] == "open"
    release.set()
    await request
    assert await transition == 2
    async with runtime.request() as lease:
        target = lease.service("storage")
        assert target.commit_evidence().role == "primary"
        assert target.commit_evidence().generation == 1
        assert lease.service("retained") is retained
        target.mutate()
    record = journal.read()
    assert record["stage"] == "retired"
    assert record["authority_instance_id"] == target.commit_evidence().instance_id
    assert SyntheticStorage(tmp_path / "source.json").commit_evidence().role == "retired"
    assert (tmp_path / "original.whab").read_bytes() == b"fictional-original-container-bytes"
    assert (tmp_path / "unavailable-plugin.data").read_bytes() == b"fictional-opaque-unknown-plugin-state"
    raw = journal.path.read_bytes()
    for secret in (b"Kept note", b"encrypted-fixture", b"checkpoint-fixture", b"fictional-opaque"):
        assert secret not in raw
    assert os.stat(journal.path).st_mode & 0o777 == 0o600
    assert coordinator.status()["cleanup_errors"] == []


@run_async
async def test_failed_prepare_does_not_write_journal_or_touch_source(tmp_path):
    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path)
    before = source.path.read_bytes()

    def occupied():
        return SyntheticStorage(tmp_path / "occupied.json", role="staged", state=states())

    with pytest.raises(RuntimeTransitionError, match="prepared"):
        await coordinator.migrate("sqlite", "json", occupied, occupied, builder)
    assert source.path.read_bytes() == before
    assert journal.read() is None
    assert runtime.status()["admission"] == "open"


@run_async
async def test_lost_committed_journal_response_recovers_destination_without_reverting(tmp_path):
    class LostResponseJournal(AuthorityJournal):
        fired = False
        def write(self, record):
            super().write(record)
            if record["stage"] == "committed" and not self.fired:
                self.fired = True
                raise OSError("synthetic private failure text")

    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path, journal_class=LostResponseJournal)
    with pytest.raises(RuntimeTransitionError, match="could not be confirmed"):
        await coordinator.migrate("sqlite", "json", factory, factory, builder)
    assert runtime.status()["admission"] == "gated"
    assert journal.read()["stage"] == "committed"
    assert await coordinator.recover() == 2
    async with runtime.request() as lease:
        lease.service("storage").mutate()
    reopened_source, reopened_destination = SyntheticStorage(source.path), factory()
    assert resolve_authority(journal, reopened_source, reopened_destination, namespace) == "destination"
    assert reopened_destination.commit_evidence().generation == 1
    assert source.closed is True
    assert reopened_source.commit_evidence().role == "retired"


@run_async
async def test_failed_before_decision_resumes_source_and_allows_new_transition(tmp_path):
    class FailedDecisionJournal(AuthorityJournal):
        fired = False
        def write(self, record):
            if record["stage"] == "committed" and not self.fired:
                self.fired = True
                raise OSError("synthetic failed write before replace")
            super().write(record)

    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path, journal_class=FailedDecisionJournal)
    with pytest.raises(RuntimeTransitionError):
        await coordinator.migrate("sqlite", "json", factory, factory, builder)
    assert source.commit_evidence().role == "fenced"
    assert await coordinator.recover() == 1
    assert source.commit_evidence().role == "primary"
    assert journal.read()["stage"] == "aborted"
    source.mutate()
    def destination2():
        return SyntheticStorage(tmp_path / "destination2.json", role="staged")
    assert await coordinator.migrate("sqlite", "json", destination2, destination2, builder) == 2


@run_async
async def test_cleanup_failure_is_observable_and_does_not_undo_primary_success(tmp_path):
    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path)
    original_set_role = source.set_role
    def set_role(transition_id, expected, role, **kwargs):
        if role == "retired":
            raise OSError("synthetic private path")
        return original_set_role(transition_id, expected, role, **kwargs)
    source.set_role = set_role
    assert await coordinator.migrate("sqlite", "json", factory, factory, builder) == 2
    assert coordinator.status()["cleanup_errors"] == [{"code": "cleanup_failed", "plugin_id": "sqlite"}]
    assert source.commit_evidence().role == "fenced"
    assert runtime.status()["admission"] == "open"


def test_prepared_handle_resolves_captured_methods_after_reopen(tmp_path):
    first = SyntheticStorage(tmp_path / "candidate.json", role="staged")
    handle = PreparedStorage(first)
    captured = handle.commit_evidence
    handle.reopen(lambda: SyntheticStorage(first.path))
    assert captured() == first.evidence
    assert first.closed is True
    handle.close()
    handle.close()
    with pytest.raises(StorageAuthorityError):
        captured()


def test_invalid_journal_and_key_reference_have_no_fallback(tmp_path):
    journal = AuthorityJournal(tmp_path / "authority.json", BUDGET_ID, "sandbox")
    assert journal.read() is None
    journal.path.write_bytes(b'{"formatVersion":99,"private":"synthetic-secret"}')
    with pytest.raises(StorageAuthorityError) as error:
        journal.read()
    assert "synthetic-secret" not in str(error.value)
    with pytest.raises(StorageAuthorityError):
        NamespaceEvidence("0" * 64, None, "secret-encryption-key")


def test_stable_journal_lock_excludes_other_coordinators_and_detects_replaced_lock(tmp_path):
    first = AuthorityJournal(tmp_path / "authority.json", BUDGET_ID, "sandbox")
    second = AuthorityJournal(first.path, BUDGET_ID, "sandbox")
    descriptor = first.acquire_writer()
    try:
        with pytest.raises(StorageAuthorityError):
            second.acquire_writer()
        lock_path = first.path.with_name(first.path.name + ".lock")
        lock_path.rename(lock_path.with_suffix(".preserved-lock"))
        lock_path.write_bytes(b"replacement")
        with pytest.raises(StorageAuthorityError):
            first._check_writer()
    finally:
        first.release_writer(descriptor)
    second.release_writer(second.acquire_writer())


@run_async
async def test_caller_cancellation_does_not_abandon_handover_or_cleanup(tmp_path):
    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def request():
        async with runtime.background():
            entered.set()
            await release.wait()
            source.mutate()
    old = asyncio.create_task(request())
    await entered.wait()
    caller = asyncio.create_task(coordinator.migrate("sqlite", "json", factory, factory, builder))
    await runtime.wait_for_phase("draining")
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert source.commit_evidence().role == "primary"
    release.set()
    await old
    await asyncio.gather(*coordinator._tasks)
    assert journal.read()["stage"] == "retired"
    assert runtime.status()["admission"] == "open"


@run_async
async def test_drain_deadline_never_cancels_admitted_financial_work(tmp_path):
    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def request():
        async with runtime.background():
            entered.set()
            await release.wait()
            source.mutate()
    old = asyncio.create_task(request())
    await entered.wait()
    with pytest.raises(RuntimeTransitionError, match="Existing work continues"):
        await coordinator.migrate("sqlite", "json", factory, factory, builder, drain_timeout=0)
    assert not old.done()
    assert journal.read() is None
    release.set()
    await old
    assert source.commit_evidence().generation == 1
    assert runtime.status()["admission"] == "open"


@run_async
async def test_recovery_allows_later_plugin_writes_but_preserves_key_and_original_container(tmp_path):
    source, journal, runtime, coordinator, namespace, factory, builder = setup(tmp_path)
    await coordinator.migrate("sqlite", "json", factory, factory, builder)
    source = SyntheticStorage(source.path)
    destination = factory()
    changed_namespace = replace(namespace, digest="2" * 64)
    assert resolve_authority(journal, source, destination, changed_namespace) == "destination"
    with pytest.raises(StorageAuthorityError, match="key-reference"):
        resolve_authority(journal, source, destination, replace(changed_namespace, key_reference=str(uuid4())))
    with pytest.raises(StorageAuthorityError, match="container"):
        resolve_authority(journal, source, destination, replace(changed_namespace, original_container_digest="3" * 64))


_CRASH_PROGRAM = r'''
import asyncio, os, signal, sys
from hashlib import sha256
from pathlib import Path
from tests.budgets.storage_authority_support import BUDGET_ID, SyntheticStorage
from tests.plugins.test_storage_transfer import states
from whab.budgets.runtime import BudgetRuntime
from whab.budgets.storage_authority import AuthorityJournal, NamespaceEvidence, StorageAuthorityCoordinator
path, boundary = Path(sys.argv[1]), sys.argv[2]
def crash(stage):
    if stage == boundary:
        os.kill(os.getpid(), signal.SIGKILL)
class CrashJournal(AuthorityJournal):
    def write(self, record):
        super().write(record)
        crash(record['stage'])
async def main():
    source = SyntheticStorage(path/'source.json', state=states(), crash=crash)
    namespace = NamespaceEvidence('0'*64, '1'*64, '00000000-0000-0000-0000-000000000001')
    journal = CrashJournal(path/'authority.json', BUDGET_ID, 'sandbox')
    runtime = BudgetRuntime(BUDGET_ID, {'storage':source})
    coordinator = StorageAuthorityCoordinator(runtime, journal, lambda:namespace)
    factory = lambda:SyntheticStorage(path/'destination.json', role='staged')
    await coordinator.migrate('sqlite','json',factory,factory,lambda active,target:{'storage':target})
    async with runtime.request() as lease:
        lease.service('storage').mutate()
    crash('destination_write')
asyncio.run(main())
'''


@pytest.mark.parametrize("boundary", ["prepared", "imported", "validated", "fence", "source_fenced",
                                      "committed", "active", "retired", "destination_write"])
def test_process_termination_at_each_durable_authority_boundary(tmp_path, boundary):
    result = subprocess.run([sys.executable, "-c", _CRASH_PROGRAM, str(tmp_path), boundary],
                            capture_output=True, timeout=20, check=False)
    assert result.returncode < 0, result.stderr.decode()
    journal = AuthorityJournal(tmp_path / "authority.json", BUDGET_ID, "sandbox")
    source = SyntheticStorage(tmp_path / "source.json")
    destination = SyntheticStorage(tmp_path / "destination.json")
    namespace = NamespaceEvidence("0" * 64, "1" * 64, "00000000-0000-0000-0000-000000000001")
    expected = "destination" if boundary in {"committed", "active", "retired", "destination_write"} else "source"
    assert resolve_authority(journal, source, destination, namespace) == expected
    if expected == "source":
        assert source.commit_evidence().role == "primary"
        assert destination.commit_evidence().role == "staged"
        assert journal.read()["stage"] == "aborted"
    else:
        assert destination.commit_evidence().role == "primary"
        assert source.commit_evidence().role in {"fenced", "retired"}
        assert destination.commit_evidence().generation == (1 if boundary == "destination_write" else 0)
    # The strict reader still accepts a complete, bounded metadata-only record.
    assert read_storage_json(journal.path.read_bytes())["authority_instance_id"] == (destination if expected == "destination" else source).commit_evidence().instance_id
