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
            or not scope["path"].startswith("/api/")
            or scope["method"] in {"GET", "HEAD", "OPTIONS"}
        ):
            await self.app(scope, receive, send)
            return
        limit = (
            10 * 1024 * 1024 if scope["path"] in self.file_import_paths
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


def install_middleware(app, port, webhook_paths=(), file_import_paths=()):
    webhook_paths = frozenset(webhook_paths)
    file_import_paths = frozenset(file_import_paths)
    origins = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1")}

    @app.middleware("http")
    async def local_access(request, call_next):
        webhook = request.url.path in webhook_paths
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
            file_import = request.url.path in file_import_paths
            content_type = "application/zip" if file_import else "application/json"
            if (
                request.method != "DELETE"
                and request.headers.get("content-type", "").split(";")[0] != content_type
            ):
                return JSONResponse({"error": f"Use {content_type}"}, status_code=415)
            limit = 10 * 1024 * 1024 if file_import else 128 * 1024 if webhook else 32 * 1024
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

    app.add_middleware(
        RequestBodyLimit, webhook_paths=webhook_paths, file_import_paths=file_import_paths
    )
