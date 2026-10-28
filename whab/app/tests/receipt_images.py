"""Tiny synthetic raster data, with no financial or device metadata."""

import struct
import zlib


def png(width=1, height=1):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
            + chunk(b"IEND", b""))


def jpeg():
    return (b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
            b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9")


def webp():
    chunk = b"VP8L\x06\x00\x00\x00\x2f\x00\x00\x00\x00\x00"
    return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk


def pdf(*, encrypted=False):
    """A real one-page fictional PDF with no financial or device metadata."""
    from io import BytesIO

    from pypdf import PdfWriter

    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if encrypted:
        writer.encrypt("fictional-receipt-password")
    writer.write(output)
    return output.getvalue()
