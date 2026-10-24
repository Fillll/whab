"""Public visual declarations and fictional native HTTPS conformance checks."""

import re

from whab import plugins as sdk

MINIMUM_UI_HOST = "2.36.0"
_FLAGS = ("budget_months", "lightweight_transactions", "lightweight_accounts", "transaction_page_size", "view_protocol")
_PAGES = ("budget", "transactions", "accounts", "insights")


def visual_declaration(plugin):
    """Validate the explicit public visual adapter without executing its helper."""
    ui = getattr(plugin, "ui", None)
    if type(ui) is not sdk.UserInterface or getattr(ui, "view_protocol", None) != 1:
        raise ValueError("Use a participating visual interface on WHAB 2.36 or newer.")
    flags = {name: getattr(ui, name) for name in _FLAGS}
    sdk.UserInterface(ui.templates, ui.static, ui.build_context, resources=ui.resources, **flags)
    contract = getattr(plugin, "contract", None)
    if (ui.render is not None or contract is None or contract.lifecycle != "live"
            or contract.state_version != 1 or contract.state_upgrades
            or any(getattr(plugin, name, None) is not None for name in (
                "bind", "settings", "migrate_state", "budget_connection", "categorization", "bank", "bank_ui", "storage",
                "interface", "bank_logos", "merchant_logos", "branding_factory", "file_import", "insights", "export"))):
        raise ValueError("A visual worker uses the host's data-only render adapter.")
    return flags


def visual_change(runtime, plugin_id, *, wheels=None, dirty=False):
    """Preview/apply through public controls, acknowledging only this proposal.

    A synthetic dirty view holds the transition before admission is gated. Its
    opaque acknowledgement carries no fictional draft or financial payload.
    """
    active = runtime.expect("GET", "/api/ui/status")["active"]
    generation = active["resource_generation"]
    view = None
    if dirty:
        view = runtime.expect("POST", "/api/ui/views", {"generation": generation, "dirty": True}, status=201)["view"]
    try:
        preview = runtime.expect("POST", "/api/ui/preview", {"pluginId": plugin_id, "wheels": wheels or []})["preview_id"]
        ticket = runtime.expect("POST", "/api/ui/commit", {"previewId": preview}, status=202)["id"]
        if runtime.expect("POST", "/api/ui/commit", {"previewId": preview}, status=202)["id"] != ticket:
            raise AssertionError("An exact visual commit retry changed its operation identity.")
        if view is not None:
            def preparing():
                value = runtime.expect("GET", "/api/ui/status")
                operation = next((row for row in value.get("operations", []) if row["id"] == ticket), None)
                if operation is not None and operation["state"] == "failed":
                    raise AssertionError("Visual candidate preparation failed; inspect its recorded public operation status.")
                return value if value["browser_preparation"] else None
            status = runtime.wait(preparing, timeout=runtime.transition_timeout)
            if status["active"] != active or status["runtime"]["admission"] != "open":
                raise AssertionError("A dirty view did not defer visual handover before the financial gate.")
            runtime.expect("PUT", "/api/ui/views/" + view, {"generation": generation, "dirty": False,
                           "prepared": status["browser_preparation"]["id"]})
        operation = runtime.operation("/api/ui/status", ticket)
        if operation["preview_id"] != preview:
            raise AssertionError("A visual operation acknowledged another preview.")
        current = runtime.expect("GET", "/api/ui/status")["active"]
        if current["plugin_id"] != plugin_id:
            raise AssertionError("The visual selection did not publish its acknowledged choice.")
        return current
    finally:
        if view is not None:
            runtime.expect("DELETE", "/api/ui/views/" + view, status=204)


def assert_visual_interface(runtime, plugin, *, asset_name=None):
    """Verify coherent public pages, selected assets and local access guards.

    Authors may name one of their own static assets. The test uses only native
    HTTPS responses, never a host router, renderer or concrete backend import.
    """
    visual_declaration(plugin)
    active = runtime.expect("GET", "/api/ui/status")["active"]
    generation = active["resource_generation"]
    if active["plugin_id"] != plugin.id or re.fullmatch(r"[a-f0-9]{64}", generation) is None:
        raise AssertionError("The runtime did not select this coherent visual generation.")
    runtime.expect("GET", "/api/budget", headers={"Host": "fictional.remote.example"}, status=403)
    runtime.expect("POST", "/api/ui/preview", {"pluginId": None}, headers={"Origin": "https://fictional.remote.example"}, status=403)
    runtime.expect("POST", "/budgets/demo/api/ui/preview", {"pluginId": None}, status=403)
    for page in _PAGES:
        status, headers, body = runtime.get_resource(f"/budgets/home/{page}/current-month/usd")
        if status != 200 or headers.get("x-whab-ui-generation") != generation or not body.strip():
            raise AssertionError("A visual page did not preserve the selected generation.")
    evidence = {"pages": list(_PAGES), "generation": generation, "guards": "passed", "asset": "not-applicable: no asset selected by author"}
    if asset_name is not None:
        if type(asset_name) is not str or not asset_name or any(part in {"", ".", ".."} for part in asset_name.split("/")) or re.fullmatch(r"[a-zA-Z0-9_./-]+", asset_name) is None:
            raise ValueError("Choose a relative public asset owned by this visual plugin.")
        status, headers, body = runtime.get_resource(f"/budgets/home/ui/{generation}/static/{asset_name}")
        if status != 200 or "immutable" not in headers.get("cache-control", "") or not body:
            raise AssertionError("A selected visual asset was unavailable or mutable.")
        evidence["asset"] = asset_name
    return evidence
