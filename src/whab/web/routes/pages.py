"""Server-rendered pages and public connection status."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from whab import __version__
from whab.domain.dates import current_month
from whab.plugins import PluginError
from whab.web.plugin_snapshot import build_snapshot
from whab.web.schemas import Workspace, valid_month


def create_router(store, banks, bank_stores, plugins, templates, environment, ui):
    router = APIRouter()

    def public_status():
        # Preserve the original status response without constructing a disabled plugin.
        from whab.web.bank_status import public_bank_status

        bank = banks.get("plaid")
        items = bank_stores["plaid"].items() if bank else []
        status = public_bank_status(bank, environment)
        return {**status, "plaidConfigured": status["configured"], "connectedItems": len(items)}

    @router.get("/api/status")
    def status():
        return public_status()

    if ui is None:
        return router

    @router.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        workspace: Workspace = "demo",
        page: Literal["budget", "transactions", "insights", "accounts"] = "budget",
        month: str | None = None,
        category: str | None = None,
    ):
        month = month or current_month()
        try:
            valid_month(month)
        except ValueError as error:
            raise HTTPException(400, "Invalid month") from error
        data = store.workspace(workspace, month)
        status = public_status()
        try:
            context = ui.build_context(
                data,
                status,
                [],
                workspace=workspace,
                page=page,
                month=month,
                selected=category,
            )
            if type(context) is not dict:
                raise ValueError("UI context must be a dictionary")
        except Exception:
            raise PluginError(
                "The selected user interface could not render this page.", code="ui_render_failed"
            ) from None
        context.update(
            data=data, status=status, workspace=workspace, page=page, month=month,
            version=__version__,
        )
        from whab.web.bank_status import public_bank_status

        context["bank_panels"] = [
            {
                "template": f"plugins/{p.id}/accounts.html",
                "status": public_bank_status(banks[p.id], environment),
                "items": bank_stores[p.id].items() if workspace == "personal" else [],
            }
            for p in plugins.bank_plugins()
            if p.bank_ui and p.id in banks
        ]
        context["plugin_scripts"] = [
            f"/plugins/{p.id}/static/connect.js?v={context['version']}"
            for p in plugins.bank_plugins()
            if p.bank_ui and p.id in banks
        ]
        context["plugins"] = plugins.describe()
        context["plugin_insights"] = (
            plugins.insights(build_snapshot(workspace, data))
            if page == "insights" and any(p.has_insights for p in context["plugins"])
            else ()
        )
        try:
            return templates.TemplateResponse(request=request, name="base.html", context=context)
        except Exception:
            raise PluginError(
                "The selected user interface could not render this page.", code="ui_render_failed"
            ) from None

    return router
