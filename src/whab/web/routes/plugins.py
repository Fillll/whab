"""Read-only extension discovery and downloads; no plugin-owned HTTP routes."""

import csv
import io
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from whab.domain.dates import current_month
from whab.web.csv import safe_cell
from whab.web.plugin_snapshot import build_snapshot
from whab.web.schemas import Workspace, valid_month


def create_router(store, plugins):
    router = APIRouter(prefix="/api/plugins")

    @router.get("")
    def describe():
        return {"plugins": [asdict(plugin) for plugin in plugins.describe()]}

    @router.get("/{plugin_id}/export")
    def export(plugin_id: str, workspace: Workspace, month: str | None = None):
        month = month or current_month()
        try:
            valid_month(month)
        except ValueError as error:
            raise HTTPException(400, "Invalid month") from error
        if not any(p.id == plugin_id and p.has_export for p in plugins.describe()):
            raise HTTPException(404, "Plugin export not found")
        snapshot = build_snapshot(workspace, store.workspace(workspace, month))
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
