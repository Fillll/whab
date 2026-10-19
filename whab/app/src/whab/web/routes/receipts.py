"""Local HTTPS-only receipt transport; uploaded images never use public asset paths."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from whab.web.schemas import ReceiptSelectionBody, Workspace


def create_router(service):
    router = APIRouter()

    @router.get("/api/receipts")
    def index(workspace: Workspace):
        return service.index(workspace)

    @router.post("/api/receipts/index")
    def index_for(body: ReceiptSelectionBody):
        return service.index_for(body.workspace, body.transactionIds)

    @router.get("/api/transactions/{transaction_id:path}/receipt")
    def get(transaction_id: str, workspace: Workspace):
        image = service.get(workspace, transaction_id)
        if image is None:
            raise HTTPException(404, "No receipt saved for this transaction.")
        raw, media_type = image
        return Response(raw, media_type=media_type, headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="receipt"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
        })

    @router.put("/api/transactions/{transaction_id:path}/receipt")
    async def save(transaction_id: str, workspace: Workspace, request: Request):
        raw = await request.body()
        await run_in_threadpool(service.save, workspace, transaction_id, raw,
                                request.headers.get("content-type", "").split(";")[0])
        return {"ok": True}

    @router.delete("/api/transactions/{transaction_id:path}/receipt")
    def delete(transaction_id: str, workspace: Workspace):
        service.delete(workspace, transaction_id)
        return {"ok": True}

    return router
