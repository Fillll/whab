"""Default presentation uses one canonical date format in every locale."""

from datetime import date, datetime

import pytest

from whab.domain.errors import StoreError
from whab.domain.formatting import format_date, format_money


@pytest.mark.parametrize("value", ["2026-10-14", "2024-02-29", "0001-01-01", "9999-12-31"])
def test_dates_have_fixed_iso_format(value):
    assert format_date(value) == value


def test_calendar_date_objects_use_fixed_iso_format():
    assert format_date(date(2026, 1, 2)) == "2026-01-02"


@pytest.mark.parametrize(
    "value",
    [
        None, True, 20261014, "10/14/2026", "14/10/2026", "2026-1-2", "2026-02-29",
        "2026-10-14T12:30:00", "2026-10-14 ", " 2026-10-14", datetime(2026, 10, 14, 12, 30),
    ],
)
def test_noncanonical_dates_are_rejected(value):
    with pytest.raises(StoreError, match="Invalid date"):
        format_date(value)


def test_formatting_module_exposes_the_core_money_default():
    assert format_money(123456) == "$1,234.56"
