"""Shared attachment validation outside the financial domain."""

from io import BytesIO

from pypdf import PdfReader

from whab.domain.errors import StoreError
from whab.domain.receipts import MAX_RECEIPT_BYTES
from whab.domain.receipts import receipt_media_type as raster_media_type


def receipt_media_type(raw):
    """Validate private receipt bytes, retaining read compatibility with old WebP."""
    if type(raw) is bytes and raw.startswith(b"%PDF-"):
        if not 1 <= len(raw) <= MAX_RECEIPT_BYTES:
            raise StoreError("Choose a receipt file no larger than 5 MiB.")
        try:
            if not raw.rstrip(b" \t\r\n\x00\f").endswith(b"%%EOF"):
                raise ValueError()
            reader = PdfReader(BytesIO(raw), strict=True, root_object_recovery_limit=0,
                               xref_stream_maximum_entries=100000)
            if "/Root" not in reader.trailer or (not reader.is_encrypted and reader.root_object.get("/Type") != "/Catalog"):
                raise ValueError()
        except Exception:
            raise StoreError("Choose a valid PDF receipt file.") from None
        return "application/pdf"
    try:
        return raster_media_type(raw)
    except StoreError:
        raise StoreError("Choose a valid JPEG, PNG or PDF receipt file no larger than 5 MiB; images must be at most 25 megapixels.") from None
