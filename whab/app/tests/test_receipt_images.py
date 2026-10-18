"""Receipt uploads accept bounded raster structures rather than filenames or MIME claims."""

import pytest

from tests.receipt_images import jpeg, png, webp
from whab.domain.errors import StoreError
from whab.domain.receipts import MAX_RECEIPT_BYTES, receipt_media_type


@pytest.mark.parametrize("image,mime", [(png(), "image/png"), (jpeg(), "image/jpeg"), (webp(), "image/webp")])
def test_actual_raster_headers_determine_type(image, mime):
    assert receipt_media_type(image) == mime


@pytest.mark.parametrize("image", [
    b"", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"<html></html>",
    b"GIF89a", b"x" * (MAX_RECEIPT_BYTES + 1), "not bytes",
    png()[:-1], png(100000, 100000), png().replace(b"IHDR", b"oops"),
    jpeg()[:-2], jpeg().replace(b"\x00\x0b", b"\x00\x01"), webp()[:-1],
])
def test_invalid_unsafe_oversized_or_truncated_receipts_are_rejected(image):
    with pytest.raises(StoreError):
        receipt_media_type(image)


def test_webp_extended_canvas_is_bounded_independently_of_small_image_payload():
    import struct

    for width, height, accepted in [(1, 1, True), (100000, 100000, False)]:
        canvas = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
        chunks = canvas + webp()[12:]
        image = b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WEBP" + chunks
        if accepted:
            assert receipt_media_type(image) == "image/webp"
        else:
            with pytest.raises(StoreError):
                receipt_media_type(image)
