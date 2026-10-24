"""Bank plugin protocol endpoints share host validation and local-access guards."""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from whab.domain.errors import StoreError
from whab.plugins import BankError, WebhookResult
from whab.plugins.effect_store import EffectError
from whab.plugins.sdk import valid_plugin_name
from whab.web.bank_schemas import ExchangeBody, FlowId, LinkBody

_CONTINUATIONS = ("begin_link", "continuation_status", "cancel_link", "complete_link")
_STAGES = {
    "ready", "exchanging", "exchange_uncertain", "captured", "applying", "apply_uncertain",
    "complete", "cancelled", "expired",
}


def _status(result, *, flow_id=None, begin=False, kind=None):
    fields = {"flow_id", "generation", "kind", "stage", "deadline_ms"}
    if begin:
        fields.add("link_token")
    if (
        type(result) is not dict or set(result) != fields
        or type(result.get("flow_id")) is not str
        or re.fullmatch(r"[a-f0-9]{32}", result["flow_id"]) is None
        or (flow_id is not None and result["flow_id"] != flow_id)
        or type(result.get("generation")) is not int
        or not 1 <= result["generation"] <= 2**53 - 1
        or type(result.get("kind")) is not str or result["kind"] not in {"link", "update"}
        or (kind is not None and result["kind"] != kind)
        or type(result.get("stage")) is not str or result["stage"] not in _STAGES
        or type(result.get("deadline_ms")) is not int
        or not 1 <= result["deadline_ms"] <= 2**53 - 1
        or (begin and (result["stage"] != "ready" or type(result.get("link_token")) is not str
                       or not 1 <= len(result["link_token"]) <= 4096
                       or "\x00" in result["link_token"]))
    ):
        raise BankError("Bank plugin returned an invalid continuation response.")
    return dict(result)


def create_router(plugin_id, banks, store, prefix=None):
    router = APIRouter(prefix=prefix or (f"/api/banks/{plugin_id}" if plugin_id else "/api/banks/{provider_id}"))

    def owner(request):
        identifier = plugin_id or request.path_params.get("provider_id")
        if not valid_plugin_name(identifier):
            raise HTTPException(404, "Bank plugin is not running")
        return identifier

    def service(request):
        bank = banks.get(owner(request))
        if bank is None:
            raise HTTPException(404 if plugin_id is None or prefix == "/api/plaid" else 503, "Bank plugin is not running")
        return bank

    def continued(request):
        bank = service(request)
        methods = [callable(getattr(bank, method, None)) for method in _CONTINUATIONS]
        if any(methods) and not all(methods):
            raise BankError("Bank plugin has an incomplete continuation interface.")
        return all(methods)

    async def invoke(request, method, *args):
        bank = service(request)
        try:
            return await getattr(bank, method)(*args)
        except (BankError, StoreError, EffectError):
            raise
        except Exception:
            raise BankError(
                "Bank plugin request could not complete. Check its configuration and retry."
            ) from None

    async def flow_call(request, method, *args, flow_id=None, begin=False, kind=None):
        try:
            return _status(await invoke(request, method, *args), flow_id=flow_id, begin=begin, kind=kind)
        except EffectError as error:
            return JSONResponse({"error": str(error), "code": error.code}, status_code=409)

    @router.get("/items")
    def items(request: Request):
        service(request)
        scoped = store[owner(request)] if plugin_id is None else store
        return {"items": scoped.items()}

    @router.post("/link-token")
    async def link(body: LinkBody, request: Request):
        if continued(request):
            return await flow_call(
                request, "begin_link", body.itemId, begin=True, kind="update" if body.itemId else "link"
            )
        token = await invoke(request, "link_token", body.itemId)
        if type(token) is not str or not 1 <= len(token) <= 4096 or "\x00" in token:
            raise BankError("Bank plugin returned an invalid connection token.")
        return {"link_token": token}

    @router.post("/exchange")
    async def exchange(body: ExchangeBody, request: Request):
        if continued(request):
            if body.flowId is None:
                error = EffectError("continuation_missing")
                return JSONResponse({"error": str(error), "code": error.code}, status_code=409)
            return await flow_call(
                request, "complete_link", body.flowId, body.publicToken, body.institutionName,
                flow_id=body.flowId,
            )
        if body.flowId is not None:
            raise BankError("This bank plugin does not support connection continuations.")
        if body.publicToken is None:
            raise BankError("A public connection token is required.")
        await invoke(request, "exchange", body.publicToken, body.institutionName)
        return {"ok": True}

    @router.get("/continuations/{flow_id}")
    async def status(flow_id: FlowId, request: Request):
        if not continued(request):
            raise HTTPException(404, "Bank continuations are not available")
        return await flow_call(request, "continuation_status", flow_id, flow_id=flow_id)

    @router.delete("/continuations/{flow_id}")
    async def cancel(flow_id: FlowId, request: Request):
        if not continued(request):
            raise HTTPException(404, "Bank continuations are not available")
        return await flow_call(request, "cancel_link", flow_id, flow_id=flow_id)

    @router.post("/sync")
    async def sync(body: LinkBody, request: Request):
        result = await invoke(request, "sync", body.itemId)
        # Preserve sync counters without passing arbitrary provider payloads to clients.
        fields = ("added", "modified", "removed")
        if type(result) is not dict or any(type(result.get(k)) is not int for k in fields):
            raise BankError("Bank plugin returned an invalid synchronization result.")
        return {"ok": True, **{key: result[key] for key in fields}}

    @router.delete("/items/{item_id:path}")
    async def disconnect(item_id: str, request: Request):
        await invoke(request, "disconnect", item_id)
        return {"ok": True}

    @router.post("/webhook")
    async def webhook(request: Request):
        result = await invoke(request, "webhook", await request.body(), dict(request.headers))
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
