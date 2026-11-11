"""Storage deltas preserve complete bytes and reject stale or malformed bases."""

from hashlib import sha256

import pytest

from whab.budgets.file_changes import apply_file_changes
from whab.plugins import StoreError


def patch(base=b'abc', updated=b'abcd', chunks=None):
    return {'patches': [{'name': 'fictional.bin', 'base': sha256(base).hexdigest(),
                         'digest': sha256(updated).hexdigest(), 'size': len(updated),
                         'chunks': [[3, b'd']] if chunks is None else chunks}]}


def test_complete_and_incremental_files_preserve_exact_bytes():
    assert apply_file_changes({}, {'files': {'fictional.bin': b'abc'}}) == {'fictional.bin': b'abc'}
    assert apply_file_changes({'fictional.bin': b'abc'}, patch()) == {'fictional.bin': b'abcd'}
    assert apply_file_changes({'fictional.bin': b'abc'}, patch(updated=b'ab', chunks=[])) == {'fictional.bin': b'ab'}
    assert apply_file_changes({'fictional.bin': b'abc'}, patch(updated=b'abc', chunks=[])) == {'fictional.bin': b'abc'}


@pytest.mark.parametrize('change', [None, [], {}, {'files': []}, {'files': {'bad': 'text'}}, {'patches': {}},
                                     {'patches': [{}]}])
def test_invalid_file_change_shapes_fail(change):
    with pytest.raises(StoreError):
        apply_file_changes({'fictional.bin': b'abc'}, change)


@pytest.mark.parametrize('update', [{'name': 'other'}, {'size': True}, {'size': -1}, {'size': 1000000000},
                                    {'base': 'wrong'}, {'digest': 'wrong'}, {'chunks': {}},
                                    {'chunks': [[-1, b'd']]}, {'chunks': [[4, b'd']]}, {'chunks': [[0, 'text']]},
                                    {'chunks': [[]]}, {'chunks': [[1, b'aa'], [0, b'z']]}])
def test_stale_or_corrupt_patches_fail(update):
    changes = patch()
    changes['patches'][0].update(update)
    with pytest.raises(StoreError):
        apply_file_changes({'fictional.bin': b'abc'}, changes)


def test_patch_segments_zero_fill_growth_and_preserve_identity():
    base = b'abcdefgh'
    updated = b'aXcdefgh\0\0Z\0'
    changes = patch(base, updated, [[1, b'X'], [10, b'Z']])
    assert apply_file_changes({'fictional.bin': base}, changes)['fictional.bin'] == updated
    unchanged = patch(base, base, [])
    assert apply_file_changes({'fictional.bin': base}, unchanged)['fictional.bin'] is base
