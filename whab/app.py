"""Python HTTP routes, validation, and server-rendered pages for WHAB."""

import csv
import io
import json
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
)

from . import __version__
from .config import ROOT, Settings
from .plaid_service import PlaidError, PlaidService
from .store import UNSET, Store, StoreError, current_month
from .views import build_context

Workspace = Literal["demo", "personal"]
Id = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Cents = Annotated[StrictInt, Field(ge=-1_000_000_000_000, le=1_000_000_000_000)]
PositiveCents = Annotated[StrictInt, Field(ge=0, le=1_000_000_000_000)]


def valid_month(value):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value) or not "1900-01" <= value <= "2200-12":
        raise ValueError("Invalid month")
    return value


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewBody(Body):
    workspace: Workspace
    categoryId: Id | None = None
    reviewed: StrictBool | None = None


class TransactionBody(Body):
    workspace: Workspace
    accountId: Id
    date: str
    merchant: Label
    amount: Cents
    categoryId: Id | None
    note: Annotated[str, Field(max_length=1000)] = ""

    @field_validator("date")
    @classmethod
    def actual_date(cls, value):
        if (
            date.fromisoformat(value).isoformat() != value
            or not "1900-01-01" <= value <= "2200-12-31"
        ):
            raise ValueError("Invalid date")
        return value

    @field_validator("amount")
    @classmethod
    def nonzero(cls, value):
        if value == 0:
            raise ValueError("Amount must not be zero")
        return value


class AssignmentBody(Body):
    workspace: Workspace
    month: str
    assigned: PositiveCents
    _month = field_validator("month")(valid_month)


class CategoryBody(Body):
    workspace: Workspace
    name: Label
    group: Label
    target: PositiveCents


class AccountBody(Body):
    workspace: Workspace
    name: Label
    type: Literal["depository", "credit"]
    balance: Cents


class LinkBody(Body):
    itemId: Id | None = None


class ExchangeBody(Body):
    publicToken: Annotated[str, Field(min_length=1, max_length=500)]
    institutionId: Id | None = None
    institutionName: Label | None = None


class RequestBodyLimit:
    """Bound chunked request bodies before Starlette/Pydantic buffer or parse them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not scope["path"].startswith("/api/")
            or scope["method"] in {"GET", "HEAD", "OPTIONS"}
        ):
            await self.app(scope, receive, send)
            return
        limit = 128 * 1024 if scope["path"] == "/api/plaid/webhook" else 32 * 1024
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > limit:
                response = JSONResponse({"error": "Request too large"}, status_code=413)
                await response(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(store=None, plaid=None, settings=None):
    settings = settings or Settings.load()
    owned_store = store is None
    store = store or Store(settings.database_path)
    plaid = plaid or PlaidService(store, settings.plaid_environment())

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if hasattr(plaid, "close"):
            await plaid.close()
        if owned_store:
            store.close()

    app = FastAPI(
        title="WHAB",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    templates = Jinja2Templates(directory=ROOT / "templates")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    origins = {f"http://{host}:{settings.port}" for host in ("localhost", "127.0.0.1")}

    @app.middleware("http")
    async def local_access(request, call_next):
        webhook = request.url.path == "/api/plaid/webhook"
        if not webhook:
            try:
                host = urlsplit("http://" + request.headers.get("host", "")).hostname
            except ValueError:
                host = None
            if host not in {"localhost", "127.0.0.1"}:
                return JSONResponse({"error": "Local access only"}, status_code=403)
            origin = request.headers.get("origin")
            if (origin and origin not in origins) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return JSONResponse({"error": "Cross-origin access is blocked"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            if (
                request.method != "DELETE"
                and request.headers.get("content-type", "").split(";")[0] != "application/json"
            ):
                return JSONResponse({"error": "Use application/json"}, status_code=415)
            limit = 128 * 1024 if webhook else 32 * 1024
            try:
                if int(request.headers.get("content-length", "0")) > limit:
                    return JSONResponse({"error": "Request too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"error": "Invalid request"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse({"error": "Invalid request"}, status_code=400)

    @app.exception_handler(StoreError)
    async def store_error(_request, error):
        return JSONResponse({"error": str(error)}, status_code=400)

    @app.exception_handler(PlaidError)
    async def plaid_error(_request, error):
        return JSONResponse({"error": str(error)}, status_code=400)

    @app.exception_handler(Exception)
    async def safe_error(_request, _error):
        return JSONResponse(
            {"error": "Request could not complete. Check your local configuration and retry."},
            status_code=500,
        )

    def public_status():
        return {**plaid.config, "connectedItems": len(store.items())}

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        workspace: Workspace = "demo",
        page: Literal["budget", "transactions", "insights", "accounts"] = "budget",
        month: str | None = None,
        category: str | None = None,
    ):
        month = month or current_month()
        try:
            valid_month(month)
        except ValueError as error:
            raise HTTPException(400, "Invalid month") from error
        data = store.workspace(workspace, month)
        context = build_context(
            data,
            public_status(),
            store.items(),
            workspace=workspace,
            page=page,
            month=month,
            selected=category,
        )
        return templates.TemplateResponse(request=request, name="base.html", context=context)

    @app.get("/api/status")
    def status():
        return public_status()

    @app.get("/api/workspace")
    def workspace_data(workspace: Workspace, month: str | None = None):
        month = month or current_month()
        try:
            valid_month(month)
        except ValueError as error:
            raise HTTPException(400, "Invalid month") from error
        return store.workspace(workspace, month)

    @app.get("/api/export")
    def export(workspace: Workspace):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "date",
                "account",
                "merchant",
                "amount_cents",
                "category",
                "pending",
                "reviewed",
                "source",
                "note",
                "transfer",
            ]
        )
        accounts = {row["id"]: row["name"] for row in store.accounts(workspace)}
        categories = {row["id"]: row["name"] for row in store.categories(workspace)}

        def safe_cell(value):
            text = str(value or "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

        for transaction in store.transactions(workspace):
            writer.writerow(
                [
                    transaction["date"],
                    safe_cell(accounts.get(transaction["accountId"])),
                    safe_cell(transaction["merchant"]),
                    transaction["amount"],
                    safe_cell(categories.get(transaction["categoryId"])),
                    transaction["pending"],
                    transaction["reviewed"],
                    transaction["source"],
                    safe_cell(transaction["note"]),
                    transaction.get("isTransfer", False),
                ]
            )
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="whab-{workspace}.csv"'},
        )

    @app.patch("/api/transactions/{transaction_id}")
    def review(transaction_id: str, body: ReviewBody):
        if not body.model_fields_set.intersection({"categoryId", "reviewed"}):
            raise HTTPException(400, "No changes provided")
        category = body.categoryId if "categoryId" in body.model_fields_set else UNSET
        store.edit_transaction(
            body.workspace, transaction_id, category_id=category, reviewed=body.reviewed
        )
        return {"ok": True}

    @app.post("/api/transactions")
    def add_transaction(body: TransactionBody):
        store.add_transaction(body.workspace, body.model_dump(exclude={"workspace"}))
        return {"ok": True}

    @app.put("/api/budget/{category_id}")
    def assignment(category_id: str, body: AssignmentBody):
        store.assign(body.workspace, category_id, body.month, body.assigned)
        return {"ok": True}

    @app.post("/api/categories")
    def category(body: CategoryBody):
        store.add_category(body.workspace, body.model_dump(exclude={"workspace"}))
        return {"ok": True}

    @app.post("/api/accounts")
    def account(body: AccountBody):
        account_id = store.add_account(body.workspace, body.model_dump(exclude={"workspace"}))
        return {"ok": True, "accountId": account_id}

    @app.get("/api/plaid/items")
    def items():
        return {"items": store.items()}

    @app.post("/api/plaid/link-token")
    async def link(body: LinkBody):
        return {"link_token": await plaid.link_token(body.itemId)}

    @app.post("/api/plaid/exchange")
    async def exchange(body: ExchangeBody):
        await plaid.exchange(body.publicToken, body.institutionName)
        return {"ok": True}

    @app.post("/api/plaid/sync")
    async def sync(body: LinkBody):
        return {"ok": True, **await plaid.sync(body.itemId)}

    @app.delete("/api/plaid/items/{item_id}")
    async def disconnect(item_id: str):
        await plaid.disconnect(item_id)
        return {"ok": True}

    @app.post("/api/plaid/webhook")
    async def webhook(request: Request):
        raw = await request.body()
        if not await plaid.verify_webhook(raw, request.headers.get("plaid-verification")):
            return JSONResponse({"error": "Invalid webhook"}, status_code=401)
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "Invalid webhook"}, status_code=400)
        if not isinstance(event, dict) or not isinstance(event.get("item_id"), str):
            return JSONResponse({"error": "Invalid webhook"}, status_code=400)
        item_id = event["item_id"]
        if not store.get_item(item_id):
            return {"ok": True}
        if (
            event.get("webhook_type") == "TRANSACTIONS"
            and event.get("webhook_code") == "SYNC_UPDATES_AVAILABLE"
        ):
            plaid.queue_sync(item_id)
        if event.get("webhook_type") == "ITEM":
            code = event.get("webhook_code")
            error = event.get("error") or {}
            if code in {"PENDING_DISCONNECT", "PENDING_EXPIRATION"} or (
                code == "ERROR"
                and isinstance(error, dict)
                and error.get("error_code") == "ITEM_LOGIN_REQUIRED"
            ):
                store.set_item_status(
                    item_id, "reconnect", "Reconnect this institution through Plaid Link."
                )
        return {"ok": True}

    app.state.store = store
    app.state.plaid = plaid
    app.add_middleware(RequestBodyLimit)
    return app
