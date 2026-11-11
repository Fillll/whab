"""Cached compression remains an ordinary ZIP with independent valid records."""

import io
import os
from zipfile import ZIP_DEFLATED, ZipFile

from whab.budgets.compression import BLOCK_BYTES, DeflateCache
from whab.budgets.container import decode_container, encode_container
from whab.budgets.document import new_manifest


def test_cached_deflate_preserves_zip_crc_growth_shrink_and_unchanged_blocks():
    manifest = new_manifest('Fictional compressed budget', storage='sqlite')
    cache = DeflateCache()
    name = 'plugins/sqlite/家庭.bin'
    raw = b'Fictional ledger ' * (BLOCK_BYTES // 4) + os.urandom(BLOCK_BYTES + 17)

    def check(files):
        archive = encode_container(manifest, files, compression_cache=cache)
        with ZipFile(io.BytesIO(archive)) as opened:
            assert opened.testzip() is None
            assert all(info.compress_type == ZIP_DEFLATED for info in opened.infolist())
        assert decode_container(archive).files == files
        return archive

    check({name: raw})
    original = cache.files[name]
    changed = raw[:BLOCK_BYTES] + b'changed' + raw[BLOCK_BYTES + 7:]
    check({name: changed})
    assert cache.files[name][0][1] is original[0][1]
    assert cache.files[name][1][1] is not original[1][1]
    check({name: changed + b'new records' * BLOCK_BYTES})
    check({name: changed[:BLOCK_BYTES + 1]})
    check({name: b'small file'})
    assert name not in cache.files
    check({name: raw})
    check({})
    assert cache.files == {}
