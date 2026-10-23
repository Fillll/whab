"""Data-only visual worker operations; helpers and Jinja stay in the child."""

import hashlib
from dataclasses import replace

from .contracts import contract_for
from .sdk import UserInterface
from .ui_render import read_arguments, render_page
from .wire import WireError
from .worker_resources import capture_resource_paths, resource_bundle

OPERATIONS = frozenset({"ui.resources", "ui.render"})
MAX_HTML_BYTES = 16 * 1024 * 1024
_FLAGS = frozenset({"budget_months", "lightweight_transactions", "lightweight_accounts", "transaction_page_size", "view_protocol"})


def supports_live_visual(plugin):
    contract = contract_for(plugin)
    return (
        plugin.contract is not None and contract.lifecycle == "live"
        and [(row.name, row.version) for row in contract.provides] == [("interface.ui", 1)]
        and contract.state_version == 1 and not contract.state_upgrades
        and plugin.ui is not None and plugin.ui.view_protocol == 1
        and all(getattr(plugin, name) is None for name in (
            "bind", "settings", "migrate_state", "budget_connection", "categorization",
            "bank", "bank_ui", "storage", "interface", "bank_logos", "merchant_logos",
            "branding_factory", "file_import", "insights", "export",
        ))
    )


def adapter_info(ui):
    return {name: getattr(ui, name) for name in sorted(_FLAGS)} if ui is not None else None


def validate_adapter_info(value):
    if type(value) is not dict or value.keys() != _FLAGS:
        raise WireError()
    # Keep SDK projection limits authoritative, without touching any directory.
    from pathlib import Path

    UserInterface(Path("templates"), Path("static"), lambda *args, **kw: {}, **value)
    return dict(value)


def snapshot_ui(ui):
    """Render from exactly the immutable bytes advertised by this worker."""
    resources = ui.resources or capture_resource_paths(ui.templates, ui.static)
    return replace(ui, templates=None, static=None, resources=resources)


def proxy_context(data, status, items, **values):
    """The parent collects inputs; it never invokes the child's context helper."""
    return {"_ui_inputs": {"data": data, "status": status, "items": items, **values}, "client_context": {}}


def html_result(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_HTML_BYTES:
        raise WireError()
    text = raw.decode("utf-8")
    if "\x00" in text:
        raise WireError()
    return {"format": "html-utf8-v1", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, raw


def read_html(metadata, payload):
    expected, raw = html_result(payload)
    if type(metadata) is not dict or metadata != expected or type(metadata.get("bytes")) is not int:
        raise WireError()
    return raw


def dispatch(ui, operation, arguments, payload):
    if ui is None or type(arguments) is not dict or payload:
        raise WireError()
    if operation == "ui.resources":
        if arguments or ui.resources is None:
            raise WireError()
        return resource_bundle(ui.resources)
    if operation != "ui.render":
        raise WireError()
    return html_result(render_page(ui, read_arguments(arguments)))


def readiness_arguments(page):
    """A fictional full core projection, with no storage/backend dependency."""
    from whab.domain.budget import calculate_workspace, select_currency
    from whab.domain.ledger_state import initial_state

    data = select_currency(calculate_workspace(initial_state("personal", "2000-01"), "2000-01"), "USD")
    context = {
        "data": data, "status": {"configured": False, "environment": "sandbox", "plaidConfigured": False, "connectedItems": 0},
        "workspace": "personal", "page": page, "month": "2000-01", "version": "readiness",
        "budget_name": "Fictional readiness budget", "budget_id": "readiness",
        "asset_prefix": "/budgets/readiness", "ui_asset_prefix": "/budgets/readiness/ui/" + "0" * 64,
        "api_prefix": "/budgets/readiness/api", "category_suggestions": {},
        "selected_account": None, "transaction_all_dates": False, "selected_account_ids": [],
        "transaction_period": None, "transaction_start_date": None, "transaction_end_date": None,
        "transaction_period_label": "January 2000", "transaction_count_period": "January 2000",
        "transaction_review_filter": "all", "bank_panels": [], "bank_source_ids": [],
        "plugin_scripts": [], "import_panels": [], "plugins": [], "account_logos": {},
        "account_logo_provider": None, "merchant_logos": {}, "plugin_management": None,
        "plugin_insights": [], "client_context": {"uiGeneration": "0" * 64},
    }
    return read_arguments({
        "input": {"data": data, "status": context["status"], "items": [], "workspace": "personal",
                  "page": page, "month": "2000-01", "selected": None},
        "context": context,
        "routing": {"key": "readiness", "personal_key": "readiness", "workspace": "personal", "page": page,
                    "month_token": "2000-01", "currency": "USD", "account_slugs": {}},
        "request": {"path": f"/budgets/readiness/{page}/2000-01/usd", "query": [], "headers": {}},
        "templates": {},
    })
