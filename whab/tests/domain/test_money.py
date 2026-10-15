"""Money contracts reject coercion and preserve signed cent precision."""

from decimal import Decimal, localcontext

import pytest

from whab.domain.errors import StoreError
from whab.domain.money import (
    MAX_CENTS,
    MAX_MINOR_UNITS,
    MAX_SAFE_INTEGER,
    SUPPORTED_CURRENCIES,
    amount_to_minor_units,
    cents,
    currency_code,
    currency_minor_units,
    dollars_to_cents,
    format_money,
    minor_units,
)


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


@pytest.mark.parametrize(
    ("currency", "places"),
    [("USD", 2), ("CAD", 2), ("JPY", 0), ("KWD", 3), ("CLF", 4), ("XCG", 2), ("ZWG", 2)],
)
def test_currency_metadata_uses_iso_precision(currency, places):
    assert currency_code(currency) == currency
    assert currency_minor_units(currency) == places


def test_supported_currencies_are_complete_and_deterministic():
    assert isinstance(SUPPORTED_CURRENCIES, tuple)
    assert SUPPORTED_CURRENCIES == tuple(sorted(set(SUPPORTED_CURRENCIES)))
    assert len(SUPPORTED_CURRENCIES) == 165
    assert {places: sum(currency_minor_units(code) == places for code in SUPPORTED_CURRENCIES)
            for places in (0, 2, 3, 4)} == {0: 17, 2: 139, 3: 7, 4: 2}


@pytest.mark.parametrize(
    "value", [None, True, 840, [], {}, "", "usd", " USD", "USD ", "BTC", "XXX", "XAU", "BGN"]
)
def test_currency_codes_reject_coercion_unknown_and_undefined_precision(value):
    with pytest.raises(StoreError, match="ISO 4217"):
        currency_code(value)
    with pytest.raises(StoreError, match="ISO 4217"):
        currency_minor_units(value)
    with pytest.raises(StoreError, match="ISO 4217"):
        amount_to_minor_units("1", value)


def test_currency_codes_reject_custom_string_subclasses():
    class Currency(str):
        pass

    with pytest.raises(StoreError, match="ISO 4217"):
        currency_code(Currency("USD"))


@pytest.mark.parametrize("currency", ["USD", "EUR", "JPY", "KWD"])
@pytest.mark.parametrize("value", [0, 1, -1, MAX_MINOR_UNITS, -MAX_MINOR_UNITS])
def test_minor_units_preserve_integer_values(currency, value):
    assert minor_units(value, currency) == value


@pytest.mark.parametrize("value", [True, 1.5, "100", MAX_MINOR_UNITS + 1, -MAX_MINOR_UNITS - 1])
def test_non_usd_minor_units_reject_coercion_and_overflow(value):
    with pytest.raises(StoreError, match="integer JPY minor units"):
        minor_units(value, "JPY")


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        (0, "USD", "$0.00"),
        (1, "USD", "$0.01"),
        (-1, "USD", "-$0.01"),
        (123456, "USD", "$1,234.56"),
        (-123456, "USD", "-$1,234.56"),
        (MAX_CENTS - 1, "USD", "$9,999,999,999.99"),
        (-MAX_CENTS + 1, "USD", "-$9,999,999,999.99"),
        (MAX_SAFE_INTEGER, "USD", "$90,071,992,547,409.91"),
        (-MAX_SAFE_INTEGER, "USD", "-$90,071,992,547,409.91"),
        (123456, "CAD", "CAD 1,234.56"),
        (-123456, "EUR", "-EUR 1,234.56"),
        (0, "JPY", "JPY 0"),
        (123456, "JPY", "JPY 123,456"),
        (1, "KWD", "KWD 0.001"),
        (-1234567, "KWD", "-KWD 1,234.567"),
        (12345678, "CLF", "CLF 1,234.5678"),
    ],
)
def test_money_format_is_fixed_and_exact(value, currency, expected):
    assert format_money(value, currency) == expected


@pytest.mark.parametrize("value", [True, "1234", 1234.0, MAX_SAFE_INTEGER + 1])
def test_money_format_rejects_non_integer_and_out_of_range_money(value):
    with pytest.raises(StoreError):
        format_money(value)


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        ("12.345", "USD", 1235),
        ("-12.345", "EUR", -1235),
        ("12.5", "JPY", 13),
        ("-12.5", "JPY", -13),
        ("0.0005", "KWD", 1),
        ("0.00005", "CLF", 1),
        (Decimal("9.995"), "EUR", 1000),
        (12.34, "USD", 1234),
        ("0.000000001", "USD", 0),
        ("-0.000000001", "USD", 0),
        ("10000000000.004", "USD", MAX_MINOR_UNITS),
        ("1000000000000", "JPY", MAX_MINOR_UNITS),
    ],
)
def test_major_amount_conversion_uses_currency_precision(value, currency, expected):
    assert amount_to_minor_units(value, currency) == expected


@pytest.mark.parametrize(
    "value", [True, None, [], float("inf"), "NaN", "sNaN", "1e999999999", "10000000000.005"]
)
def test_major_amount_conversion_rejects_invalid_or_excessive_amounts(value):
    with pytest.raises(StoreError):
        amount_to_minor_units(value)


def test_conversion_preserves_long_decimal_inputs_and_ignores_ambient_precision():
    with localcontext() as context:
        context.prec = 3
        assert amount_to_minor_units("12.3449999999999999999999999999999999999") == 1234
        assert amount_to_minor_units("12.3450000000000000000000000000000000001") == 1235
        assert format_money(MAX_CENTS - 1) == "$9,999,999,999.99"
