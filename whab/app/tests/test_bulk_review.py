"""Atomic review intents retain bounded replay and ordinary durability semantics."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace

import pytest
from whab_plugin_sqlite import Store

from tests.budgets.helpers import decode_budget
from whab.budgets.session import BudgetSession, TrackedStorage
from whab.domain.errors import StoreError
from whab.domain.transactions import validate_review_batch
from whab.services.ledger import LedgerService, RequestConflict, RevisionConflict


class ReviewBackend:
    def __init__(self, lock=None):
        self.lock = lock
        self.calls = []
        self.rows = {"one": False, "two": False}

    def review_transactions(self, workspace, transaction_ids, reviewed):
        if self.lock is not None:
            assert self.lock._is_owned()
        self.calls.append((workspace, transaction_ids, reviewed))
        if any(identifier not in self.rows for identifier in transaction_ids):
            raise StoreError("Transaction not found.")
        self.rows.update(dict.fromkeys(transaction_ids, reviewed))
        return {"access_token": "synthetic-backend-value-must-not-be-returned"}


def test_review_batch_contract_preserves_order_and_detaches_mutable_input():
    identifiers = ["two", "one"]
    assert validate_review_batch(identifiers, True) == ("two", "one")
    result = validate_review_batch(identifiers, False)
    identifiers.append("later")
    assert result == ("two", "one")
    assert validate_review_batch(tuple(str(index) for index in range(100)), True) == tuple(
        str(index) for index in range(100)
    )


class StringSubclass(str):
    pass


class ListSubclass(list):
    pass


@pytest.mark.parametrize("identifiers,reviewed", [
    ([], True), (["one"] * 101, True), ("one", True), ({"one"}, True),
    (iter(["one"]), True), (ListSubclass(["one"]), True),
    (["one"], 1), (["one"], None), (["one"], "true"),
    ([""], True), (["x" * 201], True), (["bad\x00id"], True),
    ([1], True), ([True], True), ([{}], True), ([StringSubclass("one")], True),
    (["one", "one"], True),
])
def test_review_batch_rejects_invalid_intents_before_any_storage_call(identifiers, reviewed):
    backend = ReviewBackend()
    service = LedgerService(backend)
    revision = service.revision
    with pytest.raises(StoreError):
        validate_review_batch(identifiers, reviewed)
    with pytest.raises(StoreError):
        service.review_transactions("personal", identifiers, reviewed, request_id="request")
    assert backend.calls == [] and service.revision == revision and not service._receipts


@pytest.mark.parametrize("capability", ["absent", None, False, "unsupported"])
def test_optional_bulk_review_never_splits_into_legacy_edits(capability):
    backend = SimpleNamespace(edit_transaction=lambda *_: pytest.fail("No row fallback after submission"))
    if capability != "absent":
        backend.review_transactions = capability
    service = LedgerService(backend)
    revision = service.revision
    assert service.supports_bulk_review is False
    with pytest.raises(StoreError, match="does not support bulk review"):
        service.review_transactions("personal", ["one"], True)
    assert service.revision == revision and not service._receipts


def test_bulk_command_uses_shared_lock_canonical_payload_and_no_backend_return_data():
    lock = RLock()
    backend = ReviewBackend(lock)
    committed = []
    service = LedgerService(backend, lock=lock, on_commit=lambda: committed.append(True))
    original = service.revision
    result = service.command(
        "review_transactions", "personal", {"transactionIds": ("two", "one"), "reviewed": True},
        request_id="request", expected_revision=original,
    )
    assert service.supports_bulk_review is True
    assert result == {"revision": service.revision, "data": None}
    assert result["revision"] != original
    assert service.command(
        "review_transactions", "personal", {"transactionIds": ["two", "one"], "reviewed": True},
        request_id="request", expected_revision=original,
    ) == result
    assert backend.calls == [("personal", ("two", "one"), True)] and committed == [True]


@pytest.mark.parametrize("payload", [
    {}, {"transactionIds": ["one"]}, {"reviewed": True},
    {"transactionIds": ["one"], "reviewed": True, "note": "unexpected"},
])
def test_bulk_command_rejects_missing_or_extra_fields(payload):
    backend = ReviewBackend()
    service = LedgerService(backend)
    with pytest.raises(StoreError, match="fields"):
        service.command("review_transactions", "personal", payload,
                        request_id="request", expected_revision=service.revision)
    assert backend.calls == []


def test_failed_native_batch_keeps_revision_receipts_and_all_rows_unchanged():
    backend = ReviewBackend()
    service = LedgerService(backend)
    revision = service.revision
    with pytest.raises(StoreError, match="Transaction not found"):
        service.review_transactions("personal", ["one", "missing"], True, request_id="request")
    assert backend.rows == {"one": False, "two": False}
    assert service.revision == revision and not service._receipts


def test_local_exact_retry_survives_unrelated_changes_without_overwriting_newer_intent():
    backend = ReviewBackend()
    service = LedgerService(backend)
    service.review_transactions("personal", ["one"], True, request_id="lost-response")
    first_revision = service.revision
    service.review_transactions("personal", ["one"], False, request_id="newer-intent")
    later_revision = service.revision
    assert later_revision != first_revision
    assert service.review_transactions("personal", ("one",), True, request_id="lost-response") is None
    assert backend.rows["one"] is False and len(backend.calls) == 2
    assert service.revision == later_revision


@pytest.mark.parametrize("workspace,identifiers,reviewed", [
    ("demo", ["one"], True), ("personal", ["two"], True),
    ("personal", ["one"], False), ("personal", ["one", "two"], True),
])
def test_local_request_id_is_bound_to_workspace_targets_and_value(workspace, identifiers, reviewed):
    backend = ReviewBackend()
    service = LedgerService(backend)
    service.review_transactions("personal", ["one"], True, request_id="request")
    with pytest.raises(RequestConflict):
        service.review_transactions(workspace, identifiers, reviewed, request_id="request")
    assert backend.calls == [("personal", ("one",), True)]


def test_request_id_collision_with_other_command_does_not_reapply():
    backend = ReviewBackend()
    backend.add_account = lambda *_: "account"
    service = LedgerService(backend)
    service.command("add_account", "personal", {"name": "Cash", "type": "depository", "balance": 0},
                    request_id="request", expected_revision=service.revision)
    with pytest.raises(RequestConflict):
        service.review_transactions("personal", ["one"], True, request_id="request")
    assert backend.calls == []


def test_local_receipt_binds_target_order_and_does_not_advance_on_rejected_retry():
    backend = ReviewBackend()
    service = LedgerService(backend)
    service.review_transactions("personal", ["one", "two"], True, request_id="request")
    revision = service.revision
    with pytest.raises(RequestConflict):
        service.review_transactions("personal", ["two", "one"], True, request_id="request")
    assert service.revision == revision and len(backend.calls) == 1


@pytest.mark.parametrize("workspace,request_id", [("invalid", []), ("personal", []),
                                                ("personal", ""), ("personal", "bad\x00id")])
def test_local_request_identity_validates_before_receipt_lookup(workspace, request_id):
    service = LedgerService(ReviewBackend())
    with pytest.raises(StoreError):
        service.review_transactions(workspace, ["one"], True, request_id=request_id)


def test_parallel_same_ticket_has_one_mutation_and_one_receipt():
    backend = ReviewBackend()
    service = LedgerService(backend)
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda _: service.review_transactions(
            "personal", ["one", "two"], True, request_id="request",
        ), range(20))) == [None] * 20
    assert len(backend.calls) == len(service._receipts) == 1


def test_receipts_remain_bounded_and_local_eviction_is_an_idempotent_new_set():
    backend = ReviewBackend()
    service = LedgerService(backend)
    initial = service.revision
    for index in range(1025):
        service.review_transactions("personal", ["one"], True, request_id=str(index))
    assert len(service._receipts) == 1024 and "0" not in service._receipts
    with pytest.raises(RevisionConflict):
        service.command("review_transactions", "personal", {"transactionIds": ["one"], "reviewed": True},
                        request_id="0", expected_revision=initial)
    service.review_transactions("personal", ["one"], True, request_id="0")
    assert len(service._receipts) == 1024 and len(backend.calls) == 1026


def test_restart_keeps_explicit_remote_revision_guard_but_allows_local_boolean_retry():
    backend = ReviewBackend()
    first = LedgerService(backend)
    initial = first.revision
    first.review_transactions("personal", ["one"], True, request_id="request")
    restarted = LedgerService(backend)
    with pytest.raises(RevisionConflict):
        restarted.command("review_transactions", "personal", {"transactionIds": ["one"], "reviewed": True},
                          request_id="request", expected_revision=initial)
    restarted.review_transactions("personal", ["one"], True, request_id="request")
    assert backend.rows["one"] is True and len(backend.calls) == 2


@pytest.fixture
def tracked_review(tmp_path):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Review fixture", path=tmp_path / "fixture.whab")
    backend = Store(session.storage_home / "ledger.sqlite", month="2026-10")
    session.bind(backend)
    account = backend.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    identifiers = [backend.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": f"Shop {index}",
        "amount": 100, "categoryId": "groceries", "note": f"Memo {index}",
    }) for index in range(3)]
    session.checkpoint()
    tracked = TrackedStorage(backend, session)
    service = LedgerService(tracked, budget_id=session.id, lock=tracked.lock)
    callbacks = []

    def changed():
        callbacks.append(True)
        service.invalidate_revision()

    tracked.on_mutation = changed
    try:
        yield service, backend, session, identifiers, callbacks
    finally:
        backend.close()


def test_one_tracked_batch_checkpoints_once_and_reopens_all_flags(tracked_review, monkeypatch):
    service, backend, session, identifiers, callbacks = tracked_review
    before = backend.export_budget()
    checkpoint = session.checkpoint
    checkpoints = []

    def save():
        checkpoints.append(True)
        checkpoint()

    monkeypatch.setattr(session, "checkpoint", save)
    assert service.review_transactions("personal", identifiers[:2], True, request_id="request") is None
    assert service.review_transactions("personal", identifiers[:2], True, request_id="request") is None
    assert checkpoints == callbacks == [True] and session.status == "saved"
    expected = deepcopy(before)
    for row in expected["transactions"]:
        if row["id"] in identifiers[:2]:
            row["reviewed"] = True
    assert backend.export_budget() == decode_budget(session.path.read_bytes())[1] == expected


def test_pending_checkpoint_keeps_receipt_and_retry_does_not_repeat_write(tracked_review, monkeypatch):
    service, backend, session, identifiers, callbacks = tracked_review
    previous_file = session.path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(session, "bundle", lambda **_: (_ for _ in ()).throw(OSError("Synthetic export failure")))
        service.review_transactions("personal", identifiers, True, request_id="request")
        service.review_transactions("personal", identifiers, True, request_id="request")
    assert session.status == "pending" and session.path.read_bytes() == previous_file
    assert callbacks == [True] and len(service._receipts) == 1
    assert all(row["reviewed"] for row in backend.transactions("personal"))
    session.checkpoint()
    assert session.status == "saved"
    assert all(row["reviewed"] for row in decode_budget(session.path.read_bytes())[1]["transactions"])


def test_raised_checkpoint_after_commit_is_recovered_by_same_boolean_retry(tracked_review, monkeypatch):
    service, backend, session, identifiers, callbacks = tracked_review
    checkpoint = session.checkpoint
    attempts = []

    def interrupted():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("Synthetic interruption after commit")
        checkpoint()

    monkeypatch.setattr(session, "checkpoint", interrupted)
    with pytest.raises(OSError, match="after commit"):
        service.review_transactions("personal", identifiers, True, request_id="request")
    assert not service._receipts and all(row["reviewed"] for row in backend.transactions("personal"))
    service.review_transactions("personal", identifiers, True, request_id="request")
    assert len(attempts) == len(callbacks) == 2 and len(service._receipts) == 1
    assert all(row["reviewed"] for row in decode_budget(session.path.read_bytes())[1]["transactions"])
