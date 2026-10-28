"""Bounded receipt selections and raster validation shared with storage adapters."""

import struct
import zlib

from .errors import StoreError

MAX_RECEIPT_BYTES = 5 * 1024 * 1024
MAX_RECEIPT_PIXELS = 25_000_000
RECEIPT_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})


def validate_receipt_selection(transaction_ids) -> tuple[str, ...]:
    """Validate a bounded selection of exact identities without normalizing them."""
    if type(transaction_ids) not in (list, tuple) or not 1 <= len(transaction_ids) <= 100:
        raise StoreError("Receipt selection must contain between 1 and 100 transaction IDs.")
    for value in transaction_ids:
        if type(value) is not str or not 1 <= len(value) <= 200 or "\x00" in value:
            raise StoreError("Invalid transaction ID.")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise StoreError("Invalid transaction ID.") from None
    if len(set(transaction_ids)) != len(transaction_ids):
        raise StoreError("Receipt selection transaction IDs must be distinct.")
    return tuple(transaction_ids)


def _dimensions(width, height):
    if not width or not height or width * height > MAX_RECEIPT_PIXELS:
        raise ValueError()


def _png(raw):
    position, image, header = 8, False, False
    while position + 12 <= len(raw):
        size = int.from_bytes(raw[position:position + 4], "big")
        kind = raw[position + 4:position + 8]
        end = position + 12 + size
        if end > len(raw) or zlib.crc32(raw[position + 4:end - 4]) != int.from_bytes(raw[end - 4:end], "big"):
            raise ValueError()
        if not header:
            if kind != b"IHDR" or size != 13:
                raise ValueError()
            _dimensions(*struct.unpack(">II", raw[position + 8:position + 16]))
            header = True
        elif kind == b"IHDR":
            raise ValueError()
        image = image or (kind == b"IDAT" and size > 0)
        if kind == b"IEND":
            if size or not image or end != len(raw):
                raise ValueError()
            return
        position = end
    raise ValueError()


def _jpeg(raw):
    if not raw.endswith(b"\xff\xd9"):
        raise ValueError()
    position, dimensions = 2, False
    while position + 4 <= len(raw):
        if raw[position] != 255:
            raise ValueError()
        marker = raw[position + 1]
        if marker == 255:
            position += 1
            continue
        size = int.from_bytes(raw[position + 2:position + 4], "big")
        if size < 2 or position + 2 + size > len(raw):
            raise ValueError()
        if marker in {0xC0, 0xC1, 0xC2}:
            if size < 8:
                raise ValueError()
            height, width = struct.unpack(">HH", raw[position + 5:position + 9])
            _dimensions(width, height)
            dimensions = True
        if marker == 0xDA:
            if not dimensions or position + 2 + size >= len(raw) - 2:
                raise ValueError()
            return
        position += 2 + size
    raise ValueError()


def _webp(raw):
    if int.from_bytes(raw[4:8], "little") + 8 != len(raw):
        raise ValueError()
    position, image = 12, False
    while position + 8 <= len(raw):
        kind = raw[position:position + 4]
        size = int.from_bytes(raw[position + 4:position + 8], "little")
        start, end = position + 8, position + 8 + size
        if end > len(raw):
            raise ValueError()
        if kind == b"VP8X":
            if size != 10:
                raise ValueError()
            _dimensions(int.from_bytes(raw[start + 4:start + 7], "little") + 1,
                        int.from_bytes(raw[start + 7:start + 10], "little") + 1)
        if kind == b"VP8 ":
            if size < 10 or raw[start + 3:start + 6] != b"\x9d\x01\x2a":
                raise ValueError()
            width, height = struct.unpack("<HH", raw[start + 6:start + 10])
            _dimensions(width & 0x3FFF, height & 0x3FFF)
            image = True
        if kind == b"VP8L":
            if size < 5 or raw[start] != 0x2F:
                raise ValueError()
            bits = int.from_bytes(raw[start + 1:start + 5], "little")
            _dimensions((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            image = True
        position = end + size % 2
    if not image or position != len(raw):
        raise ValueError()


def receipt_media_type(raw):
    """Check bounded raster structure; never trust extensions or upload MIME labels."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RECEIPT_BYTES:
        raise StoreError("Choose a receipt file no larger than 5 MiB.")
    try:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            _png(raw)
            return "image/png"
        if raw.startswith(b"\xff\xd8"):
            _jpeg(raw)
            return "image/jpeg"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            _webp(raw)
            return "image/webp"
    except (ValueError, IndexError, struct.error):
        pass
    raise StoreError("Choose a valid PNG, JPEG or WebP receipt image up to 25 megapixels.")
