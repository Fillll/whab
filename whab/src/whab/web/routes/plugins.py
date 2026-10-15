"""Host-owned discovery, restart-bound settings and downloads; no plugin HTTP routes."""

import csv
import io
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from whab.domain.budget import select_currency
from whab.domain.dates import current_month
from whab.plugins import PluginError
from whab.web.csv import safe_cell
from whab.web.plugin_snapshot import build_snapshot
from whab.web.schemas import PluginSelectionBody, Workspace, valid_month


def create_router(store, plugins, plugin_manager=None):
    router = APIRouter(prefix="/api/plugins")

    @router.get("")
    def describe():
        return {"plugins": [asdict(plugin) for plugin in plugins.describe()]}

    def settings_data(plugin_id):
        try:
            settings = plugins.settings(plugin_id)
        except PluginError as error:
            if error.code == "unavailable":
                raise HTTPException(404, "Plugin settings not found") from None
            raise
        return {key: [asdict(record) for record in records] for key, records in settings.items()}

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
