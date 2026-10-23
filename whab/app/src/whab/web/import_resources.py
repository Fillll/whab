"""Render and serve captured parser-version resources through generation leases."""

import mimetypes
import re

from fastapi import APIRouter, HTTPException, Response
from jinja2 import BaseLoader, TemplateNotFound

from whab.plugins.sdk import valid_plugin_name

_TOKEN = re.compile(r"[0-9a-f]{32}\Z")


class ImportResourceLoader(BaseLoader):
    def __init__(self, plugins):
        self.plugins = plugins

    def get_source(self, environment, template):
        parts = template.split("/", 2)
        if len(parts) != 3 or not valid_plugin_name(parts[0]) or _TOKEN.fullmatch(parts[1]) is None:
            raise TemplateNotFound(template)
        raw = self.plugins.import_resource(parts[0], parts[1], "templates/" + parts[2])
        if raw is None:
            raise TemplateNotFound(template)
        return raw.decode("utf-8"), "import:" + template, lambda: True


def create_router(plugins):
    router = APIRouter()

    def resource(plugin_id, token, name):
        if not valid_plugin_name(plugin_id) or token is not None and _TOKEN.fullmatch(token) is None:
            raise HTTPException(404, "Import resource not available")
        raw = plugins.import_resource(plugin_id, token, "static/" + name)
        if raw is None:
            raise HTTPException(404, "Import resource not available")
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(raw, media_type=media_type, headers={
            "Cache-Control": "public, max-age=31536000, immutable" if token else "no-store",
            "X-Content-Type-Options": "nosniff",
        })

    @router.get("/imports/{plugin_id}/{token}/static/{name:path}")
    def versioned(plugin_id: str, token: str, name: str):
        return resource(plugin_id, token, name)

    @router.get("/imports/{plugin_id}/static/{name:path}")
    def legacy(plugin_id: str, name: str):
        return resource(plugin_id, None, name)

    return router
