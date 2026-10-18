"""Strict calendar validation accepts only complete ISO calendar dates."""

from datetime import date, timedelta

import pytest

from whab.domain.errors import StoreError
from whab.domain.validation import validate_date


def test_calendar_dates_across_a_leap_century():
    first = date(1999, 1, 1)
    for offset in range(1461):
        assert validate_date((first + timedelta(days=offset)).isoformat()) is None
    assert validate_date("0001-01-01") is None
    assert validate_date("9999-12-31") is None


@pytest.mark.parametrize("value", [
    "1900-02-29", "2025-02-29", "2026-04-31", "0000-01-01", "2026-00-01", "2026-13-01",
    "2026-01-00", "2026-01-32", "2026-1-01", "2026-01-1", "20261001", "2026-W40-4",
    "2026-274", "2026-10-01T00:00:00", "2026-10-01\n", " 2026-10-01", "٢٠٢٦-١٠-٠١",
    "２０２６-１０-０１", "", None, 20261001, True, b"2026-10-01", [], {},
])
def test_invalid_calendar_dates(value):
    with pytest.raises(StoreError, match="^Invalid date\\.$"):
        validate_date(value)
