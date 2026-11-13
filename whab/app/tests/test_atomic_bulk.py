"""Atomic core/native plans preserve fictional financial data and durable history."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from whab.domain.bulk import bulk_changes, validate_edits
from whab.plugins import LedgerService, RequestConflict, RevisionConflict, StoreError
from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    read_storage_json,
)


@pytest.fixture(params=["sqlite", "json"])
def native(tmp_path, request):
    path = tmp_path / ("fictional.db" if request.param == "sqlite" else "fictional.json")
    factory = SqliteStore if request.param == "sqlite" else JsonStore
    store = factory(path, month="2026-11")
    service = LedgerService(store)
    a = service.add_account(
        "personal", {"name": "旅行 🍏", "type": "depository", "balance": 1000000}
    )
    b = service.add_account(
        "personal", {"name": "Other", "type": "other", "balance": 0, "onBudget": False}
    )
    c = service.add_category("personal", {"name": "食料品", "group": "家族", "target": 1234})
    t = [
        service.add_transaction(
            "personal",
            {
                "accountId": a,
                "date": "2026-11-01",
                "merchant": "Fictional shop",
                "amount": amount,
                "categoryId": c,
                "note": " [YNAB] \n  ",
            },
        )
        for amount in (100, -200, 300)
    ]
    yield store, service, (a, b, c, t), factory, path
    store.close()


def command(service, operation, edits, **kwargs):
    return service.command(
        operation,
        "personal",
        {"edits": edits},
        request_id=kwargs.get("request_id", uuid4().hex),
        expected_revision=kwargs.get("revision", service.revision),
        history={"actor": "Codex", "actorKind": "agent", "batchId": "fictional"},
    )


def test_mixed_one_commit_preview_replay_history_and_undo(native):
    store, service, (a, b, c, t), _, _ = native
    before = deepcopy(store.export_budget("personal"))
    generation = store.commit_evidence().generation
    commits = []
    service._on_commit = lambda: commits.append(True)
    edits = [
        {"transactionId": t[0], "categoryId": None, "categoryAgent": None, "reviewed": False},
        {
            "transactionId": t[1],
            "categoryId": c,
            "categoryAgent": "Codex",
            "appendNote": "Exact  \n",
        },
        {"transactionId": t[2], "note": ""},
    ]
    preview = service.query(
        "preview_bulk_edits",
        "personal",
        parameters={"operation": "edit_transactions", "edits": edits},
    )
    assert (
        store.export_budget("personal") == before
        and store.commit_evidence().generation == generation
        and not commits
    )
    revision, request = service.revision, uuid4().hex
    saved = command(service, "edit_transactions", edits, revision=revision, request_id=request)
    assert saved["data"] == preview["data"] and saved["revision"] != revision
    assert store.commit_evidence().generation == generation + 1 and len(commits) == 1
    after = store.export_budget("personal")
    assert {row["id"]: row for row in after["accounts"]} == {
        row["id"]: row for row in before["accounts"]
    }
    assert (
        after["categories"] == before["categories"]
        and after["assignments"] == before["assignments"]
    )
    assert saved["data"]["changes"][1]["after"]["note"] == " [YNAB] \n  \nExact  \n"
    assert saved["data"]["changes"][1]["after"]["amount"] == -200
    assert (
        command(service, "edit_transactions", edits, revision=revision, request_id=request) == saved
    )
    assert len(commits) == 1 and store.export_budget("personal") == after
    with pytest.raises(RequestConflict):
        command(
            service,
            "edit_transactions",
            [{"transactionId": t[0], "note": "conflict"}],
            revision=revision,
            request_id=request,
        )
    with pytest.raises(RevisionConflict):
        command(service, "edit_transactions", edits, revision=revision)
    entry = store.budget_history("personal")["entries"][0]
    assert (
        entry["operation"] == "edit_transactions"
        and entry["changeCount"] == 3
        and entry["actor"] == "Codex"
        and entry["batchId"] == "fictional"
    )
    for action in ("undo", "redo"):
        plan = service.query(
            "history_preview", "personal", parameters={"action": action, "target": entry["id"]}
        )["data"]
        service.command(
            "history_apply",
            "personal",
            {"action": action, "target": entry["id"], "fingerprint": plan["fingerprint"]},
            request_id=uuid4().hex,
            expected_revision=service.revision,
        )
        actual = store.export_budget("personal")
        expected = before if action == "undo" else after
        assert {r["id"]: r for r in actual["transactions"]} == {
            r["id"]: r for r in expected["transactions"]
        }


def test_whole_batch_refusal_and_account_scope(native):
    store, service, (a, b, c, t), _, _ = native
    before = store.export_budget("personal")
    generation = store.commit_evidence().generation
    invalids = [
        [],
        [{"transactionId": t[0], "note": "valid"}, {"transactionId": "absent", "reviewed": True}],
        [{"transactionId": t[0], "note": "valid"}, {"transactionId": t[0], "note": "duplicate"}],
        [{"transactionId": t[0], "note": "valid"}, {"transactionId": t[1], "categoryId": "absent"}],
        [
            {"transactionId": t[0], "note": "valid"},
            {"transactionId": t[1], "appendNote": "x" * 1000},
        ],
    ]
    for edits in invalids:
        with pytest.raises(StoreError):
            command(service, "edit_transactions", edits)
        assert (
            store.export_budget("personal") == before
            and store.commit_evidence().generation == generation
        )
    with pytest.raises(StoreError, match="Only cash and credit"):
        command(
            service,
            "classify_accounts",
            [{"accountId": b, "type": "credit"}, {"accountId": a, "type": "investment"}],
        )
    assert store.export_budget("personal") == before
    saved = command(
        service,
        "classify_accounts",
        [
            {"accountId": b, "type": "credit"},
            {"accountId": a, "type": "deposit", "subtype": "cd", "onBudget": False},
        ],
    )
    assert all(not row["after"]["onBudget"] for row in saved["data"]["changes"])
    assert store.commit_evidence().generation == generation + 1
    assert store.export_budget("personal")["transactions"] == before["transactions"]
    assert store.budget_history("personal")["entries"][0]["reversible"]
    with pytest.raises(StoreError):
        service.command(
            "edit_transactions",
            "demo",
            {"edits": [{"transactionId": t[0], "note": "wrong workspace"}]},
            request_id="foreign",
            expected_revision=service.revision,
        )


def test_native_receipt_restart_restore_and_external_detection(native):
    store, _, (a, b, c, t), factory, path = native
    before = store.commit_evidence()
    operation = StorageOperation(
        uuid4().hex,
        before.authority_epoch,
        before.generation,
        "personal",
        "edit_transactions",
        canonical_storage_json({"edits": [{"transactionId": t[0], "appendNote": "once"}]}),
    )
    saved = store.execute_operation(operation)
    expected = store.export_budget("personal")
    files = store.export_budget_files("personal")
    store.close()
    reopened = factory(path, month="2026-11")
    try:
        replayed = reopened.execute_operation(operation)
        assert replayed.replayed and replayed.receipt == saved.receipt
        assert reopened.export_budget("personal") == expected
        assert reopened.commit_evidence().generation == before.generation + 1
        empty = factory(
            ":memory:" if factory is SqliteStore else path.with_name("empty.json"), month="2026-11"
        )
        try:
            empty.restore_budget_files("personal", files)
            actual = empty.export_budget("personal")
            assert {r["id"]: r for r in actual["transactions"]} == {
                r["id"]: r for r in expected["transactions"]
            }
            assert actual["history"] == expected["history"]
        finally:
            empty.close()
        assert read_storage_json(saved.receipt.result)["changes"][0]["after"]["note"].endswith(
            "\nonce"
        )
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "operation,edits",
    [
        ("bad", []),
        ("edit_transactions", None),
        ("edit_transactions", [{}]),
        ("edit_transactions", [{"transactionId": True, "note": ""}]),
        ("edit_transactions", [{"transactionId": "\ud800", "note": ""}]),
        ("edit_transactions", [{"transactionId": "x", "reviewed": None}]),
        ("classify_accounts", [{"accountId": "x", "onBudget": None}]),
        ("edit_transactions", [{"transactionId": "x", "categoryId": False}]),
        ("edit_transactions", [{"transactionId": "x", "categoryAgent": "Codex"}]),
        ("edit_transactions", [{"transactionId": "x", "note": None}]),
        ("edit_transactions", [{"transactionId": "x", "note": "", "appendNote": "x"}]),
        ("edit_transactions", [{"transactionId": "x", "appendNote": ""}]),
        ("edit_transactions", [{"transactionId": "x", "note": ""}] * 101),
    ],
)
def test_strict_bulk_shapes(operation, edits):
    with pytest.raises(StoreError):
        validate_edits(operation, edits)


def test_unsupported_and_invalid_preview_fail_before_backend_access():
    service = LedgerService(SimpleNamespace())
    with pytest.raises(StoreError, match="Update the storage"):
        command(service, "edit_transactions", [{"transactionId": "x", "note": ""}])
    for parameters in ({}, {"operation": "edit_transactions", "edits": []}):
        with pytest.raises(StoreError):
            service.query("preview_bulk_edits", "personal", parameters=parameters)
    state = {"accounts": {}, "categories": {}, "transactions": {"x": {"id": "x", "note": None}}}
    with pytest.raises(StoreError, match="memo is invalid"):
        bulk_changes(state, "edit_transactions", [{"transactionId": "x", "appendNote": "x"}])


def test_process_crashes_leave_old_or_complete_batch_and_replay_once(native, tmp_path):
    import json
    import os
    import subprocess
    import sys

    store, _, (_, _, _, transactions), factory, path = native
    # Each crash stage uses a new native file restored through the public contract.
    files = store.export_budget_files("personal")
    for stage in ("before", "after"):
        target = path.with_name(stage + path.suffix)
        reader = factory(target, month="2026-11")
        reader.restore_budget_files("personal", files)
        evidence = reader.commit_evidence()
        edits = [{"transactionId": key, "appendNote": "crash-safe"} for key in transactions]
        operation = StorageOperation(
            uuid4().hex,
            evidence.authority_epoch,
            evidence.generation,
            "personal",
            "edit_transactions",
            canonical_storage_json({"edits": edits}),
        )
        before = reader.export_budget("personal")
        reader.close()
        script = """
import json,sys,os
from whab_plugin_sqlite import Store as SQL
from whab_plugin_json import Store as JSON
from whab.plugins.storage_transfer import StorageOperation,canonical_storage_json
value=json.load(sys.stdin)
store=(SQL if value['backend']=='sqlite' else JSON)(value['path'],month='2026-11')
if value['stage']=='before':
    if value['backend']=='sqlite':
        original=store._save_authority
        def crash(*args,**kwargs):
            original(*args,**kwargs)
            os._exit(31)
        store._save_authority=crash
    else:
        store._commit=lambda *_:os._exit(31)
operation=StorageOperation(value['id'],value['epoch'],value['generation'],'personal','edit_transactions',canonical_storage_json({'edits':value['edits']}))
store.execute_operation(operation)
os._exit(32)
"""
        env = {
            key: os.environ[key]
            for key in ("PATH", "TMPDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        process = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps(
                {
                    "backend": "sqlite" if factory is SqliteStore else "json",
                    "path": str(target),
                    "stage": stage,
                    "id": operation.operation_id,
                    "epoch": evidence.authority_epoch,
                    "generation": evidence.generation,
                    "edits": edits,
                }
            ),
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert process.returncode == (31 if stage == "before" else 32), process.stderr
        reopened = factory(target, month="2026-11")
        try:
            if stage == "before":
                assert reopened.export_budget("personal") == before
                assert reopened.commit_evidence().generation == evidence.generation
            else:
                replay = reopened.execute_operation(operation)
                assert (
                    replay.replayed
                    and reopened.commit_evidence().generation == evidence.generation + 1
                )
                assert all(
                    row["note"].count("crash-safe") == 1
                    for row in reopened.transactions("personal")
                )
        finally:
            reopened.close()
