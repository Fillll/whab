"""Run an installed WHAB build with a minimal process-manager readiness probe.

The wrapper also supports WHAB builds predating the host's /api/health route.
It does not alter budget behavior or expose a process-control API over HTTPS.
"""

import argparse
import os
import re
import ssl
from urllib.parse import urlsplit
from uuid import UUID


class ManagedApplication:
    """Keep readiness independent of the selected budget and its bank adapters."""

    def __init__(self, application, *, port, version, build, instance):
        self.application = application
        self.port = port
        self.identity = {
            "application": "whab", "ready": True, "version": version,
            "build": build, "instance": instance,
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/health":
            return await self.application(scope, receive, send)
        from starlette.responses import JSONResponse

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        response_headers = {
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        }
        if scope.get("scheme") != "https":
            response = JSONResponse({"error": "HTTPS is required"}, status_code=400, headers=response_headers)
        else:
            try:
                hostname = urlsplit("http://" + headers.get("host", "")).hostname
            except ValueError:
                hostname = None
            origins = {f"https://{host}:{self.port}" for host in ("localhost", "127.0.0.1")}
            origin = headers.get("origin")
            if hostname not in {"localhost", "127.0.0.1"}:
                response = JSONResponse({"error": "Local access only"}, status_code=403, headers=response_headers)
            elif (origin and origin not in origins) or headers.get("sec-fetch-site") == "cross-site":
                response = JSONResponse({"error": "Cross-origin access is blocked"}, status_code=403, headers=response_headers)
            elif scope["method"] != "GET":
                response = JSONResponse({"error": "Use GET"}, status_code=405, headers={**response_headers, "Allow": "GET"})
            else:
                response = JSONResponse(self.identity, headers=response_headers)
        return await response(scope, receive, send)


def create_application(settings):
    from whab import __version__
    from whab.app import create_app

    try:
        instance = str(UUID(os.environ.get("WHAB_INSTANCE_ID", "")))
    except ValueError:
        raise ValueError("A valid managed instance identity is required.") from None
    build = os.environ.get("WHAB_BUILD_COMMIT", "development")
    if re.fullmatch(r"(?:[0-9a-f]{40}|development-[0-9a-f]{24})", build) is None:
        build = "development"
    return ManagedApplication(
        create_app(settings=settings), port=settings.port,
        version=__version__, build=build, instance=instance,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    os.environ["PORT"] = str(args.port)
    from whab.config import Settings
    from whab.tls import validate_tls

    settings = Settings.load()
    validate_tls(settings.tls_cert_file, settings.tls_key_file)
    # Validate launch identity before binding a port or opening storage.
    if str(UUID(args.instance)) != str(UUID(os.environ.get("WHAB_INSTANCE_ID", ""))):
        parser.error("The managed instance identity does not match.")
    import uvicorn

    uvicorn.run(
        lambda: create_application(settings), factory=True,
        host="127.0.0.1", port=settings.port, reload=False, access_log=False,
        log_level="warning", ssl_certfile=str(settings.tls_cert_file),
        ssl_keyfile=str(settings.tls_key_file), ssl_version=ssl.PROTOCOL_TLS_SERVER,
        proxy_headers=False, forwarded_allow_ips="",
    )


if __name__ == "__main__":
    main()
