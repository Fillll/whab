"""Local, budget-scoped subscription operations using sanitized public ledger reads."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from whab.plugins import StoreError

from .engine import Engine
from .jobs import Jobs


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: Literal["personal", "demo"]
    action: Literal["status", "configure", "classify", "override", "start", "job", "stop"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    month: str = Field(pattern=r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$|^2200-(0[1-9]|1[0-2])$")
    config: dict = Field(default_factory=dict)
    transactionId: str = Field(default="", max_length=200)
    fingerprint: str = Field(default="", max_length=64)
    decision: dict = Field(default_factory=dict)
    operationId: str = Field(default="", max_length=36)
    jobId: str = Field(default="", max_length=36)


def router(context):
    jobs = Jobs(context)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await run_in_threadpool(jobs.close)

    routes = APIRouter(lifespan=lifespan)

    @routes.post("/api/plugin-settings/subscriptions")
    def subscriptions(body: Request):
        if body.action == "job":
            return jobs.status(body.workspace)
        if body.action == "start":
            return jobs.start(body.workspace, body.operationId, body.config)
        if body.action == "stop":
            return jobs.stop(body.workspace, body.jobId)
        with jobs.lock:
            engine = Engine(jobs.budget(body.workspace))
            # Public portable snapshots are coherent and omit provider credentials.
            snapshot, rows = jobs.snapshot(body.workspace)
            data = snapshot["data"]
            if body.action != "status":
                jobs.require_idle(body.workspace)
            if body.action == "configure":
                engine.configure(body.config)
            elif body.action == "override":
                engine.override(rows, body.transactionId, body.fingerprint, body.decision)
            elif body.action == "classify":
                progress = engine.classify(rows)
                if (
                    context.service.query("accounts", body.workspace)["revision"]
                    != snapshot["revision"]
                ):
                    raise StoreError(
                        "Transactions changed during classification. Refresh before trying again."
                    )
                engine.write()
                return progress
            return {
                **engine.report(
                    rows, data["accounts"], data["categories"], body.month, body.currency
                ),
                "job": jobs.status(body.workspace),
            }

    return routes
