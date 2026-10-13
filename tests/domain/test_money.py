"""Money contracts reject coercion and preserve signed cent precision."""

import pytest

from whab.domain.errors import StoreError
from whab.domain.money import MAX_CENTS, cents, dollars_to_cents


@pytest.mark.parametrize("value", [True, 1.5, "100", MAX_CENTS + 1, -MAX_CENTS - 1])
def test_stored_money_rejects_coercion_and_out_of_range_values(value):
    with pytest.raises(StoreError, match="integer USD cents"):
        cents(value)


@pytest.mark.parametrize(
    ("dollars", "expected"), [("12.345", 1235), ("-12.345", -1235), ("0.01", 1)]
)
def test_bank_dollars_round_exactly_to_signed_cents(dollars, expected):
    assert dollars_to_cents(dollars) == expected


@pytest.mark.parametrize("value", [True, float("inf"), "NaN"])
def test_nonfinite_and_boolean_bank_amounts_are_rejected(value):
    with pytest.raises(StoreError):
        dollars_to_cents(value)
