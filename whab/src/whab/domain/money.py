"""Exact currency amounts and fixed, locale-independent presentation.

Amounts are stored in integer minor units of their declared currency. USD
compatibility helpers retain the original cents contract.
"""

from decimal import ROUND_HALF_UP, Decimal, localcontext
from types import MappingProxyType
from typing import Any

from .errors import StoreError

MAX_CENTS = 1_000_000_000_000
MAX_MINOR_UNITS = MAX_CENTS
MAX_SAFE_INTEGER = 9_007_199_254_740_991

# ISO 4217 List One, published 2026-09-17 by its maintenance agency, SIX:
# https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
# Only codes with a defined numeric minor unit are supported. Metals, test codes
# and units such as XDR/XXX have no declared precision and cannot be ledger money.
# Checked-in metadata keeps core behavior deterministic and usable offline.
_CURRENCY_MINOR_UNITS = MappingProxyType(
    {
        **dict.fromkeys(
            (
                "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX UYI VND VUV XAF XOF XPF"
            ).split(),
            0,
        ),
        **dict.fromkeys(
            (
                "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BMD BND BOB BOV BRL BSD "
                "BTN BWP BYN BZD CAD CDF CHE CHF CHW CNY COP COU CRC CUP CVE CZK DKK DOP "
                "DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GTQ GYD HKD HNL HTG HUF "
                "IDR ILS INR IRR JMD KES KGS KHR KPW KYD KZT LAK LBP LKR LRD LSL MAD MDL "
                "MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR "
                "NZD PAB PEN PGK PHP PKR PLN QAR RON RSD RUB SAR SBD SCR SDG SEK SGD SHP "
                "SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TOP TRY TTD TWD TZS UAH USD "
                "USN UYU UZS VED VES WST XAD XCD XCG YER ZAR ZMW ZWG"
            ).split(),
            2,
        ),
        **dict.fromkeys("BHD IQD JOD KWD LYD OMR TND".split(), 3),
        **dict.fromkeys("CLF UYW".split(), 4),
    }
)
SUPPORTED_CURRENCIES = tuple(sorted(_CURRENCY_MINOR_UNITS))


def currency_code(value: Any) -> str:
    """Require a canonical supported ISO code, without silently coercing it."""
    if type(value) is not str or value not in _CURRENCY_MINOR_UNITS:
        raise StoreError("Currency must be a supported uppercase ISO 4217 code.")
    return value


def currency_minor_units(currency: str) -> int:
    """Return the number of decimal places in the currency's major unit."""
    return _CURRENCY_MINOR_UNITS[currency_code(currency)]


def minor_units(value: Any, currency: str = "USD") -> int:
    """Validate a stored integer amount in its declared currency."""
    code = currency_code(currency)
    if code == "USD":
        return cents(value)
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_MINOR_UNITS:
        raise StoreError(f"Money must be integer {code} minor units within the supported range.")
    return value


def cents(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_CENTS:
        raise StoreError("Money must be integer USD cents within the supported range.")
    return value


def amount_to_minor_units(value: Any, currency: str = "USD") -> int:
    """Round a major-unit source amount half away from zero, without float math.

    Decimal strings are preferred. Existing bank integrations may pass a finite
    float; its decimal spelling is used, preserving the USD ingestion contract.
    """
    places = currency_minor_units(currency)
    try:
        if isinstance(value, bool):
            raise ValueError
        decimal = Decimal(str(value))
        if not decimal.is_finite():
            raise ValueError
        # Bound before rounding or converting to int, including huge exponents.
        # Decimal construction and comparison do not depend on context precision.
        limit = Decimal(f"{MAX_MINOR_UNITS + 1}e-{places}")
        if decimal.copy_abs() >= limit:
            raise ValueError
        # Long decimal inputs must not round early at the ambient precision.
        with localcontext() as context:
            context.prec = max(28, len(decimal.as_tuple().digits) + places + 1)
            rounded = (decimal * 10**places).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return minor_units(int(rounded), currency)
    except (ValueError, TypeError, ArithmeticError):
        raise StoreError("Invalid bank amount.") from None


def dollars_to_cents(value: Any) -> int:
    """Compatibility helper for institution amounts denominated in USD."""
    return amount_to_minor_units(value, "USD")


def format_money(value: Any, currency: str = "USD") -> str:
    """Format integer money with commas, a decimal point and fixed precision.

    USD uses ``$1,234.56``. Other currencies use their unambiguous ISO code,
    such as ``CAD 1,234.56`` or ``JPY 1,234``. The sign precedes the prefix.
    Presentation plugins may replace this default at their own boundary.
    """
    places = currency_minor_units(currency)
    # A displayed total may exceed the per-record storage limit. It must retain
    # the same safe integer bound used by domain aggregate projections.
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_SAFE_INTEGER:
        raise StoreError("Formatted money must be integer minor units within the supported range.")
    amount = value
    whole, fraction = divmod(abs(amount), 10**places)
    number = f"{whole:,}"
    if places:
        number += f".{fraction:0{places}d}"
    prefix = "$" if currency == "USD" else f"{currency} "
    sign = "-" if amount < 0 else ""
    return f"{sign}{prefix}{number}"
