"""Real detached workers, progress persistence, cancellation and failure recovery."""

import json
from copy import deepcopy
from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from whab_plugin_ai_credentials import api as ai
from whab_plugin_subscriptions import jobs as module
from whab_plugin_subscriptions.engine import Engine
from whab_plugin_subscriptions.jobs import FILE, Jobs, idle

from whab.plugins import StoreError


def service(rows):
    class Ledger:
        def __init__(self):
            self.rows = deepcopy(rows)
            self.demo = []

        def query(self, operation, workspace):
            return {
                "revision": "fictional",
                "data": {
                    "accounts": [{"id": "account", "name": "Fictional account", "currency": "USD"}],
                    "categories": [],
                    "transactions": deepcopy(self.demo if workspace == "demo" else self.rows),
                },
            }

    return Ledger()


def response(payload):
    return json.dumps(
        {
            "classifications": [
                {
                    "transactionId": row["transactionId"],
                    "isSubscription": True,
                    "cadence": "monthly",
                    "score": 0.95,
                }
                for row in payload["transactions"]
            ]
        }
    )


def create(context, config, rows):
    engine = Engine(context)
    engine.configure(config)
    ledger = service(rows)
    return Jobs(SimpleNamespace(budget=context, service=ledger)), ledger


def finished(jobs, workspace="personal"):
    jobs.threads[workspace].join(5)
    assert not jobs.threads[workspace].is_alive()
    return jobs.status(workspace)


def test_background_progress_is_nonblocking_idempotent_and_survives_browser_independence(
    context, config, row, monkeypatch
):
    rows = [{**row, "id": str(i), "merchant": f"Fictional {i}"} for i in range(23)]
    jobs, ledger = create(context, config, rows)
    entered, release, read = Event(), Event(), Event()
    calls = []

    def complete(vault, provider, model, payload, *args, **kwargs):
        calls.append(deepcopy(payload))
        entered.set()
        assert release.wait(5)
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    operation = str(uuid4())
    try:
        state = jobs.start("personal", operation)
        assert state["id"] == operation and state["status"] == "preparing"
        assert entered.wait(3)
        seen = []
        reader = Thread(target=lambda: (seen.append(jobs.status("personal")), read.set()))
        reader.start()
        assert read.wait(2), "Status must not wait for the provider request."
        reader.join(2)
        assert seen[0]["total"] == 23 and seen[0]["processed"] == 0
        assert jobs.start("personal", str(uuid4()))["id"] == operation
        assert jobs.start("personal", operation)["id"] == operation
        with pytest.raises(StoreError, match="different settings"):
            jobs.start("personal", operation, {**config, "model": "different"})
        with jobs.lock, pytest.raises(StoreError, match="running in the background"):
            jobs.require_idle("personal")
        # A browser view/HTTP request is no longer needed to advance the next batch.
        release.set()
        state = finished(jobs)
        assert state["status"] == "completed" and state["processed"] == state["total"] == 23
        assert state["classified"] == 23 and [len(c["transactions"]) for c in calls] == [20, 3]
        assert jobs.start("personal", operation) == state
        assert jobs.stop("personal", operation) == state
        assert len(calls) == 2
        other = Jobs(SimpleNamespace(budget=context, service=ledger))
        assert other.status("personal") == state
        other.close()
        jobs.start("personal", str(uuid4()))
        assert finished(jobs)["total"] == 0 and len(calls) == 2
    finally:
        release.set()
        jobs.close()
    with pytest.raises(StoreError, match="shutting down"):
        jobs.start("personal", str(uuid4()))


def test_stop_finishes_current_batch_and_resume_checks_only_remaining_rows(
    context, config, row, monkeypatch
):
    rows = [{**row, "id": str(i), "merchant": f"Fictional {i}"} for i in range(22)]
    jobs, ledger = create(context, config, rows)
    entered, release = Event(), Event()
    calls = []

    def complete(vault, provider, model, payload, *args, **kwargs):
        calls.append(len(payload["transactions"]))
        entered.set()
        assert release.wait(5)
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    try:
        state = jobs.start("personal", str(uuid4()))
        assert entered.wait(3)
        with pytest.raises(StoreError, match="job changed"):
            jobs.stop("personal", str(uuid4()))
        with pytest.raises(StoreError):
            jobs.stop("personal", "bad")
        assert jobs.stop("personal", state["id"])["status"] == "stopping"
        release.set()
        assert finished(jobs)["status"] == "stopped"
        assert jobs.status("personal")["processed"] == 20
        jobs.start("personal", str(uuid4()))
        result = finished(jobs)
        assert result["status"] == "completed" and result["total"] == 2
        assert calls == [20, 2]
    finally:
        release.set()
        jobs.close()


def test_cache_reuse_progress_and_concurrent_material_changes_are_skipped(
    context, config, row, monkeypatch
):
    cached = {**row, "id": "cached", "date": "2026-09-15"}
    repeated = {**row, "id": "repeat"}
    changed = {**row, "id": "changed", "merchant": "Changed plan"}
    deleted = {**row, "id": "deleted", "merchant": "Deleted plan"}
    pending = {**row, "id": "pending", "merchant": "Pending plan"}
    note = {**row, "id": "note", "merchant": "Note plan"}
    jobs, ledger = create(context, config, [cached, repeated, changed, deleted, pending, note])
    engine = Engine(context)
    engine.put(cached, {"isSubscription": False, "cadence": "unknown", "score": 1}, "manual")
    engine.write()

    def complete(vault, provider, model, payload, *args, **kwargs):
        ledger.rows = [
            {**r, "amount": 9999}
            if r["id"] == "changed"
            else {**r, "pending": True}
            if r["id"] == "pending"
            else {**r, "note": "Changed memo"}
            if r["id"] == "note"
            else r
            for r in ledger.rows
            if r["id"] != "deleted"
        ]
        ledger.rows.append({**row, "id": "new-import", "merchant": "New import"})
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    try:
        jobs.start("personal", str(uuid4()))
        state = finished(jobs)
        assert state["status"] == "completed" and state["total"] == state["processed"] == 5
        assert state["classified"] == 1 and state["reused"] == 1 and state["skipped"] == 3
        saved = Engine(context)
        assert saved.label(repeated)["source"] == "manual"
        assert saved.label(note) and saved.label({**row, "id": "new-import"}) is None
        assert saved.label(changed) is None and saved.label(deleted) is None
    finally:
        jobs.close()


def test_provider_failure_preserves_completed_batches_and_sanitizes_unexpected_errors(
    context, config, row, monkeypatch
):
    rows = [{**row, "id": str(i), "merchant": f"Fictional {i}"} for i in range(21)]
    jobs, ledger = create(context, config, rows)
    calls = []

    def complete(vault, provider, model, payload, *args, **kwargs):
        calls.append(len(payload["transactions"]))
        if len(calls) > 1:
            raise StoreError("Fictional provider usage limit")
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    try:
        jobs.start("personal", str(uuid4()))
        state = finished(jobs)
        assert state["status"] == "failed" and state["processed"] == 20
        assert "usage limit" in state["error"]
        assert sum(bool(Engine(context).label(r)) for r in rows) == 20
        monkeypatch.setattr(
            ai, "complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("private exception"))
        )
        jobs.start("personal", str(uuid4()))
        state = finished(jobs)
        assert state["status"] == "failed" and "private exception" not in state["error"]
    finally:
        jobs.close()


def test_configuration_changes_during_inference_fail_without_overwriting_labels(
    context, config, row, monkeypatch
):
    jobs, ledger = create(context, config, [row])

    def complete(vault, provider, model, payload, *args, **kwargs):
        Engine(context).configure({**config, "model": "different-model"})
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    try:
        jobs.start("personal", str(uuid4()))
        assert finished(jobs)["status"] == "failed"
        assert "settings changed" in jobs.status("personal")["error"]
        assert Engine(context).label(row) is None
    finally:
        jobs.close()


@pytest.mark.parametrize("status", ["preparing", "running", "stopping"])
def test_restart_marks_unfinished_job_interrupted_without_automatically_calling_ai(
    context, config, status, monkeypatch
):
    state = {
        **idle(),
        "id": str(uuid4()),
        "status": status,
        "total": 100,
        "processed": 20,
        "classified": 20,
    }
    context.write_file(FILE, json.dumps(state).encode())
    monkeypatch.setattr(
        ai, "complete", lambda *a, **k: pytest.fail("Restart must not infer or automatically retry")
    )
    jobs, ledger = create(context, config, [])
    result = jobs.status("personal")
    assert result["status"] == "interrupted" and result["processed"] == 20 and result["finishedAt"]
    assert json.loads(context.read_file(FILE))["status"] == "interrupted"
    jobs.close()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"bad",
        b"[]",
        b"{}",
        json.dumps({**idle(), "version": True}).encode(),
        json.dumps({**idle(), "status": "bad"}).encode(),
        json.dumps({**idle(), "id": "bad"}).encode(),
        json.dumps({**idle(), "status": "running"}).encode(),
        json.dumps({**idle(), "total": -1}).encode(),
        json.dumps({**idle(), "processed": 1}).encode(),
        json.dumps({**idle(), "total": 0, "processed": 1, "classified": 1}).encode(),
        json.dumps({**idle(), "error": None}).encode(),
    ],
)
def test_corrupt_progress_is_preserved_and_reported(context, raw):
    context.write_file(FILE, raw)
    jobs = Jobs(SimpleNamespace(budget=context))
    with pytest.raises(StoreError, match="progress is invalid"):
        jobs.status("personal")
    assert context.read_file(FILE) == raw


def test_invalid_start_and_missing_budget_are_safe(context, config):
    jobs, ledger = create(context, config, [])
    for value in (None, "bad", 1):
        with pytest.raises(StoreError):
            jobs.start("personal", value)
    assert module.identifier(None) is False and module.identifier("bad") is False
    assert jobs.status("personal")["status"] == "idle"
    jobs.require_idle("personal")
    jobs.close()
    with pytest.raises(StoreError):
        Jobs(SimpleNamespace(budget=None)).status("personal")


def test_worker_start_failure_and_progress_save_failure_remain_observable(
    context, config, row, monkeypatch
):
    jobs, ledger = create(context, config, [row])
    monkeypatch.setattr(
        module.Thread, "start", lambda _: (_ for _ in ()).throw(RuntimeError("private"))
    )
    with pytest.raises(StoreError, match="could not start"):
        jobs.start("personal", str(uuid4()))
    assert jobs.status("personal")["status"] == "failed"
    broken = replace(
        context, write_file=lambda *args: (_ for _ in ()).throw(OSError("private path"))
    )
    jobs.context.budget = broken
    jobs._fail("personal", "Authored safe failure")
    assert jobs.status("personal")["error"] == "Authored safe failure"
    jobs.close()


def test_shutdown_drains_current_batch_without_starting_another(context, config, row, monkeypatch):
    rows = [{**row, "id": str(i), "merchant": f"Fictional {i}"} for i in range(22)]
    jobs, ledger = create(context, config, rows)
    entered, release = Event(), Event()

    def complete(vault, provider, model, payload, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    try:
        jobs.start("personal", str(uuid4()))
        assert entered.wait(3)
        closer = Thread(target=jobs.close)
        closer.start()
        assert jobs.stops["personal"].wait(2)
        release.set()
        closer.join(3)
        assert not closer.is_alive()
        result = finished(jobs)
        assert result["status"] == "interrupted" and result["processed"] == 20
        assert sum(bool(Engine(context).label(r)) for r in rows) == 20
    finally:
        release.set()
        jobs.close()


@pytest.mark.parametrize("provider_failure", [False, True])
def test_late_provider_result_cannot_access_a_retired_budget(
    context, config, row, monkeypatch, provider_failure
):
    jobs, ledger = create(context, config, [row])
    entered, release = Event(), Event()

    def complete(vault, provider, model, payload, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        if provider_failure:
            raise RuntimeError("private late failure")
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    jobs.start("personal", str(uuid4()))
    assert entered.wait(3)
    jobs.close(timeout=0)
    assert jobs.status("personal")["status"] == "interrupted"

    def retired(*args):
        pytest.fail("No budget callback may run after retirement")

    jobs.context.budget = replace(context, read_file=retired, write_file=retired)
    jobs.context.service = SimpleNamespace(query=retired)
    release.set()
    assert finished(jobs)["status"] == "interrupted"
    assert Engine(context).label(row) is None


def test_worker_retired_before_it_is_scheduled_never_opens_budget_state(
    context, config, row, monkeypatch
):
    jobs, ledger = create(context, config, [row])
    admitted = Event()
    original = module.Thread

    def delayed(*, target, args, **kwargs):
        def run():
            assert admitted.wait(3)
            target(*args)

        return original(target=run, **kwargs)

    monkeypatch.setattr(module, "Thread", delayed)
    jobs.start("personal", str(uuid4()))
    jobs.close(timeout=0)
    assert jobs.status("personal")["status"] == "interrupted"

    def retired(*args):
        pytest.fail("Retired queued work must not read a namespace")

    jobs.context.budget = replace(context, read_file=retired, write_file=retired)
    admitted.set()
    assert finished(jobs)["status"] == "interrupted"


def test_shutdown_progress_failure_is_visible_and_does_not_escape_cleanup(
    context, config, row, monkeypatch
):
    jobs, ledger = create(context, config, [row])
    entered, release = Event(), Event()

    def complete(vault, provider, model, payload, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    jobs.start("personal", str(uuid4()))
    assert entered.wait(3)
    jobs.context.budget = replace(
        context, write_file=lambda *args: (_ for _ in ()).throw(OSError("private path"))
    )
    jobs.close(timeout=0)
    assert jobs.status("personal")["status"] == "failed"
    assert "private path" not in jobs.status("personal")["error"]
    release.set()
    finished(jobs)


def test_changes_between_batches_are_skipped_before_another_provider_request(
    context, config, row, monkeypatch
):
    rows = [{**row, "id": str(i), "merchant": f"Fictional {i}"} for i in range(21)]
    jobs, ledger = create(context, config, rows)
    original = ledger.query
    queries = []

    def query(*args):
        queries.append(args)
        if len(queries) == 3:
            ledger.rows[-1]["amount"] += 1
        return original(*args)

    ledger.query = query
    calls = []

    def complete(vault, provider, model, payload, *args, **kwargs):
        calls.append(len(payload["transactions"]))
        return response(payload)

    monkeypatch.setattr(ai, "complete", complete)
    try:
        jobs.start("personal", str(uuid4()))
        state = finished(jobs)
        assert state["status"] == "completed" and state["skipped"] == 1
        assert state["processed"] == 21 and calls == [20]
    finally:
        jobs.close()


def test_demo_and_other_budget_jobs_do_not_share_progress_or_results(
    context, config, row, monkeypatch
):
    jobs, ledger = create(context, config, [row])
    ledger.demo = [{**row, "id": "demo", "merchant": "Fictional demo plan"}]
    demo = jobs.budget("demo")
    vault = ai.Vault(demo)
    vault.write("openai", {"api_key": "fictional-demo"})
    demo_config = {**config, "connectionId": vault.references()["openai"]}
    monkeypatch.setattr(
        ai, "complete", lambda vault, provider, model, payload, *a, **k: response(payload)
    )
    try:
        jobs.start("demo", str(uuid4()), demo_config)
        assert finished(jobs, "demo")["processed"] == 1
        assert jobs.status("personal")["status"] == "idle"
        assert Engine(context).label(row) is None
        assert Engine(demo).label(ledger.demo[0])
        files = {}
        other = replace(
            context,
            budget_id="other",
            read_file=files.get,
            write_file=lambda n, r: files.__setitem__(n, r),
        )
        isolated = Jobs(SimpleNamespace(budget=other, service=ledger))
        assert isolated.status("demo")["status"] == "idle"
        isolated.close()
    finally:
        jobs.close()
