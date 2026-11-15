"""Versioned, bounded portable snapshots with checksummed financial changes.

Only sanitized SQLite images enter this envelope. The native database is never
copied. A complete envelope travels inside the ordinary atomic WHAB ZIP save;
there is no sidecar and no acknowledged change waiting for a future snapshot.
"""

import json
from hashlib import sha256

from whab.plugins import StoreError

MAGIC = b'WHAB-SQLITE-2\x00\r\n'
MAX_BYTES = 50 * 1024 * 1024
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
MAX_FRAMES = 512
HEADER_BYTES = len(MAGIC) + 8 + 32
OPERATIONS = frozenset({'edit_transaction', 'edit_transaction_details', 'update_account',
                        'edit_transactions', 'classify_accounts', 'change_transactions',
                        'review_transactions', 'append_transaction_note'})


def snapshot(raw):
    """Wrap a validated image; older image-only readers reject the new magic."""
    result = MAGIC + len(raw).to_bytes(8, 'big') + sha256(raw).digest() + raw
    if len(result) > MAX_BYTES:
        raise StoreError('This SQLite budget exceeds the portable database size limit.')
    return result


def append(raw, entries, frames):
    """Return a complete new envelope, or request compaction for large changes."""
    if not raw.startswith(MAGIC) or frames >= MAX_FRAMES:
        return None
    if not entries or any('snapshot' in entry or entry['operation'] not in OPERATIONS
                          or any(change['path'][0] not in {'accounts', 'transactions'}
                                 or change['before'] is None or change['after'] is None
                                 for change in entry['changes']) for entry in entries):
        return None
    payload = json.dumps(entries, ensure_ascii=True, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
    frame = len(payload).to_bytes(8, 'big') + sha256(payload).digest() + payload
    base_end = HEADER_BYTES + int.from_bytes(raw[len(MAGIC):len(MAGIC) + 8], 'big')
    if len(raw) + len(frame) > MAX_BYTES or len(raw) + len(frame) - base_end > MAX_JOURNAL_BYTES:
        return None
    return raw + frame


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate journal field')
        result[key] = value
    return result


def read(raw):
    """Authenticate all bytes and framing before treating any record as data."""
    try:
        if type(raw) is not bytes or not raw.startswith(MAGIC) or not HEADER_BYTES <= len(raw) <= MAX_BYTES:
            raise ValueError()
        size = int.from_bytes(raw[len(MAGIC):len(MAGIC) + 8], 'big')
        end = HEADER_BYTES + size
        if size < 100 or end > len(raw) or len(raw) - end > MAX_JOURNAL_BYTES:
            raise ValueError()
        base = raw[HEADER_BYTES:end]
        if not base.startswith(b'SQLite format 3\x00') or sha256(base).digest() != raw[len(MAGIC) + 8:HEADER_BYTES]:
            raise ValueError()
        entries = []
        frames = 0
        while end < len(raw):
            if len(raw) - end < 40 or frames >= MAX_FRAMES:
                raise ValueError()
            size = int.from_bytes(raw[end:end + 8], 'big')
            expected = raw[end + 8:end + 40]
            end += 40
            if not 1 <= size <= MAX_JOURNAL_BYTES or end + size > len(raw):
                raise ValueError()
            payload = raw[end:end + size]
            if sha256(payload).digest() != expected:
                raise ValueError()
            changes = json.loads(payload, object_pairs_hook=_unique,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(changes) is not list or not changes:
                raise ValueError()
            entries.extend(changes)
            end += size
            frames += 1
        return base, entries
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise StoreError('This SQLite portable journal is invalid or damaged.') from None
