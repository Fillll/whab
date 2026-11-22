"""Resolve visual Insight controls against the selected budget and currency."""

from dataclasses import asdict, replace
from json import dumps

from whab.domain.dates import shift_month
from whab.domain.errors import StoreError
from whab.plugins.sdk import MAX_INSIGHT_MONTHS, SpendingMonth, _insight_month
from whab.plugins.ui_render import MAX_UI_QUERY_FIELDS
from whab.plugins.wire import MAX_METADATA_BYTES
from whab.services.ledger import InsightRangeTooLarge

_DATE_CONTROLS = ("insight_from", "insight_to", "insight_group")
_FILTERS = (
    ("insight_account", "account_ids", "accounts", "accounts"),
    ("insight_category_group", "category_group_ids", "category_groups", "category groups"),
    ("insight_category", "category_ids", "categories", "categories"),
)


def insight_selections(query):
    """Keep exact selections once and preserve explicit-empty flags for validation."""
    if len(query.multi_items()) > MAX_UI_QUERY_FIELDS:
        raise StoreError("Too many choices in this Monthly spending link. Reduce the selected accounts, groups or categories.")
    selections = {}
    for parameter, _, _, _ in _FILTERS:
        selections[parameter] = list(dict.fromkeys(value for value in query.getlist(parameter) if value))
        selections[parameter + "_none"] = query.getlist(parameter + "_none")
    return selections


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


def visual_insight_snapshot(snapshot, query, service, *, include_contributors=False):
    default_start = max("1900-01", shift_month(snapshot.month, -5))
    options = {"start_month": default_start, "end_month": snapshot.month, "group_by": "group",
               "max_fields": MAX_UI_QUERY_FIELDS, "explicit_empty_supported": True, **_choices(snapshot)}
    start = query.get("insight_from", default_start)
    end = query.get("insight_to", snapshot.month)
    group = query.get("insight_group", "group")
    filter_error = None
    selections = insight_selections(query)
    for parameter, field, choices, label in _FILTERS:
        selected = selections[parameter]
        allowed = {row["id"] for row in options[choices]}
        valid = [value for value in selected if value in allowed]
        options[field] = valid
        # Retain the selected label for older Web versions using single-value controls.
        options[field.removesuffix("s")] = valid[0] if len(valid) == 1 else ""
        marker = selections[parameter + "_none"]
        empty = marker == ["1"] and not selected
        options[parameter.removeprefix("insight_") + "_none"] = empty
        if filter_error is None:
            if marker and not empty:
                filter_error = f"Choose values or deselect all for {label}."
            elif len(valid) != len(selected):
                filter_error = f"Choose {label} available for this budget and currency."
    try:
        if any(len(query.getlist(key)) > 1 for key in _DATE_CONTROLS):
            raise ValueError
        months = _insight_month(end) - _insight_month(start) + 1
        if not 1 <= months <= MAX_INSIGHT_MONTHS or group not in {"category", "group"}:
            raise ValueError
    except ValueError:
        options["error"] = f"Choose an ordered range of 1 to {MAX_INSIGHT_MONTHS} months and a breakdown."
        return snapshot, options
    options.update(start_month=start, end_month=end, group_by=group)
    if filter_error is not None:
        options["error"] = filter_error
        return snapshot, options
    if options["account_none"] or options["category_group_none"] or options["category_none"]:
        spending = tuple(SpendingMonth(shift_month(start, offset), 0, ()) for offset in range(months))
        return replace(snapshot, spending=spending, spending_by=group), options

    category_ids = tuple(None if choice == "uncategorized" else choice.removeprefix("category:")
                         for choice in options["category_ids"])
    try:
        spending = service.insight_spending(snapshot.workspace, start, end, snapshot.currency,
                                           account_ids=options["account_ids"], group_ids=options["category_group_ids"],
                                           category_ids=category_ids,
                                           **({"include_contributors": True} if include_contributors else {}))
        if include_contributors:
            # Leave space in the worker frames for the ordinary report snapshot and page.
            contributor_bytes = sum(
                len(dumps(asdict(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
                for month in spending for row in month.contributors
            )
            if contributor_bytes > MAX_METADATA_BYTES // 2:
                raise InsightRangeTooLarge(
                    "Too much transaction detail in this range. Choose fewer months or narrow the filters."
                )
        return replace(snapshot, spending=spending, spending_by=group), options
    except InsightRangeTooLarge as error:
        options["error"] = str(error)
    except ValueError:
        options["error"] = "Monthly spending is unavailable for this range. Try fewer months or refresh Insights."
    return snapshot, options
