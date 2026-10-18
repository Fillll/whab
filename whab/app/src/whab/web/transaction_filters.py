"""Browser register periods over inclusive household-local calendar dates."""

from calendar import month_name, monthrange
from dataclasses import dataclass

from whab.domain.dates import current_month, shift_month
from whab.domain.validation import validate_date


@dataclass(frozen=True)
class TransactionPeriod:
    selection: str
    start: str | None
    end: str | None
    month: str
    query_month: str | None
    label: str
    count_label: str


def transaction_period(month, month_token, selection, start, end, all_dates):
    """Keep old month/all-date bookmarks while supporting explicit period filters."""
    legacy_month = selection is None and not all_dates and not start and not end
    if selection is None:
        selection = "all" if all_dates else "custom" if start or end or month_token != "current-month" else "current-month"
    if selection not in {"current-month", "previous-month", "custom", "all"}:
        raise ValueError("Invalid transaction period.")
    if selection in {"current-month", "previous-month"}:
        month = current_month() if selection == "current-month" else shift_month(current_month(), -1)
    if selection in {"current-month", "previous-month"} or legacy_month:
        year, number = map(int, month.split("-"))
        start, end = month + "-01", f"{month}-{monthrange(year, number)[1]:02d}"
    elif selection == "all":
        start = end = None
    for value in (start, end):
        if value is not None:
            validate_date(value)
    if start is not None and end is not None and start > end:
        raise ValueError("Start date must not be after end date.")
    query_month = None
    if start and end and start[:7] == end[:7]:
        year, number = map(int, start[:7].split("-"))
        if start.endswith("-01") and int(end[-2:]) == monthrange(year, number)[1]:
            query_month = start[:7]
    if query_month:
        label = f"{month_name[int(query_month[-2:])]} {query_month[:4]}"
    elif start and end:
        label = f"{start} – {end}"
    elif start:
        label = f"From {start}"
    elif end:
        label = f"Through {end}"
    else:
        label = "Custom dates"
    return TransactionPeriod(selection, start, end, month, query_month, label,
                             label if start or end else "All dates")
