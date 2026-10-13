"""Bank plugin protocol endpoints share host validation and local-access guards."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from whab.domain.errors import StoreError
from whab.plugins import BankError, WebhookResult
from whab.web.bank_schemas import ExchangeBody, LinkBody


def create_router(plugin_id, banks, store, prefix=None):
    router = APIRouter(prefix=prefix or f"/api/banks/{plugin_id}")

    async def invoke(method, *args):
        bank = banks.get(plugin_id)
        if bank is None:
            raise HTTPException(503, "Bank plugin is not running")
        try:
            return await getattr(bank, method)(*args)
        except (BankError, StoreError):
            raise
        except Exception:
            raise BankError(
                "Bank plugin request could not complete. Check its configuration and retry."
            ) from None

    @router.get("/items")
    def items():
        return {"items": store.items()}

    @router.post("/link-token")
    async def link(body: LinkBody):
        token = await invoke("link_token", body.itemId)
        if type(token) is not str or not 1 <= len(token) <= 4096:
            raise BankError("Bank plugin returned an invalid connection token.")
        return {"link_token": token}

    @router.post("/exchange")
    async def exchange(body: ExchangeBody):
        await invoke("exchange", body.publicToken, body.institutionName)
        return {"ok": True}

    @router.post("/sync")
    async def sync(body: LinkBody):
        result = await invoke("sync", body.itemId)
        # Preserve sync counters without passing arbitrary provider payloads to clients.
        fields = ("added", "modified", "removed")
        if type(result) is not dict or any(type(result.get(k)) is not int for k in fields):
            raise BankError("Bank plugin returned an invalid synchronization result.")
        return {"ok": True, **{key: result[key] for key in fields}}

    @router.delete("/items/{item_id:path}")
    async def disconnect(item_id: str):
        await invoke("disconnect", item_id)
        return {"ok": True}

    @router.post("/webhook")
    async def webhook(request: Request):
        result = await invoke("webhook", await request.body(), dict(request.headers))
        if type(result) is not WebhookResult:
            raise BankError("Bank plugin returned an invalid webhook response.")
        try:
            result = WebhookResult(result.ok, result.status_code, result.error)
        except ValueError:
            raise BankError("Bank plugin returned an invalid webhook response.") from None
        # Do not surface arbitrary provider error text from a public webhook endpoint.
        payload = {"ok": True} if result.ok else {"error": "Invalid webhook"}
        return JSONResponse(payload, status_code=result.status_code)

    return router
