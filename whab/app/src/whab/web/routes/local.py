"""Authenticated local CLI control, independent of optional remote interfaces."""

import asyncio
from hmac import compare_digest
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from whab.hosting import current_owner
from whab.plugins import InterfaceContext, PluginError
from whab.plugins.interface_adapter import RemoteLedgerInterface
from whab.web.interface_adapter import BoundRemoteInterface, RemoteCommand, RemoteQuery

PREFIX = "/api/local/v1"


class LocalAccess:
    """Authenticate before budget dispatch can open a saved runtime."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope["path"].startswith(PREFIX + "/")
                or PREFIX + "/" in scope["path"]):
            owner = current_owner()
            headers = dict(scope.get("headers", ()))
            if (owner is None or scope.get("scheme") != "https"
                    or not compare_digest(headers.get(b"authorization", b""),
                                          ("Bearer " + owner.token).encode())):
                await JSONResponse({"error": "Local host authentication required."}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class LeaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pid: int = Field(gt=0)


class SelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["enable", "disable"]
    plugin: str = Field(min_length=1, max_length=100)


class SyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plugin: str = Field(min_length=1, max_length=100)
    due: bool = False


def create_router(app):
    router = APIRouter(prefix=PREFIX)

    def binding():
        owner = current_owner()
        if owner is None:
            raise HTTPException(503, "Local host is unavailable.")
        service = app.state.ledger_service
        return BoundRemoteInterface(RemoteLedgerInterface(), InterfaceContext(service=service,
            config={"SERVER_TOKEN": owner.token}, environment=owner.settings.plaid_env, budget_id=service.budget_id))

    def personal(request):
        if request.scope.get("whab.workspace") == "demo":
            raise HTTPException(403, "Local management requires your own budget.")

    def plugin_projection():
        owner = current_owner()
        return {**app.state.plugin_manager.projection(),
                "host_url": f"https://localhost:{owner.settings.port}"}

    @router.post("/leases")
    def acquire(body: LeaseBody):
        try:
            return {"lease": current_owner().acquire(body.pid)}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @router.delete("/leases/{identifier}")
    def release(identifier: str):
        current_owner().release(identifier)
        return {"ok": True}

    @router.get("/queries/{operation}")
    async def query(operation: str, workspace: Literal["personal", "demo"] = "personal", month: str | None = None):
        async with app.state.budget_runtime.request():
            selected = binding()
            return await run_in_threadpool(selected.query, selected.context.budget_id, operation, workspace, month)

    @router.post("/queries")
    async def feature_query(body: RemoteQuery):
        async with app.state.budget_runtime.request():
            selected = binding()
            return await run_in_threadpool(selected.query, selected.context.budget_id, body.operation,
                                          body.workspace, body.month, body.parameters)

    @router.post("/commands")
    async def command(body: RemoteCommand):
        async with app.state.budget_runtime.request():
            selected = binding()
            return await run_in_threadpool(selected.command, selected.context.budget_id, body)

    @router.get("/plugins")
    async def plugins():
        return await run_in_threadpool(plugin_projection)

    @router.post("/plugins")
    async def selection(request: Request, body: SelectionBody):
        personal(request)
        manager = app.state.plugin_manager
        # Visual choices use the existing durable UI decision/browser drain.
        rows = (await run_in_threadpool(manager.projection))["plugins"]
        row = next((row for row in rows if row["id"] == body.plugin), None)
        if row is None or not row["installed"]:
            raise HTTPException(400, "Choose an installed plugin.")
        from whab.plugins.management import installed_entrypoints
        from whab.plugins.runtime import load_descriptors
        descriptor, = await run_in_threadpool(load_descriptors, (body.plugin,), installed_entrypoints())
        visual = descriptor.ui is not None
        if descriptor._process is not None:
            await run_in_threadpool(descriptor._process.close)
        if visual:
            controller = app.state.visual_controller
            plan = await controller.preview(body.plugin if body.action == "enable" else None, use_installed=True)
            identifier = controller.schedule(plan["preview_id"])
            await asyncio.gather(*tuple(controller.tasks), return_exceptions=True)
            result = controller.operations[identifier]
            if result["state"] != "complete":
                raise HTTPException(409, "The visual change failed. Inspect interface status before retrying.")
        else:
            selected = set(manager.projection()["configured"])
            selected.add(body.plugin) if body.action == "enable" else selected.discard(body.plugin)
            selection = tuple(sorted(selected))
            try:
                await run_in_threadpool(manager.preflight_live, selection)
            except PluginError as error:
                if error.code != "restart_required":
                    raise
                # Persist only a validated startup selection. Never fall back
                # after activation, where a transition may have committed.
                await run_in_threadpool(manager.save, selection)
            else:
                await manager.live.activate(selection)
        return await run_in_threadpool(plugin_projection)

    @router.post("/sync")
    async def sync(request: Request, body: SyncBody):
        personal(request)
        return await app.state.bank_jobs.run(body.plugin, due=body.due)

    @router.get("/jobs")
    def jobs():
        return app.state.bank_jobs.status()

    @router.post("/jobs/run")
    async def run_jobs(request: Request):
        personal(request)
        result = await app.state.bank_jobs.run_due()
        return JSONResponse({"jobs": result}, status_code=200 if all(row["ok"] for row in result.values()) else 503)

    @router.get("/job-targets")
    def targets():
        host = app.state.host
        return {"budgets": sorted(set(host.recent) | set(host.applications))}

    return router
