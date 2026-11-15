"""General transaction changes cross public worker/HTTPS boundaries atomically."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from whab_plugin_server.plugin import plugin as server_plugin

from whab.plugins import (
    LedgerService,
    RequestConflict,
    RevisionConflict,
    StorageContext,
    StoreError,
)
from whab.plugins.remote_transport import RemoteStore
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router


def worker(kind, home, intents):
    return StorageWorkerProxy(StorageWorker(kind), StorageContext(home, "sandbox", {}), intents)


def seed(target):
    from whab_plugin_sqlite import Store

    store = Store(":memory:", month="2026-11")
    account = store.add_account(
        "personal", {"name": "Fictional 貯蓄 🍏", "type": "depository", "balance": 300000}
    )
    category = store.add_category(
        "personal", {"name": "Fictional residence", "group": "House", "target": 0}
    )
    original = store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-02",
            "merchant": "Fictional receipt",
            "amount": -100000,
            "categoryId": category,
            "note": "  [YNAB: cleared]\nImported provenance  ",
        },
        source="ynab",
    )
    store.edit_transaction("personal", original, reviewed=True)
    other = store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-02",
            "merchant": "Fictional separate purchase",
            "amount": 70000,
            "categoryId": category,
        },
    )
    snapshot = store.export_budget("personal")
    next(row for row in snapshot["transactions"] if row["id"] == original)["merchant"] = (
        "  Fictional receipt  "
    )
    snapshot.pop("history", None)
    store.close()
    target.restore_budget("personal", snapshot)
    payload = {
        "edits": [{"transactionId": original, "amount": -80000}],
        "creates": [
            {
                "creationId": str(uuid4()),
                "accountId": account,
                "date": "2026-10-02",
                "merchant": "Fictional lender",
                "amount": -20000,
                "currency": "USD",
                "categoryId": category,
                "note": "Explicit fictional portion",
                "categoryAgent": "Codex",
            }
        ],
        "preserveBalances": True,
    }
    return account, category, original, other, payload


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_worker_split_checkpoint_restart_replay_and_saved_file_restoration(tmp_path, kind):
    home, intents = tmp_path / "fictional", tmp_path / "intents"
    store = worker(kind, home, intents)
    checkpoints = []
    try:
        account, category, original, other, payload = seed(store)
        before = deepcopy(store.export_budget("personal"))

        def checkpoint():
            checkpoints.append(store.export_budget_files())

        service = LedgerService(store, budget_id="fictional", on_commit=checkpoint)
        assert service.query("transaction_change_policy", "personal")["data"] == {
            "version": 1,
            "maxChanges": 100,
            "preserveBalances": True,
        }
        plan = service.query("preview_transaction_changes", "personal", parameters=payload)
        generation = store.commit_evidence().generation
        assert store.export_budget("personal") == before and checkpoints == []
        args = {
            "request_id": str(uuid4()),
            "expected_revision": plan["revision"],
            "history": {"actor": "Codex", "actorKind": "agent", "batchId": "fictional-split"},
        }
        saved = service.command("change_transactions", "personal", payload, **args)
        assert saved["data"] == plan["data"] and len(checkpoints) == 1
        assert store.commit_evidence().generation == generation + 1
        after = store.export_budget("personal")
        prior = {row["id"]: row for row in before["transactions"]}
        current = {row["id"]: row for row in after["transactions"]}
        assert current[other] == prior[other]
        for field in ["date", "accountId", "merchant", "categoryId", "note", "source"]:
            assert current[original][field] == prior[original][field]
        assert (
            current[original]["reviewed"] is False
            and current[original]["original"]["amount"] == -100000
        )
        assert sum(row["amount"] for row in current.values()) == sum(
            row["amount"] for row in prior.values()
        )
        assert after["accounts"] == before["accounts"]
        created = current[payload["creates"][0]["creationId"]]
        assert created["source"] == "manual" and not created["reviewed"]
        assert saved["data"]["createdIds"] == [created["id"]]
        assert service.command("change_transactions", "personal", payload, **args) == saved
        assert len(checkpoints) == 1
        with pytest.raises(RequestConflict):
            service.command(
                "change_transactions", "personal", {**payload, "preserveBalances": False}, **args
            )
        with pytest.raises(RevisionConflict):
            service.command(
                "change_transactions", "personal", payload, **{**args, "request_id": str(uuid4())}
            )
        entry = store.budget_history("personal")["entries"][0]
        assert entry["actor"] == "Codex" and entry["batchId"] == "fictional-split"
        for action, expected in [("undo", before), ("redo", after)]:
            reversal = service.query(
                "history_preview", "personal", parameters={"action": action, "target": entry["id"]}
            )["data"]
            service.command(
                "history_apply",
                "personal",
                {"action": action, "target": entry["id"], "fingerprint": reversal["fingerprint"]},
                request_id=str(uuid4()),
                expected_revision=service.revision,
            )
            actual = store.export_budget("personal")
            assert {r["id"]: r for r in actual["transactions"]} == {
                r["id"]: r for r in expected["transactions"]
            }
            assert actual["accounts"] == expected["accounts"]
        generation = store.commit_evidence().generation
    finally:
        store.close()
    reopened = worker(kind, home, intents)
    restored = worker(kind, tmp_path / "empty", tmp_path / "empty-intents")
    try:
        service = LedgerService(
            reopened,
            budget_id="fictional",
            on_commit=lambda: pytest.fail("Replay must not checkpoint"),
        )
        assert service.command("change_transactions", "personal", payload, **args) == saved
        assert reopened.commit_evidence().generation == generation
        restored.restore_budget_files("personal", checkpoints[0])
        assert restored.transactions("personal") == reopened.transactions("personal")
        assert restored.accounts("personal") == reopened.accounts("personal")
        assert any(
            row["requestId"] == args["request_id"]
            for row in restored.budget_history("personal")["entries"]
        )
    finally:
        reopened.close()
        restored.close()


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_unknown_checkpoint_response_replays_durable_result_after_restart(tmp_path, kind):
    home, intents = tmp_path / "fictional", tmp_path / "intents"
    store = worker(kind, home, intents)
    try:
        *_, payload = seed(store)

        def lost():
            raise OSError("Fictional checkpoint acknowledgement lost")

        service = LedgerService(store, on_commit=lost)
        args = {"request_id": str(uuid4()), "expected_revision": service.revision}
        with pytest.raises(OSError, match="acknowledgement"):
            service.command("change_transactions", "personal", payload, **args)
        generation = store.commit_evidence().generation
    finally:
        store.close()
    reopened = worker(kind, home, intents)
    try:
        saved = LedgerService(
            reopened, on_commit=lambda: pytest.fail("Never save an exact replay")
        ).command("change_transactions", "personal", payload, **args)
        assert saved["data"]["createdIds"] == [payload["creates"][0]["creationId"]]
        assert reopened.commit_evidence().generation == generation
    finally:
        reopened.close()


def test_unsupported_backends_and_invalid_public_shapes_fail_before_mutation():
    service = LedgerService(SimpleNamespace())
    payload = {"edits": [{"transactionId": "fictional", "note": "x"}], "creates": []}
    for query, parameters in [
        ("transaction_change_policy", None),
        ("preview_transaction_changes", payload),
    ]:
        with pytest.raises(StoreError, match="Update the storage"):
            service.query(query, "personal", parameters=parameters)
    with pytest.raises(StoreError, match="Update the storage"):
        service.command(
            "change_transactions",
            "personal",
            payload,
            request_id="fictional",
            expected_revision=service.revision,
        )
    for invalid in [None, {}, {"edits": [], "creates": [], "extra": 1}]:
        with pytest.raises(StoreError, match="require edits"):
            service.query("preview_transaction_changes", "personal", parameters=invalid)


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_remote_atomic_change_preview_policy_and_exact_public_retry(tmp_path, kind):
    store = worker(kind, tmp_path / "fictional", tmp_path / "intents")
    token = "fictional-token-" + "x" * 32
    service = LedgerService(store, budget_id="fictional")
    binding = BoundRemoteInterface(
        server_plugin.interface.adapter,
        SimpleNamespace(config={"SERVER_TOKEN": token}, service=service, budget_id="fictional"),
    )
    app = FastAPI()
    app.include_router(create_remote_router(lambda: binding))
    with TestClient(app, base_url="https://fictional.example") as server:

        def forward(request):
            return server.request(
                request.method, str(request.url), headers=request.headers, content=request.content
            )

        remote = RemoteStore(
            "https://fictional.example", token, "fictional", transport=httpx.MockTransport(forward)
        )
        try:
            *_, payload = seed(store)
            assert remote.supports_atomic_transaction_changes()
            preview = remote.preview_transaction_changes(
                "personal", payload["edits"], payload["creates"], preserve_balances=True
            )
            result = remote.change_transactions(
                "personal", payload["edits"], payload["creates"], preserve_balances=True
            )
            assert result == preview
            headers = {"Authorization": "Bearer " + token}
            empty = {
                "edits": [
                    {
                        "transactionId": payload["edits"][0]["transactionId"],
                        "note": "saved remotely",
                    }
                ],
                "creates": [],
            }
            intent = {
                "operation": "change_transactions",
                "workspace": "personal",
                "payload": empty,
                "request_id": str(uuid4()),
                "expected_revision": service.revision,
            }
            response = server.post(
                "/remote/v1/budgets/fictional/commands", headers=headers, json=intent
            )
            assert response.status_code == 200
            binding.context.service = LedgerService(store, budget_id="fictional")
            replay = server.post(
                "/remote/v1/budgets/fictional/commands", headers=headers, json=intent
            )
            assert replay.status_code == 200 and replay.json() == response.json()
        finally:
            remote.close()
            store.close()


def test_remote_unsupported_policy_is_a_read_only_actionable_failure():
    requests = []

    def old(request):
        requests.append(request.method)
        return httpx.Response(400, json={"detail": "Unknown ledger query."})

    remote = RemoteStore(
        "https://fictional.example", "x" * 40, "fictional", transport=httpx.MockTransport(old)
    )
    try:
        with pytest.raises(StoreError, match="no mutation was submitted"):
            remote.supports_atomic_transaction_changes()
        assert requests == ["GET"]
    finally:
        remote.close()
