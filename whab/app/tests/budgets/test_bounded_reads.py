"""Recovery reads retain their size boundary without maximum-size preallocation."""

import io
import tracemalloc
from pathlib import Path

import pytest

from whab.budgets.session import _read_bytes
from whab.domain.errors import StoreError


@pytest.mark.parametrize("size", [0, 1, 65535, 65536, 65537, 150000])
def test_bounded_read_returns_complete_bytes_including_exact_limit(tmp_path, size):
    path = tmp_path / "fictional.bin"
    raw = b"x" * size
    path.write_bytes(raw)
    assert _read_bytes(path, limit=size) == raw


def test_bounded_read_rejects_an_additional_byte(tmp_path):
    path = tmp_path / "fictional.bin"
    path.write_bytes(b"x" * 65537)
    with pytest.raises(StoreError, match="exceeds the supported size"):
        _read_bytes(path, limit=65536)


def test_short_reads_and_growth_do_not_truncate_or_bypass_the_limit(monkeypatch):
    class Growing(io.BytesIO):
        def read(self, size):
            if self.tell() == 3:
                self.seek(0, io.SEEK_END)
                self.write(b"def")
                self.seek(3)
            return super().read(min(size, 3))

    monkeypatch.setattr(Path, "open", lambda *_: Growing(b"abc"))
    assert _read_bytes("fictional.bin", limit=6) == b"abcdef"
    with pytest.raises(StoreError, match="exceeds the supported size"):
        _read_bytes("fictional.bin", limit=5)


def test_small_file_peak_memory_tracks_file_size_instead_of_the_fifty_megabyte_limit(tmp_path):
    path = tmp_path / "fictional.bin"
    path.write_bytes(b"x" * 1024 * 1024)
    tracemalloc.start()
    try:
        raw = _read_bytes(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(raw) == 1024 * 1024
    assert peak < 4 * 1024 * 1024
