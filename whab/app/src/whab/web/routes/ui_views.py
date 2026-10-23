"""Opaque browser participation; draft form contents never leave the browser."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from whab.plugins.runtime import PluginError


class ViewState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    generation: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dirty: StrictBool
    prepared: Annotated[str, Field(max_length=36)] | None = None


def create_router(resolve):
    """The resolver only reads host coordination state, including while gated."""
    router = APIRouter(prefix="/api/ui/views")

    def views():
        selected = resolve()
        if selected is None:
            raise HTTPException(404, "A visual interface is unavailable.")
        return selected

    def invoke(method, *args, **kwargs):
        try:
            return getattr(views(), method)(*args, **kwargs)
        except PluginError as error:
            return JSONResponse({"error": str(error), "code": error.code}, status_code=400)

    @router.post("", status_code=201)
    async def register(body: ViewState):
        if body.prepared is not None:
            raise HTTPException(400, "Register this view before preparing an interface change.")
        return invoke("register", body.generation, dirty=body.dirty)

    @router.put("/{identifier}")
    async def update(identifier: UUID, body: ViewState):
        return invoke("update", str(identifier), body.generation, dirty=body.dirty, prepared=body.prepared)

    @router.get("/{identifier}")
    async def status(identifier: UUID):
        return invoke("heartbeat", str(identifier))

    @router.delete("/{identifier}", status_code=204)
    async def unregister(identifier: UUID):
        invoke("unregister", str(identifier))

    return router
