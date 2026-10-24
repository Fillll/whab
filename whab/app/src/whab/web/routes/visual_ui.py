"""Local host-owned preview, handover and recovery for visual interfaces."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool

from whab.plugins import PluginError
from whab.plugins.environments import ApprovedWheel
from whab.web.schemas import ApprovedPluginWheel, Body, PluginRecoveryBody


class Preview(Body):
    pluginId: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")] | None = None
    useInstalled: StrictBool = False
    wheels: Annotated[list[ApprovedPluginWheel], Field(max_length=128)] = []


class Commit(Body):
    previewId: Annotated[str, Field(pattern=r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$")]


def create_router(controller):
    router = APIRouter(prefix="/api/ui")

    def personal(request):
        if request.scope.get("whab.workspace") == "demo":
            raise HTTPException(403, "This operation requires your own budget")

    def failure(error):
        return JSONResponse({"error": str(error), "code": error.code, "plugin_id": error.plugin_id}, status_code=409)

    @router.get("/status")
    async def status():
        return controller.status()

    @router.post("/preview")
    async def preview(request: Request, body: Preview):
        personal(request)
        try:
            return await controller.preview(body.pluginId, use_installed=body.useInstalled,
                approvals=tuple(ApprovedWheel(wheel.path, wheel.sha256) for wheel in body.wheels))
        except PluginError as error:
            return failure(error)

    @router.post("/commit", status_code=202)
    async def commit(request: Request, body: Commit):
        personal(request)
        try:
            return {"id": controller.schedule(body.previewId), "accepted": True}
        except PluginError as error:
            return failure(error)

    @router.post("/recover")
    async def recover(request: Request, body: PluginRecoveryBody):
        personal(request)
        try:
            generation = await controller.recover(body.authority)
            return {"generation": generation, "status": controller.status()}
        except PluginError as error:
            return failure(error)

    return router
