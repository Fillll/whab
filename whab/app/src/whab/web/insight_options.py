"""Parse a visual Insight's bounded read-only date and grouping controls."""

from dataclasses import replace

from whab.domain.dates import shift_month
from whab.domain.errors import StoreError
from whab.plugins.sdk import MAX_INSIGHT_MONTHS, _insight_month
from whab.services.ledger import InsightRangeTooLarge


def visual_insight_snapshot(snapshot, query, service):
    default_start = max("1900-01", shift_month(snapshot.month, -5))
    defaults = {"start_month": default_start, "end_month": snapshot.month, "group_by": "group"}
    start = query.get("insight_from", default_start)
    end = query.get("insight_to", snapshot.month)
    group = query.get("insight_group", "group")
    try:
        if any(len(query.getlist(key)) > 1 for key in ("insight_from", "insight_to", "insight_group")):
            raise ValueError
        if not 0 <= _insight_month(end) - _insight_month(start) < MAX_INSIGHT_MONTHS or group not in {"category", "group"}:
            raise ValueError
    except ValueError:
        return snapshot, {**defaults, "error": f"Choose an ordered range of 1 to {MAX_INSIGHT_MONTHS} months and a breakdown."}
    options = {"start_month": start, "end_month": end, "group_by": group}
    try:
        spending = service.insight_spending(snapshot.workspace, start, end, snapshot.currency)
        return replace(snapshot, spending=spending, spending_by=group), options
    except InsightRangeTooLarge as error:
        options["error"] = str(error)
    except (ValueError, StoreError):
        options["error"] = "Monthly spending is unavailable for this range. Try fewer months or refresh Insights."
    return snapshot, options
