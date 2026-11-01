"""Local, budget-scoped subscription operations using sanitized public ledger reads."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from whab.plugins import StoreError

from .api import Subscriptions
from .engine import Engine


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: Literal["personal", "demo"]
    action: Literal["status", "configure", "classify", "override", "start", "job", "stop", "set-mode"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    month: str = Field(pattern=r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$|^2200-(0[1-9]|1[0-2])$")
    config: dict = Field(default_factory=dict)
    transactionId: str = Field(default="", max_length=200)
    fingerprint: str = Field(default="", max_length=64)
    decision: dict = Field(default_factory=dict)
    operationId: str = Field(default="", max_length=36)
    jobId: str = Field(default="", max_length=36)
    mode: Literal["manual", "ai"] = "manual"


def router(context):
    feature = Subscriptions(context)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if feature.ai is not None:
                await run_in_threadpool(feature.ai.close)

    routes = APIRouter(lifespan=lifespan)

    def mode(workspace):
        raw = feature.budget(workspace).read_file("classification-mode.json")
        if raw not in (None, b'"manual"', b'"ai"'):
            raise StoreError("Saved subscription classification mode is invalid.")
        if raw == b'"manual"' or feature.ai is None:
            return "manual"
        saved = Engine(feature.budget(workspace)).saved["config"]
        return "ai" if raw == b'"ai"' or saved["consent"] else "manual"

    def idle():
        return {"status": "idle"}

    @routes.post("/api/plugin-settings/subscriptions")
    def subscriptions(body: Request):
        jobs = feature.ai
        if body.action in {"configure", "classify", "start", "stop"} and jobs is None:
            raise StoreError("Enable AI subscription classification for model-based scans, or label payments manually.")
        if body.action == "job":
            return jobs.status(body.workspace) if jobs is not None else idle()
        if body.action == "start":
            if not body.config and mode(body.workspace) != "ai":
                raise StoreError("Choose AI classification or label payments manually.")
            result = jobs.start(body.workspace, body.operationId, body.config)
            if body.config:
                feature.budget(body.workspace).write_file("classification-mode.json", b'"ai"')
            return result
        if body.action == "stop":
            return jobs.stop(body.workspace, body.jobId)
        with context.service.operation_lock:
            engine = jobs.engine_factory(feature.budget(body.workspace)) if jobs is not None else Engine(feature.budget(body.workspace))
            snapshot, rows = feature.snapshot(body.workspace)
            data = snapshot["data"]
            if body.action != "status" and jobs is not None:
                jobs.require_idle(body.workspace)
            if body.action == "set-mode":
                if body.mode == "ai" and jobs is None:
                    raise StoreError("Enable the AI subscription classification plugin first.")
                context.service.external_mutation(lambda: feature.budget(body.workspace).write_file(
                    "classification-mode.json", ('"' + body.mode + '"').encode()))
            elif body.action == "configure":
                engine.configure(body.config)
                feature.budget(body.workspace).write_file("classification-mode.json", b'"ai"')
            elif body.action == "override":
                # Existing HTTP shape remains supported; explicit labels always
                # carry certainty and do not inherit a model's confidence score.
                if set(body.decision) != {"isSubscription", "cadence", "score"}:
                    raise StoreError("Choose a valid subscription label.")
                context.service.external_mutation(lambda: feature.command(body.workspace, "set_label", {
                    "transactionId": body.transactionId, "fingerprint": body.fingerprint,
                    "isSubscription": body.decision["isSubscription"], "cadence": body.decision["cadence"],
                }))
            elif body.action == "classify":
                if mode(body.workspace) != "ai":
                    raise StoreError("Choose AI classification or label payments manually.")
                progress = engine.classify(rows)
                if context.service.query("accounts", body.workspace)["revision"] != snapshot["revision"]:
                    raise StoreError("Transactions changed during classification. Refresh before trying again.")
                engine.write()
                return progress
            engine = Engine(feature.budget(body.workspace))
            return {
                **engine.report(rows, data["accounts"], data["categories"], body.month, body.currency),
                "aiAvailable": jobs is not None, "classificationMode": mode(body.workspace),
                "job": jobs.status(body.workspace) if jobs is not None else idle(),
            }

    return routes
