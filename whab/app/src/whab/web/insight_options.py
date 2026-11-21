"""Resolve visual Insight controls against the selected budget and currency."""

from dataclasses import replace

from whab.domain.dates import shift_month
from whab.domain.errors import StoreError
from whab.plugins.sdk import MAX_INSIGHT_MONTHS, _insight_month
from whab.plugins.ui_render import MAX_UI_QUERY_FIELDS
from whab.services.ledger import InsightRangeTooLarge

_DATE_CONTROLS = ("insight_from", "insight_to", "insight_group")
_FILTERS = (
    ("insight_account", "account_ids", "accounts", "accounts"),
    ("insight_category_group", "category_group_ids", "category_groups", "category groups"),
    ("insight_category", "category_ids", "categories", "categories"),
)


def insight_selections(query):
    """Keep each exact selection once and treat legacy blank values as absent."""
    if len(query.multi_items()) > MAX_UI_QUERY_FIELDS:
        raise StoreError("Too many choices in this Monthly spending link. Reduce the selected accounts, groups or categories.")
    return {parameter: list(dict.fromkeys(value for value in query.getlist(parameter) if value))
            for parameter, _, _, _ in _FILTERS}


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
    options = {"start_month": default_start, "end_month": snapshot.month, "group_by": "group",
               "max_fields": MAX_UI_QUERY_FIELDS, **_choices(snapshot)}
    start = query.get("insight_from", default_start)
    end = query.get("insight_to", snapshot.month)
    group = query.get("insight_group", "group")
    invalid_filter = None
    selections = insight_selections(query)
    for parameter, field, choices, label in _FILTERS:
        selected = selections[parameter]
        allowed = {row["id"] for row in options[choices]}
        valid = [value for value in selected if value in allowed]
        options[field] = valid
        # Retain the selected label for older Web versions using single-value controls.
        options[field.removesuffix("s")] = valid[0] if len(valid) == 1 else ""
        if len(valid) != len(selected) and invalid_filter is None:
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
    if invalid_filter is not None:
        options["error"] = f"Choose {invalid_filter} available for this budget and currency."
        return snapshot, options

    category_ids = tuple(None if choice == "uncategorized" else choice.removeprefix("category:")
                         for choice in options["category_ids"])
    try:
        spending = service.insight_spending(snapshot.workspace, start, end, snapshot.currency,
                                           account_ids=options["account_ids"], group_ids=options["category_group_ids"],
                                           category_ids=category_ids)
        return replace(snapshot, spending=spending, spending_by=group), options
    except InsightRangeTooLarge as error:
        options["error"] = str(error)
    except ValueError:
        options["error"] = "Monthly spending is unavailable for this range. Try fewer months or refresh Insights."
    return snapshot, options
