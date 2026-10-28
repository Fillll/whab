"""The shared SDK validates PDFs while preserving historical image attachments."""

import pytest

from tests.receipt_images import jpeg, pdf, png, webp
from whab.domain.errors import StoreError
from whab.plugins import receipt_media_type


@pytest.mark.parametrize("raw,media_type", [(jpeg(), "image/jpeg"), (png(), "image/png"), (pdf(), "application/pdf"), (pdf(encrypted=True), "application/pdf"), (webp(), "image/webp")])
def test_shared_receipt_types_include_valid_pdf_and_legacy_webp(raw, media_type):
    assert receipt_media_type(raw) == media_type


@pytest.mark.parametrize("raw", [b"%PDF-1.7\n%%EOF", pdf()[:-8], pdf()+b"<html>bad</html>", b"%PDF-1.7\nnot a document\n%%EOF", b"%PDF-1.7\n"+b"x"*(5*1024*1024)])
def test_malformed_and_oversized_pdf_are_rejected(raw):
    with pytest.raises(StoreError, match="receipt"):
        receipt_media_type(raw)
