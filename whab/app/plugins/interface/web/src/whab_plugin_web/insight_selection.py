"""Select one report from the Insights gallery and its existing filtered links."""

from collections.abc import Mapping

_BUILTIN_REPORTS = {"overview", "categories", "payees"}
_MONTHLY_FILTERS = {
    "insight_from", "insight_to", "insight_group",
    "insight_account", "insight_category_group", "insight_category",
}


def _field(record, name):
    return record[name] if isinstance(record, Mapping) else getattr(record, name)


def _has_visual(plugin):
    return any(_field(row, "name") == "reporting.insights" and _field(row, "version") in (2, 3)
               for row in _field(plugin, "capabilities"))


def insight_selection(query, plugins, results):
    """Resolve a Web selection against this budget's enabled report descriptors."""
    selection = {"report": "gallery", "result": None, "visual": False}
    requested = query.getlist("insight_report")
    if len(requested) > 1:
        return {**selection, "report": "unavailable"}
    report = requested[0] if requested else None
    if report in _BUILTIN_REPORTS:
        return {**selection, "report": report}
    if report is None and not _MONTHLY_FILTERS.intersection(query):
        return selection

    enabled = {_field(plugin, "id"): plugin for plugin in plugins if _field(plugin, "has_insights")}
    if report is None:
        visual = [identifier for identifier, plugin in enabled.items() if _has_visual(plugin)]
        if not visual:
            return selection
        identifier = "monthly-spending" if "monthly-spending" in visual else visual[0]
        report = f"plugin:{identifier}"
    if not report.startswith("plugin:") or (identifier := report.removeprefix("plugin:")) not in enabled:
        return {**selection, "report": "unavailable"}
    return {
        "report": report,
        "result": next((result for result in results if _field(result, "plugin_id") == identifier), None),
        "visual": _has_visual(enabled[identifier]),
    }
