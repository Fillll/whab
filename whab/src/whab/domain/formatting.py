"""Core's fixed presentation defaults, independent of locale and UI plugins."""

from datetime import date

from .money import format_money as format_money
from .validation import validate_date


def format_date(value: str | date) -> str:
    """Return a calendar date in YYYY-MM-DD, rejecting ambiguous input."""
    if type(value) is date:
        return value.isoformat()
    validate_date(value)
    return value
