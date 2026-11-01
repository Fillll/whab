"""Local, budget-scoped subscription operations using sanitized public ledger reads."""

from dataclasses import replace
from threading import RLock
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from whab.plugins import StoreError

from .engine import Engine


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: Literal["personal", "demo"]
    action: Literal["status", "configure", "classify", "override"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    month: str = Field(pattern=r"^(19|20|21)\d{2}-(0[1-9]|1[0-2])$|^2200-(0[1-9]|1[0-2])$")
    config: dict = Field(default_factory=dict)
    transactionId: str = Field(default="", max_length=200)
    fingerprint: str = Field(default="", max_length=64)
    decision: dict = Field(default_factory=dict)


def router(context):
    routes, lock = APIRouter(), RLock()

    @routes.post("/api/plugin-settings/subscriptions")
    def subscriptions(body: Request):
        if context.budget is None:
            raise StoreError("Open subscriptions on the local budget host.")
        base = context.budget
        budget = (
            replace(
                base,
                read_file=lambda name: base.read_file("demo/" + name),
                write_file=lambda name, raw: base.write_file("demo/" + name, raw),
            )
            if body.workspace == "demo"
            else base
        )
        with lock:
            engine = Engine(budget)
            # Public portable snapshots are coherent and omit provider credentials.
            snapshot = context.service.query("portable", body.workspace)
            data = snapshot["data"]
            accounts = {r["id"]: r for r in data["accounts"]}
            rows = [
                {**r, "currency": accounts[r["accountId"]]["currency"]}
                for r in data["transactions"]
                if not r.get("deleted")
            ]
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
            return engine.report(
                rows, data["accounts"], data["categories"], body.month, body.currency
            )

    return routes
