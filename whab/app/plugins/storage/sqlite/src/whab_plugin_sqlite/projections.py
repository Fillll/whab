"""Consecutive month projections from one coherent SQLite ledger snapshot."""

from whab.domain.budget import calculate_workspace_periods
from whab.domain.dates import current_month
from whab.domain.validation import validate_month

from .projection_reads import snapshot


def workspace_periods(store, workspace: str, month: str, count: int) -> list[dict]:
    state = snapshot(store, workspace, month, count)
    validate_month(month)
    as_of_month = current_month()
    return calculate_workspace_periods(state, month, count, as_of_month=as_of_month)
