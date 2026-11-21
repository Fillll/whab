"""Resolve visual Insight controls against the selected budget and currency."""

from dataclasses import replace

from whab.domain.dates import shift_month
from whab.plugins.sdk import MAX_INSIGHT_MONTHS, _insight_month
from whab.services.ledger import InsightRangeTooLarge

_DATE_CONTROLS = ("insight_from", "insight_to", "insight_group")
_FILTERS = (
    ("insight_account", "account_id", "accounts", "an account"),
    ("insight_category_group", "category_group_id", "category_groups", "a category group"),
    ("insight_category", "category_id", "categories", "a category"),
)


def _choices(snapshot):
    def by_label(row):
        return row["label"].casefold(), row["id"]

    accounts = [{"id": row.id, "label": f"{row.name} (closed)" if row.closed else row.name}
                for row in snapshot.accounts if row.on_budget]
    groups = {}
    categories = []
    for row in snapshot.categories:
        if row.kind != "expense":
            continue
        group_id = row.parent_id
        groups[group_id] = {"id": group_id, "label": row.group}
        categories.append({"id": "category:" + row.id, "label": f"{row.group} / {row.name}", "group_id": group_id})
    return {
        "accounts": sorted(accounts, key=by_label),
        "category_groups": sorted(groups.values(), key=by_label),
        "categories": sorted(categories, key=by_label) + [{"id": "uncategorized", "label": "Uncategorized", "group_id": ""}],
    }


def visual_insight_snapshot(snapshot, query, service):
    default_start = max("1900-01", shift_month(snapshot.month, -5))
    options = {"start_month": default_start, "end_month": snapshot.month, "group_by": "group", **_choices(snapshot)}
    start = query.get("insight_from", default_start)
    end = query.get("insight_to", snapshot.month)
    group = query.get("insight_group", "group")
    invalid_filter = None
    for parameter, field, choices, label in _FILTERS:
        selected = query.get(parameter, "")
        valid = not selected or any(row["id"] == selected for row in options[choices])
        options[field] = selected if valid else ""
        if not valid and invalid_filter is None:
            invalid_filter = label
    try:
        if any(len(query.getlist(key)) > 1 for key in _DATE_CONTROLS):
            raise ValueError
        if not 0 <= _insight_month(end) - _insight_month(start) < MAX_INSIGHT_MONTHS or group not in {"category", "group"}:
            raise ValueError
    except ValueError:
        options["error"] = f"Choose an ordered range of 1 to {MAX_INSIGHT_MONTHS} months and a breakdown."
        return snapshot, options
    options.update(start_month=start, end_month=end, group_by=group)
    if any(len(query.getlist(key)) > 1 for key, _, _, _ in _FILTERS):
        options["error"] = "Choose a single account, category group, and category."
        return snapshot, options
    if invalid_filter is not None:
        options["error"] = f"Choose {invalid_filter} available for this budget and currency."
        return snapshot, options

    filters = {}
    if options["account_id"]:
        filters["account_id"] = options["account_id"]
    if options["category_group_id"]:
        filters["group_id"] = options["category_group_id"]
    if choice := options["category_id"]:
        filters["category_id"] = None if choice == "uncategorized" else choice.removeprefix("category:")
    try:
        spending = service.insight_spending(snapshot.workspace, start, end, snapshot.currency, **filters)
        return replace(snapshot, spending=spending, spending_by=group), options
    except InsightRangeTooLarge as error:
        options["error"] = str(error)
    except ValueError:
        options["error"] = "Monthly spending is unavailable for this range. Try fewer months or refresh Insights."
    return snapshot, options
