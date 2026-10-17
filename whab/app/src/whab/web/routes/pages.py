"""Server-rendered pages and public connection status."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from whab import __version__
from whab.domain.budget import select_currency
from whab.domain.dates import current_month
from whab.plugins import PluginError
from whab.plugins.sdk import valid_merchant_logo_key
from whab.services.ledger import LedgerService
from whab.web.plugin_snapshot import build_snapshot
from whab.web.schemas import Workspace, valid_month


def create_router(
    store, banks, bank_stores, plugins, templates, environment, ui, plugin_manager=None,
    budget_name="My budget", service=None,
):
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

    service = service if service is not None else LedgerService(store, lock=getattr(store, "lock", None))

    @router.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        workspace: Workspace = "demo",
        page: Literal["budget", "transactions", "insights", "accounts"] = "budget",
        month: str | None = None,
        category: str | None = None,
        currency: str = "USD",
    ):
        month = month or current_month()
        try:
            valid_month(month)
        except ValueError as error:
            raise HTTPException(400, "Invalid month") from error
        if page == "budget" and ui.budget_months > 1:
            periods = [
                select_currency(period, currency)
                for period in service.workspace_periods(workspace, month, ui.budget_months)
            ]
            data = {**periods[0], "periods": periods}
        else:
            data = select_currency(service.workspace(workspace, month), currency)
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
            budget_name=budget_name, budget_id=service.budget_id,
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
        context["bank_source_ids"] = tuple(p.id for p in plugins.bank_plugins())
        context["plugin_scripts"] = [
            f"/plugins/{p.id}/static/connect.js?v={context['version']}"
            for p in plugins.bank_plugins()
            if p.bank_ui and p.id in banks
        ]
        context["import_panels"] = [
            {
                "template": f"imports/{p.id}/accounts.html",
                "id": p.id,
                "display_name": p.display_name,
            }
            for p in plugins.file_import_plugins()
            if p.file_import.templates is not None and workspace == "personal" and page == "accounts"
        ]
        context["plugin_scripts"].extend(
            f"/imports/{panel['id']}/static/import.js?v={context['version']}"
            for panel in context["import_panels"]
        )
        context["plugins"] = plugins.describe()
        # Resolve public institution text only; fetching is deferred to image requests.
        institution_logos = {
            name: plugins.bank_logo(name)
            for name in dict.fromkeys(account.get("institution", "") for account in data["accounts"])
        }
        context["account_logos"] = {
            account["id"]: f"/api/plugins/{logo[0]}/bank-logos/{logo[1]}"
            for account in data["accounts"]
            if (logo := institution_logos.get(account.get("institution", ""))) is not None
        }
        merchant_providers = {
            plugin.id for plugin in plugins.bank_plugins() if plugin.merchant_logos is not None
        }
        context["merchant_logos"] = {
            transaction["id"]: (
                f"/api/plugins/{transaction['source']}/merchant-logos/{transaction['merchantLogo']}"
            )
            for transaction in data["transactions"]
            if transaction.get("source") in merchant_providers
            and valid_merchant_logo_key(transaction.get("merchantLogo"))
        }
        context["plugin_management"] = (
            plugin_manager.projection() if plugin_manager is not None else None
        )
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
