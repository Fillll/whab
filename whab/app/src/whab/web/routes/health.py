"""Minimal local readiness and build identity for desktop process managers."""

import os
import re
from uuid import UUID

from fastapi import APIRouter

from whab import __version__


def create_router():
    router = APIRouter()
    build = os.environ.get("WHAB_BUILD_COMMIT", "development")
    if re.fullmatch(r"[0-9a-f]{40}", build) is None:
        build = "development"
    try:
        instance = str(UUID(os.environ.get("WHAB_INSTANCE_ID", "")))
    except ValueError:
        instance = None

    @router.get("/api/health")
    def health():
        # Uvicorn serves requests only after the application's lifespan starts.
        # This endpoint deliberately never queries a budget or bank service.
        return {
            "application": "whab", "ready": True, "version": __version__,
            "build": build, "instance": instance,
        }

    return router
