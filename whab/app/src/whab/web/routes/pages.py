"""Server-rendered pages and public connection status."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from whab import __version__
from whab.domain.budget import select_currency
from whab.domain.dates import current_month
from whab.plugins import PluginError
from whab.plugins.sdk import valid_merchant_logo_key
from whab.services.ledger import LedgerService
from whab.web.navigation import budget_key, page_url
from whab.web.plugin_snapshot import build_snapshot
from whab.web.schemas import valid_month


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

    @router.get("/")
    def landing(request: Request):
        if any(key in request.query_params for key in ("page", "workspace", "month", "currency")):
            raise HTTPException(400, "Open a canonical /budgets/ address.")
        routing = request.scope.get("whab.routing")
        personal_key = routing.route_key(service.budget_id) if routing else budget_key(service.budget_id)
        key = personal_key if service.budget_id != "default" or request.cookies.get("whab-budget") else "demo"
        target = page_url(key)
        if "oauth_state_id" in request.query_params:
            if not any(p.id == "plaid" and p.bank_ui for p in plugins.bank_plugins()):
                raise HTTPException(400, "Bank OAuth continuation is unavailable.")
            # Provider state is opaque and case-sensitive, unlike routing values.
            prefix = "/budgets/" + budget_key(service.budget_id)
            return HTMLResponse(
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                '<title>Continue bank connection</title>'
                '<p id="oauth-status">Returning to the budget that started this connection…</p>'
                f'<script src="{prefix}/plugins/plaid/static/oauth.js"></script></html>'
            )
        return RedirectResponse(target, status_code=303)

    @router.get("/budgets/{key}/{page}/{month_token}/{currency}", response_class=HTMLResponse)
    def index(
        request: Request,
        key: str,
        page: Literal["budget", "transactions", "insights", "accounts"],
        month_token: str,
        currency: str,
        category: str | None = None,
        account: str | None = None,
        all_dates: bool = False,
    ):
        if any(name in request.query_params for name in ("page", "workspace", "month", "currency")):
            raise HTTPException(400, "Routing values belong in the address path.")
        workspace = request.scope.get("whab.workspace", "demo" if key == "demo" else "personal")
        routing = request.scope.get("whab.routing")
        identifier = "demo" if workspace == "demo" else service.budget_id
        key = request.scope.get("whab.budget_key", key)
        month = current_month() if month_token == "current-month" else month_token
        currency = currency.upper()
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
        all_accounts = service.accounts(workspace)
        account_slugs = routing.account_slugs(identifier, workspace, all_accounts) if routing else {}
        account_slug = request.path_params.get("account_slug")
        if account_slug is not None:
            resolved = routing.resolve_account_slug(identifier, workspace, account_slug) if routing else None
            if resolved is None:
                raise HTTPException(404, "Account address not found")
            if account is not None and account != resolved:
                raise HTTPException(400, "Account does not match this address")
            account = resolved
        selected_account = next((row for row in data["accounts"] if row["id"] == account), None)
        if account_slug is not None and selected_account is None:
            raise HTTPException(404, "Account address not found for this currency")
        transaction_all_dates = page == "transactions" and all_dates
        if page == "transactions":
            if transaction_all_dates:
                account_ids = {row["id"] for row in data["accounts"]}
                data = {
                    **data,
                    "transactions": [
                        row for row in service.transactions(workspace)
                        if row["accountId"] in account_ids
                    ],
                }
            if selected_account is not None:
                data = {
                    **data,
                    "transactions": [
                        row for row in data["transactions"] if row["accountId"] == account
                    ],
                }
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
        personal_key = request.scope.get("whab.personal_key", budget_key(service.budget_id))
        asset_prefix = f"/budgets/{'demo' if workspace == 'demo' else budget_key(service.budget_id)}"
        api_prefix = asset_prefix + "/api"

        def url(next_page=page, **overrides):
            target_workspace = overrides.pop("workspace", workspace)
            target_key = key if target_workspace == workspace else ("demo" if target_workspace == "demo" else personal_key)
            target_currency = overrides.pop("currency", currency)
            target_account = overrides.pop("account", None)
            target_slug = account_slugs.get(target_account) if target_workspace == workspace and next_page == "transactions" else None
            if target_account is not None and target_slug is None:
                overrides["account"] = target_account
            return page_url(
                target_key, next_page, overrides.pop("month", month_token),
                target_currency, account_slug=target_slug, **overrides,
            )

        context["url"] = url
        context["asset_prefix"] = asset_prefix
        context["api_prefix"] = api_prefix
        canonical_filters = dict(request.query_params)
        canonical_filters.pop("account", None)
        canonical_url = page_url(
            key, page, month_token, currency,
            account_slug=account_slugs.get(selected_account["id"]) if selected_account and page == "transactions" else None,
            **canonical_filters,
        )
        context.setdefault("client_context", {}).update(
            budgetKey=key, budgetUrl=f"/budgets/{key}", apiPrefix=api_prefix,
            budgetId=identifier, budgetAliases=routing.budget_route_keys(identifier) if routing else [key],
            accountSlugs={row["id"]: account_slugs[row["id"]] for row in data["accounts"] if row["id"] in account_slugs},
            selectedAccountId=selected_account["id"] if selected_account and page == "transactions" else None,
            canonicalUrl=canonical_url,
            personalBudgetKey=personal_key, monthToken=month_token, page=page,
        )
        context.update(
            data=data, status=status, workspace=workspace, page=page, month=month,
            version=__version__,
            budget_name=budget_name, budget_id=service.budget_id,
            selected_account=selected_account if page == "transactions" else None,
            transaction_all_dates=transaction_all_dates,
        )
        from whab.web.bank_status import public_bank_status

        context["bank_panels"] = [
            {
                "template": f"plugins/{p.id}/accounts.html",
                "status": public_bank_status(banks[p.id], environment),
                "items": bank_stores[p.id].items() if workspace == "personal" else [],
            }
            for p in plugins.bank_plugins()
            if p.bank_ui and p.id in banks and workspace == "personal"
        ]
        context["bank_source_ids"] = tuple(p.id for p in plugins.bank_plugins())
        context["plugin_scripts"] = [
            f"{asset_prefix}/plugins/{p.id}/static/connect.js?v={context['version']}"
            for p in plugins.bank_plugins()
            if p.bank_ui and p.id in banks and workspace == "personal"
        ]
        context["import_panels"] = [
            {
                "template": f"imports/{p.id}/accounts.html",
                "id": p.id,
                "display_name": p.display_name,
            }
            for p in plugins.file_import_plugins()
            if p.file_import.templates is not None
        ]
        context["plugin_scripts"].extend(
            f"{asset_prefix}/imports/{panel['id']}/static/import.js?v={context['version']}"
            for panel in context["import_panels"]
        )
        context["plugins"] = plugins.describe()
        # Resolve public institution text only; fetching is deferred to image requests.
        institution_logos = {
            identity: plugins.bank_logo(identity[0], website=identity[1])
            for identity in dict.fromkeys(
                (account.get("institution", ""), account.get("bankWebsite"))
                for account in data["accounts"]
            )
        }
        context["account_logos"] = {
            account["id"]: f"{api_prefix}/plugins/{logo[0]}/bank-logos/{logo[1]}"
            for account in data["accounts"]
            if (logo := institution_logos.get((account.get("institution", ""), account.get("bankWebsite")))) is not None
        }
        context["account_logo_provider"] = plugins.bank_logo_provider()
        merchant_providers = {
            plugin.id for plugin in plugins.bank_plugins() if plugin.merchant_logos is not None
        }
        context["merchant_logos"] = {
            transaction["id"]: (
                f"{api_prefix}/plugins/{transaction['source']}/merchant-logos/{transaction['merchantLogo']}"
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
            response = templates.TemplateResponse(request=request, name="base.html", context=context)
            if page == "transactions":
                response.headers["Vary"] = "X-WHAB-Transactions-Fragment"
                if request.headers.get("X-WHAB-Transactions-Fragment") == "1":
                    response.headers["X-WHAB-Transactions-Fragment"] = "1"
                    response.headers["Cache-Control"] = "no-store"
            return response
        except Exception:
            raise PluginError(
                "The selected user interface could not render this page.", code="ui_render_failed"
            ) from None

    @router.get("/budgets/{key}/{account_slug}/transactions/{month_token}/{currency}", response_class=HTMLResponse)
    def account_index(
        request: Request, key: str, account_slug: str, month_token: str, currency: str,
        category: str | None = None, account: str | None = None, all_dates: bool = False,
    ):
        return index(request, key, "transactions", month_token, currency,
                     category=category, account=account, all_dates=all_dates)

    return router
