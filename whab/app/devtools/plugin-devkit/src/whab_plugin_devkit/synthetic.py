"""Small fictional fixtures with no dependency on a maintained backend."""

import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class SyntheticClock:
    """Explicit suite calendar; adapters may inject the same month in their factory.

    The default follows the host's current UTC month for backends without a clock
    injection contract. This does not patch globals or claim to control a backend.
    """

    month: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m"))

    @property
    def date(self):
        return f"{self.month}-01"


def imported_account(account_id="shared-account", item_id="shared-item"):
    return {
        "id": account_id, "itemId": item_id, "name": "Fictional checking",
        "institution": "Fictional Bank", "type": "depository", "balance": 100000,
        "currency": "USD", "source": "plaid", "stale": False, "balanceUnknown": False,
    }


def imported_transaction(clock, transaction_id="pending", **changes):
    return {
        "id": transaction_id, "accountId": "shared-account", "date": clock.date,
        "merchant": "Fictional shop", "amount": 125, "suggestedCategoryId": None,
        "pending": True, "source": "plaid", "inferredTransfer": False,
        "pendingTransactionId": None, **changes,
    }


def receipt_png():
    """One synthetic white pixel, including valid chunk checksums."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )
