"""Validation shared by ledger commands and imported financial records."""

import re
from datetime import date

from .errors import StoreError


def validate_date(value: str) -> None:
    try:
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            or date.fromisoformat(value).isoformat() != value
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise StoreError("Invalid date.") from None


def validate_month(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:19|20|21|22)\d{2}-(?:0[1-9]|1[0-2])", value
    ):
        raise StoreError("Invalid month.")


def validate_label(value: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise StoreError("Invalid name.")
    return value.strip()
