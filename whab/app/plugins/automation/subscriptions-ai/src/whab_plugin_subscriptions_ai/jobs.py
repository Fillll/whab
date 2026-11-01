"""Budget-owned background scans. No request or browser owns the worker lifetime."""

import json
from contextvars import Context
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, RLock, Thread
from time import monotonic
from uuid import UUID

from whab_plugin_subscriptions.api import digest, excluded, fingerprint

from whab.plugins import StoreError

from .engine import Engine, configuration

ACTIVE = {"preparing", "running", "stopping"}
STATUSES = ACTIVE | {"idle", "completed", "stopped", "failed", "interrupted"}
FILE = "classification-job.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except ValueError:
        return False


def idle():
    return {
        "version": 1,
        "id": "",
        "status": "idle",
        "total": 0,
        "processed": 0,
        "classified": 0,
        "reused": 0,
        "skipped": 0,
        "error": "",
        "configHash": "",
        "startedAt": "",
        "finishedAt": "",
    }


class Jobs:
    engine_factory = staticmethod(Engine)

    def __init__(self, context):
        self.context = context
        self.lock = getattr(getattr(context, "service", None), "operation_lock", None) or RLock()
        self.states, self.threads, self.stops = {}, {}, {}
        self.closing = self.closed = False

    def budget(self, workspace):
        base = self.context.budget
        if base is None:
            raise StoreError("Open subscriptions on the local budget host.")
        return (
            replace(
                base,
                read_file=lambda name: base.read_file("demo/" + name),
                write_file=lambda name, raw: base.write_file("demo/" + name, raw),
            )
            if workspace == "demo"
            else base
        )

    def snapshot(self, workspace):
        snapshot = self.context.service.query("portable", workspace)
        data = snapshot["data"]
        accounts = {r["id"]: r for r in data["accounts"]}
        rows = [
            {**r, "currency": accounts[r["accountId"]]["currency"]}
            for r in data["transactions"]
            if not r.get("deleted")
        ]
        return snapshot, rows

    def _save(self, workspace, state):
        self.budget(workspace).write_file(FILE, json.dumps(state, allow_nan=False).encode())
        self.states[workspace] = state
        return dict(state)

    def _get(self, workspace):
        if workspace not in self.states:
            raw = self.budget(workspace).read_file(FILE)
            try:
                state = json.loads(raw) if raw is not None else idle()
                if (
                    type(state) is not dict
                    or set(state) != set(idle())
                    or type(state["version"]) is not int
                    or state["version"] != 1
                    or type(state["status"]) is not str
                    or state["status"] not in STATUSES
                    or (state["id"] != "" and not identifier(state["id"]))
                    or (state["status"] != "idle" and not state["id"])
                    or any(
                        type(state[k]) is not int or state[k] < 0
                        for k in ("total", "processed", "classified", "reused", "skipped")
                    )
                    or state["processed"]
                    != state["classified"] + state["reused"] + state["skipped"]
                    or state["processed"] > state["total"]
                    or any(
                        type(state[k]) is not str or len(state[k]) > 500
                        for k in ("error", "configHash", "startedAt", "finishedAt")
                    )
                ):
                    raise ValueError()
            except ValueError, TypeError, KeyError:
                raise StoreError(
                    "Saved subscription progress is invalid. Preserve this budget before restoring a valid copy."
                ) from None
            if state["status"] in ACTIVE:
                state = {**state, "status": "interrupted", "finishedAt": now()}
                self._save(workspace, state)
            self.states[workspace] = state
        return self.states[workspace]

    def status(self, workspace):
        with self.lock:
            return dict(self._get(workspace))

    def require_idle(self, workspace):
        if self._get(workspace)["status"] in ACTIVE:
            raise StoreError(
                "Classification is running in the background. Stop it before changing its settings or subscription labels."
            )

    def start(self, workspace, operation_id, config=None):
        if not identifier(operation_id):
            raise StoreError("Start classification with a valid operation ID.")
        with self.lock:
            if self.closing:
                raise StoreError(
                    "WHAB is shutting down. Start classification after reopening the budget."
                )
            previous = self._get(workspace)
            if previous["id"] == operation_id:
                if config and digest(config) != previous["configHash"]:
                    raise StoreError(
                        "This classification operation already used different settings. Start a new operation."
                    )
                return dict(previous)
            if previous["status"] in ACTIVE:
                return dict(previous)
            engine = Engine(self.budget(workspace))
            if config:
                engine.configure(config)
            configuration(engine.saved["config"])
            state = {
                **idle(),
                "id": operation_id,
                "status": "preparing",
                "startedAt": now(),
                "configHash": digest(engine.saved["config"]),
            }
            self._save(workspace, state)
            self.stops[workspace] = Event()
            try:
                thread = Thread(
                    target=self._run,
                    args=(workspace,),
                    name="whab-subscriptions-" + workspace,
                    daemon=True,
                    context=Context(),
                )
                self.threads[workspace] = thread
                thread.start()
            except RuntimeError:
                self.threads.pop(workspace, None)
                self._fail(workspace, "Background classification could not start. Try again.")
                raise StoreError("Background classification could not start. Try again.") from None
            return dict(state)

    def stop(self, workspace, job_id):
        with self.lock:
            state = self._get(workspace)
            if not identifier(job_id) or job_id != state["id"]:
                raise StoreError("The classification job changed. Refresh before stopping it.")
            if state["status"] in ACTIVE:
                self.stops[workspace].set()
                return self._save(workspace, {**state, "status": "stopping"})
            return dict(state)

    def _finish(self, workspace, status):
        self._save(workspace, {**self.states[workspace], "status": status, "finishedAt": now()})

    def _fail(self, workspace, message):
        if self.closed:
            return
        state = {
            **self.states[workspace],
            "status": "failed",
            "error": message[:500],
            "finishedAt": now(),
        }
        # A failed progress write must still leave an observable failed job in this
        # process. Restart treats its last durable running receipt as interrupted.
        self.states[workspace] = state
        try:
            self._save(workspace, state)
        except Exception:
            pass

    def _run(self, workspace):
        targets = None
        try:
            while True:
                with self.lock:
                    if self.closed:
                        return
                    if targets is not None and not targets:
                        self._finish(workspace, "completed")
                        return
                    if self.stops[workspace].is_set():
                        self._finish(workspace, "interrupted" if self.closing else "stopped")
                        return
                    engine = Engine(self.budget(workspace))
                    _, rows = self.snapshot(workspace)
                    if targets is None:
                        targets = {
                            r["id"]: fingerprint(r)
                            for r in rows
                            if not excluded(r) and not engine.label(r)
                        }
                        self._save(
                            workspace,
                            {**self.states[workspace], "status": "running", "total": len(targets)},
                        )
                    if not targets:
                        self._finish(workspace, "completed")
                        return
                    # The scan is fixed at Start. Newly imported rows wait for the
                    # next explicit scan; current cached peers may provide evidence.
                    selected = [r for r in rows if r["id"] in targets or engine.label(r)]
                    current = {r["id"]: r for r in rows}
                    changed = {
                        key
                        for key, value in targets.items()
                        if key not in current
                        or excluded(current[key])
                        or fingerprint(current[key]) != value
                    }
                    for key in changed:
                        targets.pop(key)
                    selected = [r for r in selected if r["id"] not in changed]
                # Provider inference never holds the lock used by status/polling.
                engine.classify(selected)
                with self.lock:
                    if self.closed:
                        return
                    latest = Engine(self.budget(workspace))
                    if digest(latest.saved["config"]) != self.states[workspace]["configHash"]:
                        raise StoreError(
                            "Subscription settings changed during classification. Completed batches are saved; start again with the intended settings."
                        )
                    _, rows = self.snapshot(workspace)
                    current = {r["id"]: r for r in rows}
                    classified = reused = 0
                    for key in list(targets):
                        row = current.get(key)
                        if row is None or excluded(row) or fingerprint(row) != targets[key]:
                            targets.pop(key)
                            changed.add(key)
                        elif engine.label(row):
                            latest.saved["labels"][key] = engine.saved["labels"][key]
                            targets.pop(key)
                            if engine.saved["labels"][key]["source"] == "ai":
                                classified += 1
                            else:
                                reused += 1
                    latest.write()
                    state = self.states[workspace]
                    self._save(
                        workspace,
                        {
                            **state,
                            "classified": state["classified"] + classified,
                            "reused": state["reused"] + reused,
                            "skipped": state["skipped"] + len(changed),
                            "processed": state["processed"] + classified + reused + len(changed),
                        },
                    )
        except StoreError as error:
            with self.lock:
                self._fail(workspace, str(error))
        except Exception:
            with self.lock:
                self._fail(
                    workspace,
                    "Subscription classification could not finish. Completed batches are saved; try again.",
                )

    def close(self, timeout=50):
        with self.lock:
            self.closing = True
            for stop in self.stops.values():
                stop.set()
            threads = list(self.threads.values())
        deadline = monotonic() + timeout
        for thread in threads:
            thread.join(max(0, deadline - monotonic()))
        with self.lock:
            for workspace, state in list(self.states.items()):
                if state["status"] in ACTIVE:
                    try:
                        self._finish(workspace, "interrupted")
                    except Exception:
                        self._fail(
                            workspace,
                            "Classification was interrupted while WHAB shut down. Completed batches are saved.",
                        )
            self.closed = True
