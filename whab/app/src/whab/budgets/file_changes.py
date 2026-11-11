"""Apply bounded storage-owned file changes to a previously verified payload."""

from hashlib import sha256

from whab.domain.errors import StoreError

from .document import MAX_BUNDLE_BYTES


def apply_file_changes(previous, changes, *, digest_file=None):
    if digest_file is None:
        def digest_file(name, raw):
            return sha256(raw).hexdigest()
    if type(changes) is not dict:
        raise StoreError('Invalid storage file changes.')
    if changes.keys() == {'files'}:
        files = changes['files']
        if type(files) is not dict or any(type(name) is not str or type(raw) is not bytes for name, raw in files.items()):
            raise StoreError('Invalid complete storage files.')
        return files
    if changes.keys() != {'patches'} or type(changes['patches']) is not list or len(changes['patches']) > 1000:
        raise StoreError('Invalid storage file patches.')
    result = {}
    total = 0
    for patch in changes['patches']:
        if type(patch) is not dict or patch.keys() != {'name', 'base', 'digest', 'size', 'chunks'}:
            raise StoreError('Invalid storage file patch.')
        name, size = patch['name'], patch['size']
        if type(name) is not str or name not in previous or name in result or type(size) is not int or not 0 <= size <= MAX_BUNDLE_BYTES:
            raise StoreError('Invalid storage file patch target.')
        base = previous[name]
        if type(patch['base']) is not str or digest_file(name, base) != patch['base']:
            raise StoreError('Storage file patch has a different base.')
        total += size
        if total > MAX_BUNDLE_BYTES or type(patch['chunks']) is not list:
            raise StoreError('Storage file patches exceed supported bounds.')
        raw = bytearray(base[:size])
        raw.extend(b'\0' * (size - len(raw)))
        end = 0
        for chunk in patch['chunks']:
            if type(chunk) not in {list, tuple} or len(chunk) != 2:
                raise StoreError('Invalid storage file chunk.')
            offset, content = chunk
            if type(offset) is not int or type(content) is not bytes or offset < end or offset + len(content) > size:
                raise StoreError('Invalid storage file chunk bounds.')
            raw[offset:offset + len(content)] = content
            end = offset + len(content)
        raw = bytes(raw)
        if digest_file(name, raw) != patch['digest']:
            raise StoreError('Storage file patch digest does not match.')
        result[name] = raw
    return result
