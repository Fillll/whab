"""Host-owned discovery, restart-bound settings and downloads; no plugin HTTP routes."""

import csv
import io
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from whab.domain.accounts import bank_website_entry
from whab.domain.budget import select_currency
from whab.domain.dates import current_month
from whab.plugins import PluginError
from whab.plugins.catalog import CatalogError, catalog_snapshot
from whab.web.csv import safe_cell
from whab.web.navigation import budget_key
from whab.web.plugin_snapshot import build_snapshot
from whab.web.schemas import (
    BankLogoPreviewBody,
    PluginRecoveryBody,
    PluginSelectionBody,
    PluginUpgradeBody,
    Workspace,
    valid_month,
)


def create_router(store, plugins, plugin_manager=None):
    router = APIRouter(prefix="/api/plugins")

    @router.get("")
    def describe():
        return {"plugins": [asdict(plugin) for plugin in plugins.describe()]}

    @router.get("/catalog")
    def curated_catalog():
        try:
            return JSONResponse(catalog_snapshot(), headers={"Cache-Control": "no-store"})
        except CatalogError as error:
            raise HTTPException(503, str(error)) from None

    @router.get("/runtime")
    def runtime_status():
        if plugin_manager is None or plugin_manager.live is None:
            raise HTTPException(404, "Live plugin management is unavailable")
        return plugin_manager.live.status()

    @router.post("/management/transition", status_code=202)
    async def transition(request: Request, body: PluginSelectionBody):
        if request.scope.get("whab.workspace") == "demo":
            raise HTTPException(403, "This operation requires your own budget")
        if plugin_manager is None:
            raise HTTPException(404, "Live plugin management is unavailable")
        try:
            selection = tuple(body.plugins)
            await run_in_threadpool(plugin_manager.preflight_live, selection)
            identifier = plugin_manager.schedule_live(selection, validated=True)
        except PluginError as error:
            return JSONResponse({"error": str(error), "code": error.code,
                                 "plugin_id": error.plugin_id}, status_code=409)
        return {"id": identifier, "accepted": True, "status": plugin_manager.live.status()}

    @router.post("/management/upgrade", status_code=202)
    async def upgrade(request: Request, body: PluginUpgradeBody):
        if request.scope.get("whab.workspace") == "demo":
            raise HTTPException(403, "This operation requires your own budget")
        if plugin_manager is None or plugin_manager.live is None:
            raise HTTPException(404, "Live plugin management is unavailable")
        from whab.plugins.environments import ApprovedWheel

        identifier = plugin_manager.live.schedule_upgrade(body.pluginId, tuple(
            ApprovedWheel(wheel.path, wheel.sha256) for wheel in body.wheels
        ))
        return {"id": identifier, "accepted": True}

    @router.post("/management/recover")
    async def recover(request: Request, body: PluginRecoveryBody):
        if request.scope.get("whab.workspace") == "demo":
            raise HTTPException(403, "This operation requires your own budget")
        if plugin_manager is None or plugin_manager.live is None:
            raise HTTPException(404, "Live plugin management is unavailable")
        try:
            generation = await plugin_manager.live.recover(body.authority)
        except PluginError as error:
            return JSONResponse({"error": str(error), "code": error.code,
                                 "plugin_id": error.plugin_id}, status_code=409)
        return {"generation": generation, "status": plugin_manager.live.status()}

    @router.post("/bank-logo-preview")
    def preview_bank_logo(request: Request, body: BankLogoPreviewBody):
        website = bank_website_entry(body.website)
        result = {
            "available": False, "website": website, "logoUrl": None,
            "provider": plugins.bank_logo_provider(),
        }
        if website is None:
            return result
        logo = plugins.bank_logo("", website=website)
        if logo is None or plugins.logo_image(*logo) is None:
            return result
        identifier = request.scope.get("whab.budget_id")
        prefix = f"/budgets/{budget_key(identifier)}" if identifier is not None else ""
        return {
            **result, "available": True,
            "logoUrl": f"{prefix}/api/plugins/{logo[0]}/bank-logos/{logo[1]}",
            "provider": plugins.bank_logo_provider(logo[0]),
        }

    def settings_data(plugin_id):
        try:
            settings = plugins.settings(plugin_id)
        except PluginError as error:
            if error.code == "unavailable":
                raise HTTPException(404, "Plugin settings not found") from None
            raise
        result = {key: [asdict(record) for record in records] for key, records in settings.items()}
        if any(plugin.id == plugin_id and plugin.categorization for plugin in plugins.descriptors()):
            result["categorization"] = True
        return result

    @router.get("/{plugin_id}/settings")
    def plugin_settings(plugin_id: str):
        return settings_data(plugin_id)

    @router.post("/{plugin_id}/settings/actions/{action_id}")
    def settings_action(plugin_id: str, action_id: str):
        try:
            message = plugins.settings_action(plugin_id, action_id)
        except PluginError as error:
            if error.code == "unavailable":
                raise HTTPException(404, "Plugin action not found") from None
            raise
        return {"message": message, "settings": settings_data(plugin_id)}

    @router.get("/{plugin_id}/bank-logos/{key}")
    def bank_logo(plugin_id: str, key: str):
        image = plugins.logo_image(plugin_id, key)
        if image is None:
            raise HTTPException(404, "Bank logo unavailable")
        return Response(
            image.data,
            media_type=image.media_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @router.get("/{plugin_id}/merchant-logos/{key}")
    def merchant_logo(plugin_id: str, key: str):
        image = plugins.merchant_logo_image(plugin_id, key)
        if image is None:
            raise HTTPException(404, "Merchant logo unavailable")
        return Response(
            image.data,
            media_type=image.media_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    if plugin_manager is not None:
        @router.get("/management")
        def management():
            return plugin_manager.projection()

        @router.put("/management")
        def configure(body: PluginSelectionBody):
            return plugin_manager.save(tuple(body.plugins))

    @router.get("/{plugin_id}/export")
    def export(plugin_id: str, workspace: Workspace, month: str | None = None, currency: str = "USD"):
        month = month or current_month()
        try:
            valid_month(month)
        except ValueError as error:
            raise HTTPException(400, "Invalid month") from error
        if not any(p.id == plugin_id and p.has_export for p in plugins.describe()):
            raise HTTPException(404, "Plugin export not found")
        snapshot = build_snapshot(workspace, select_currency(store.workspace(workspace, month), currency))
        table = plugins.export(plugin_id, snapshot)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([safe_cell(column) for column in table.columns])
        for row in table.rows:
            writer.writerow(
                [safe_cell(value) if isinstance(value, str) else value for value in row]
            )
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="whab-{plugin_id}-{workspace}-{month}.csv"'
            },
        )

    return router
