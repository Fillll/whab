"""Guarded file parsing and explicit, atomic migration; parsers receive bytes only."""

import hashlib
import inspect

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from whab.domain.file_imports import validate_workspace_import
from whab.plugins import StoreError


def create_router(store, plugins):
    router = APIRouter(prefix="/api/plugins")

    def descriptor(plugin_id):
        plugin = next((p for p in plugins.file_import_plugins() if p.id == plugin_id), None)
        if plugin is None:
            raise HTTPException(404, "File import plugin not enabled")
        return plugin

    async def parse(request, plugin):
        if request.headers.get("x-import-currency") != "USD":
            raise HTTPException(400, "Confirm that the export uses USD before importing.")
        raw = await request.body()
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise HTTPException(400, "Choose a ZIP export no larger than 10 MiB.")

        def normalize():
            try:
                result = plugin.file_import.parse(raw)
                if inspect.iscoroutine(result):
                    result.close()
                return validate_workspace_import(result)
            except StoreError:
                raise
            except Exception:
                raise StoreError("The file import plugin could not read this export.") from None

        return hashlib.sha256(raw).hexdigest(), await run_in_threadpool(normalize)

    @router.post("/{plugin_id}/import/preview")
    async def preview(plugin_id: str, request: Request):
        plugin = descriptor(plugin_id)
        fingerprint, data = await parse(request, plugin)

        def availability():
            if not all(callable(getattr(store, name, None)) for name in (
                "import_workspace", "preview_workspace_import",
            )):
                return {
                    "canImport": False,
                    "reason": "The selected storage plugin does not support file imports.",
                    "alreadyImported": False,
                }
            return store.preview_workspace_import("personal", plugin.id, fingerprint)

        availability = await run_in_threadpool(availability)
        return {
            "fingerprint": fingerprint,
            "accounts": len(data.accounts),
            "categories": len(data.categories),
            "transactions": len(data.transactions),
            "assignments": len(data.assignments),
            "budgetStartMonth": data.budget_start_month,
            "warnings": data.warnings,
            **availability,
        }

    @router.post("/{plugin_id}/import/commit")
    async def commit(plugin_id: str, request: Request):
        plugin = descriptor(plugin_id)
        fingerprint, data = await parse(request, plugin)
        if request.headers.get("x-import-fingerprint") != fingerprint:
            raise HTTPException(409, "The file changed. Preview this export again before importing.")
        importer = getattr(store, "import_workspace", None)
        if not callable(importer):
            raise HTTPException(400, "The selected storage plugin does not support file imports.")
        return await run_in_threadpool(importer, "personal", plugin.id, fingerprint, data)

    return router
