"""Local-only request guards applied to every feature router."""

from urllib.parse import urlsplit

from fastapi.responses import JSONResponse


class RequestBodyLimit:
    """Bound chunked request bodies before Starlette/Pydantic buffer or parse them."""

    def __init__(self, app, webhook_paths, file_import_paths=()):
        self.app = app
        self.webhook_paths = frozenset(webhook_paths)
        self.file_import_paths = frozenset(file_import_paths)

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not scope["path"].startswith(("/api/", "/remote/"))
            or scope["method"] in {"GET", "HEAD", "OPTIONS"}
        ):
            await self.app(scope, receive, send)
            return
        limit = (
            (50 * 1024 * 1024 if scope["path"] == "/api/budget/open" else 10 * 1024 * 1024) if scope["path"] in self.file_import_paths
            else 128 * 1024 if scope["path"] in self.webhook_paths else 32 * 1024
        )
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


def install_middleware(app, port, webhook_paths=(), file_import_paths=(), remote=False):
    webhook_paths = frozenset(webhook_paths)
    file_import_paths = frozenset(file_import_paths)
    new_budget_import_paths = frozenset(
        path for path in file_import_paths
        if path.endswith(("/import/new/preview", "/import/new/commit"))
    )
    origins = {f"https://{host}:{port}" for host in ("localhost", "127.0.0.1")}

    @app.middleware("http")
    async def local_access(request, call_next):
        if request.url.scheme != "https":
            return JSONResponse({"error": "HTTPS is required"}, status_code=400, headers={"Cache-Control": "no-store"})
        webhook = request.url.path in webhook_paths
        remote_request = remote and (request.url.path == "/remote/v1" or request.url.path.startswith("/remote/v1/"))
        if not webhook and not remote_request:
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
        if request.url.path.startswith(("/api/", "/remote/")) and request.method not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            file_import = request.url.path in file_import_paths
            content_type = "application/zip" if file_import else "application/json"
            if (
                request.method != "DELETE"
                and request.headers.get("content-type", "").split(";")[0] != content_type
            ):
                return JSONResponse({"error": f"Use {content_type}"}, status_code=415)
            limit = (50 * 1024 * 1024 if request.url.path == "/api/budget/open" else 10 * 1024 * 1024) if file_import else 128 * 1024 if webhook else 32 * 1024
            try:
                if int(request.headers.get("content-length", "0")) > limit:
                    return JSONResponse({"error": "Request too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"error": "Invalid request"}, status_code=400)
        workspace = request.scope.get("whab.workspace")
        if workspace and request.url.path.startswith("/api/"):
            if any(value != workspace for value in request.query_params.getlist("workspace")):
                return JSONResponse({"error": "Workspace does not match this budget address"}, status_code=400)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("content-type", "").split(";")[0] == "application/json":
                try:
                    body = await request.json()
                except ValueError:
                    return JSONResponse({"error": "Use valid JSON"}, status_code=400)
                if isinstance(body, dict) and "workspace" in body and body["workspace"] != workspace:
                    return JSONResponse({"error": "Workspace does not match this budget address"}, status_code=400)
            if workspace == "demo":
                path = request.url.path
                personal_only = (
                    path.startswith(("/api/banks/", "/api/plaid/", "/api/budget/plugins/"))
                    or ("/import/" in path and path not in new_budget_import_paths)
                    or path in {"/api/budget/save", "/api/budget/copies"}
                    or (request.method not in {"GET", "HEAD", "OPTIONS"} and (
                        path == "/api/plugins/management" or "/settings/actions/" in path
                    ))
                )
                if personal_only:
                    return JSONResponse({"error": "This operation requires your own budget"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(("/api/", "/remote/")) or request.url.path == "/" or request.url.path.startswith("/budgets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        RequestBodyLimit, webhook_paths=webhook_paths, file_import_paths=file_import_paths
    )
