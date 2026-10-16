"""One application hosts isolated budgets and their plugin runtimes."""

import asyncio
import base64
import binascii
import copy
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from whab.domain.errors import StoreError

from .container import decode_container, encode_container
from .credentials import encrypt_credentials
from .document import atomic_write, encode_bundle
from .session import BudgetSession, container_digest

COOKIE = "whab-budget"


def request_password(request):
    """Read an explicitly supplied password without putting it in a URL or body."""
    value = request.headers.get("x-budget-password")
    encoding = request.headers.get("x-budget-password-encoding")
    if value is None:
        if encoding is not None:
            raise StoreError("Supply a budget password with its encoding header.")
        return None
    try:
        if len(value) > 8192:
            raise ValueError
        if encoding == "base64":
            password = base64.b64decode(value, validate=True).decode("utf-8")
        elif encoding is None:
            value.encode("ascii")
            password = value
        else:
            raise ValueError
        if len(password.encode("utf-8")) > 4096:
            raise ValueError
        return password
    except (ValueError, UnicodeError, binascii.Error):
        raise StoreError("Use a valid ASCII password header or UTF-8 password encoded as base64.") from None


class NameBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)


class CopyBody(NameBody):
    path: str = Field(min_length=1, max_length=4096)


class PluginDataBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    settings: dict | None = None
    state: dict | None = None


class BudgetHost:
    def __init__(self, settings):
        self.settings = settings
        self.applications = {}
        self.contexts = []
        self.default_id = "default"
        self._legacy_export = None
        self._open_lock = asyncio.Lock()
        self._recent_path = settings.home / "budgets" / f"index-{settings.plaid_env}.json"
        self.recent = self._read_recent()

    def _read_recent(self):
        if not self._recent_path.exists():
            return {}
        try:
            with self._recent_path.open("rb") as source:
                raw = source.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            entries = json.loads(raw)
            if type(entries) is not list or len(entries) > 1000:
                raise ValueError()
            result = {}
            for entry in entries:
                if (
                    type(entry) is not dict or entry.keys() != {"id", "name", "path"}
                    or type(entry["id"]) is not str or str(UUID(entry["id"])) != entry["id"]
                    or entry["id"] in result
                    or type(entry["name"]) is not str or not entry["name"].strip()
                    or len(entry["name"]) > 100 or "\x00" in entry["name"]
                    or type(entry["path"]) is not str or len(entry["path"]) > 4096
                    or "\x00" in entry["path"] or not Path(entry["path"]).is_absolute()
                ):
                    raise ValueError()
                result[entry["id"]] = entry
            return result
        except (ValueError, TypeError, AttributeError, OSError):
            raise StoreError("The recent budget list is unreadable. Repair its device-local index before opening WHAB.") from None

    def _remember(self, session):
        entry = {"id": session.id, "name": session.name, "path": str(session.path)}
        updated = {key: value for key, value in self.recent.items() if key != session.id}
        updated[session.id] = entry
        updated = dict(list(updated.items())[-1000:])
        raw = json.dumps(list(updated.values())).encode()
        while len(raw) > 1024 * 1024:
            updated.pop(next(iter(updated)))
            raw = json.dumps(list(updated.values())).encode()
        try:
            atomic_write(self._recent_path, raw)
        except OSError:
            raise StoreError("The recent budget list could not be saved. Check this device's runtime directory.") from None
        self.recent = updated

    def register(self, app, session):
        budget_id = session.id if session else "default"
        self.default_id = budget_id
        self.applications[budget_id] = (app, session)
        # A command-line-opened budget is already active. Remember it in memory;
        # ordinary open/activate requests persist the registry after startup.
        if session:
            self.recent[budget_id] = {"id": budget_id, "name": session.name, "path": str(session.path)}

    async def remember_current(self):
        """Persist the startup budget only after its services started successfully."""
        async with self._open_lock:
            _, session = self.applications[self.default_id]
            if session:
                await run_in_threadpool(self._remember, session)

    async def close(self):
        async with self._open_lock:
            failure = None
            for context in reversed(self.contexts):
                try:
                    await context.__aexit__(None, None, None)
                except Exception as error:
                    failure = failure or error
            self.contexts.clear()
            if failure:
                raise failure

    async def open(self, raw=None, name=None, as_copy=False, password=None):
        async with self._open_lock:
            return await self._open(raw=raw, name=name, as_copy=as_copy, password=password)

    async def activate(self, budget_id, password=None):
        async with self._open_lock:
            if budget_id in self.applications:
                _, session = self.applications[budget_id]
                if session:
                    await run_in_threadpool(self._remember, session)
                return
            entry = self.recent.get(budget_id)
            if entry is None:
                raise HTTPException(404, "Open this budget file before selecting it.")
            session = await run_in_threadpool(
                BudgetSession.open, self.settings.home, self.settings.plaid_env, entry["path"],
                password=password,
            )
            if session.id != budget_id:
                raise StoreError("The saved budget file now contains a different budget. Open the intended file explicitly.")
            await self._start(session)

    async def _open(self, raw=None, name=None, as_copy=False, password=None):
        if raw is not None:
            container = await run_in_threadpool(decode_container, raw, password=password)
            manifest = container.manifest
            if as_copy:
                manifest["id"] = str(uuid4())
                if container.legacy_ledger is not None:
                    raw = await run_in_threadpool(encode_bundle, manifest, container.legacy_ledger)
                else:
                    credential_blob = await run_in_threadpool(encrypt_credentials, manifest["id"], container.credentials, password) if container.credential_blob is not None else None
                    raw = await run_in_threadpool(encode_container, manifest, container.files, credential_blob)
                container = await run_in_threadpool(decode_container, raw, password=password)
            budget_id = manifest["id"]
            if budget_id in self.applications:
                _, current = self.applications[budget_id]
                current_container = await run_in_threadpool(
                    lambda: decode_container(current.bundle(preserve_credentials=True), password=current.password),
                )
                if container_digest(container) != container_digest(current_container):
                    raise StoreError("A different revision of this budget is already open. Open this file as a separate budget to keep both revisions.")
                return current
            path = self.settings.home / "budgets" / budget_id / self.settings.plaid_env / "opened.whab"
            if path.exists():
                try:
                    previous = decode_container(path.read_bytes(), password=password)
                except StoreError:
                    raise StoreError("The saved budget could not be verified as this credential revision. Open this file as a separate budget to keep both revisions.") from None
                if container_digest(container) != container_digest(previous):
                    raise StoreError("A different saved revision of this budget is already on this device. Open this file as a separate budget to keep both revisions.")
            await run_in_threadpool(atomic_write, path, raw)
            session = BudgetSession(
                self.settings.home, self.settings.plaid_env, manifest, path=path,
                ledger=container.legacy_ledger, files=container.files,
                credentials=container.credentials, credential_blob=container.credential_blob,
                password=password,
            )
        else:
            session = BudgetSession.create(
                self.settings.home, self.settings.plaid_env, name,
                self.settings.plugins, "sqlite",
            )
        await self._start(session)
        return session

    async def _start(self, session):
        from whab.app import create_app

        settings = replace(self.settings, budget_path=session.path, storage=session.manifest["storage"], plugins=session.selection, copy_paths=())
        app = await run_in_threadpool(create_app, settings=settings, budget_session=session, budget_host=self, _child=True)
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        try:
            await run_in_threadpool(self._remember, session)
        except Exception:
            await context.__aexit__(None, None, None)
            raise
        self.contexts.append(context)
        self.applications[session.id] = (app, session)

    def list(self, current_id):
        entries = {key: {"id": key, "name": entry["name"], "active": key == current_id} for key, entry in self.recent.items()}
        entries.update({
            key: {"id": key, "name": session.name if session else "My budget", "active": key == current_id}
            for key, (_, session) in self.applications.items()
        })
        return {"budgets": [
            entry for entry in entries.values()
        ]}

    def router(self, store, settings, session, storage_config=None):
        router = APIRouter()
        current_id = session.id if session else "default"

        def activate(budget_id):
            response = JSONResponse({"id": budget_id, "opened": True})
            response.set_cookie(COOKIE, budget_id, httponly=True, samesite="strict", secure=True)
            return response

        @router.get("/budget-recovery")
        def recover():
            response = RedirectResponse("/", status_code=303)
            response.delete_cookie(COOKIE, httponly=True, samesite="strict", secure=True)
            return response

        @router.get("/api/budgets")
        def list_budgets():
            return self.list(current_id)

        @router.get("/api/budget")
        def current():
            if session:
                return session.projection()
            return {"id": "default", "name": "My budget", "portable": False, "storage": settings.storage, "copies": [], "plugins": list(settings.plugins), "missingPlugins": []}

        @router.post("/api/budget/new")
        async def new(body: NameBody):
            opened = await self.open(name=body.name)
            return activate(opened.id)

        @router.post("/api/budget/open")
        async def open_budget(request: Request, as_copy: bool = False):
            opened = await self.open(raw=await request.body(), as_copy=as_copy, password=request_password(request))
            return activate(opened.id)

        @router.post("/api/budgets/{budget_id}/activate")
        async def select(budget_id: str, request: Request):
            await self.activate(budget_id, password=request_password(request))
            return activate(budget_id)

        @router.get("/api/budget/export")
        def export(request: Request, include_credentials: bool = False):
            document = session
            if document is None:
                if self._legacy_export is None:
                    self._legacy_export = BudgetSession.create(settings.home, settings.plaid_env, "My budget", settings.plugins, settings.storage)
                document = self._legacy_export
                document.store = store
                document.storage_config = storage_config or {}
            raw = document.bundle(include_credentials=include_credentials, password=request_password(request))
            return Response(raw, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="budget.whab"'})

        @router.post("/api/budget/save")
        def save():
            if session is None:
                raise HTTPException(400, "Open a portable budget before saving copies.")
            session.checkpoint()
            return session.projection()

        @router.post("/api/budget/copies")
        def add_copy(body: CopyBody):
            if session is None:
                raise HTTPException(400, "Open a portable budget before adding a save destination.")
            session.add_copy(body.name, body.path)
            return session.projection()

        @router.get("/api/budget/plugins/{plugin_id}")
        def plugin_data(plugin_id: str):
            record = session.plugin_record(plugin_id) if session else None
            if record is None:
                raise HTTPException(404, "Plugin is not part of this portable budget.")
            return copy.deepcopy(record)

        @router.put("/api/budget/plugins/{plugin_id}")
        def save_plugin_data(plugin_id: str, body: PluginDataBody):
            if session is None:
                raise HTTPException(400, "Open a portable budget before saving its plugin data.")
            session.save_plugin_data(plugin_id, settings=body.settings, state=body.state)
            return copy.deepcopy(session.plugin_record(plugin_id))

        return router


class BudgetDispatch:
    def __init__(self, app, host):
        self.app = app
        self.host = host

    def _local(self, scope):
        headers = dict(scope.get("headers", ()))
        try:
            hostname = urlsplit("http://" + headers.get(b"host", b"").decode("latin-1")).hostname
        except ValueError:
            return False
        origin = headers.get(b"origin")
        origins = {f"https://{host}:{self.host.settings.port}".encode() for host in ("localhost", "127.0.0.1")}
        return (
            hostname in {"localhost", "127.0.0.1"}
            and (origin is None or origin in origins)
            and headers.get(b"sec-fetch-site") != b"cross-site"
        )

    @staticmethod
    async def _unavailable(scope, receive, send):
        message = "This budget is unavailable. Select or reopen its file before continuing."
        response = (
            JSONResponse({"error": message}, status_code=409)
            if scope["path"].startswith("/api/")
            else HTMLResponse(
                f'<h1>Budget unavailable</h1><p>{message}</p>'
                '<a href="/budget-recovery">Return to the startup budget</a>', status_code=409,
            )
        )
        response.headers["Cache-Control"] = "no-store"
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Reject before cookie dispatch can activate a saved runtime or route remotely.
            if scope.get("scheme") != "https":
                response = JSONResponse({"error": "HTTPS is required"}, status_code=400, headers={"Cache-Control": "no-store"})
                await response(scope, receive, send)
                return
            prefix = "/remote/v1/budgets/"
            if scope["path"].startswith(prefix):
                budget_id = scope["path"][len(prefix):].partition("/")[0]
                entry = self.host.applications.get(budget_id)
                if entry and budget_id != self.host.default_id:
                    await entry[0](scope, receive, send)
                    return
                # A remote request selects its budget explicitly. Cookies cannot
                # redirect it, and unauthenticated requests never start runtimes.
                await self.app(scope, receive, send)
                return
            from http.cookies import SimpleCookie

            cookies = SimpleCookie()
            for key, value in scope.get("headers", ()):
                if key == b"cookie":
                    try:
                        cookies.load(value.decode("latin-1"))
                    except Exception:
                        pass
            selected = cookies.get(COOKIE)
            budget_id = selected.value if selected else self.host.default_id
            entry = self.host.applications.get(budget_id)
            control = (
                scope["path"] in {"/budget-recovery", "/api/budgets", "/api/budget/open", "/api/budget/new"}
                or scope["path"].startswith("/api/budgets/")
            )
            if selected and not entry and not control and self._local(scope):
                if budget_id not in self.host.recent:
                    await self._unavailable(scope, receive, send)
                    return
                try:
                    await self.host.activate(budget_id)
                except (StoreError, HTTPException):
                    await self._unavailable(scope, receive, send)
                    return
                entry = self.host.applications.get(budget_id)
            if entry and budget_id != self.host.default_id:
                await entry[0](scope, receive, send)
                return
        await self.app(scope, receive, send)
