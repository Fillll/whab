"""Household-local dates and month arithmetic."""

from datetime import datetime
from zoneinfo import ZoneInfo


def today() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def current_month() -> str:
    return today()[:7]


def shift_month(month: str, offset: int) -> str:
    year, number = map(int, month.split("-"))
    serial = year * 12 + number - 1 + offset
    return f"{serial // 12:04d}-{serial % 12 + 1:02d}"
