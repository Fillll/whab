"""Consecutive month projections from one coherent SQLite ledger snapshot."""

from whab.domain.budget import calculate_workspace
from whab.domain.dates import current_month, shift_month
from whab.domain.validation import validate_month


def workspace_periods(store, workspace: str, month: str, count: int) -> list[dict]:
    state = store.state(workspace)
    validate_month(month)
    as_of_month = current_month()
    return [
        calculate_workspace(state, shift_month(month, offset), as_of_month=as_of_month)
        for offset in range(count)
    ]
