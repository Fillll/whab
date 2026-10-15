"""Integer USD cents and exact conversion from institution dollar amounts."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .errors import StoreError

MAX_CENTS = 1_000_000_000_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def cents(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_CENTS:
        raise StoreError("Money must be integer USD cents within the supported range.")
    return value


def dollars_to_cents(value: Any) -> int:
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite():
            raise ValueError
        return cents(int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    except (ValueError, TypeError, ArithmeticError):
        raise StoreError("Invalid bank amount.") from None
