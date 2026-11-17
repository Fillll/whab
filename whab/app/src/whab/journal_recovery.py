"""Explicit offline rebinding of legacy private journals; never financial repair.

All selected journal owners and the host must be stopped. A complete immutable
before/after plan is flushed before any identity is upgraded. Exact retries may
resume that plan after interruption, accepting only its original or saved bytes.
"""

import fcntl
import os
import re
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

from whab.plugins.effect_store import (
    EffectError,
    EffectStore,
    _mkdir,
    decode,
    digest,
    encode,
    fsync_directory,
)

_NAMES = {'visual-selection.json', 'namespace-effects.json', 'bank-continuations.json',
          'storage-intents.json', 'storage-upgrade.json', 'storage-writer.json', 'remote-client.json'}
_ROOTS = ('visual-ui', 'plugin-workers', 'remote-client', 'storage-authority', 'storage-instances', 'budgets')
_MAX_FILES = 1024
_MAX_BYTES = 256 * 1024 * 1024


class JournalRecoveryError(ValueError):
    _MESSAGES = {
        'invalid_runtime': 'Choose an existing private runtime home and explicit sandbox or production environment.',
        'busy': 'Stop WHAB and its CLI jobs before recovering journal identities. Another owner is still active.',
        'invalid_journal': 'A private journal or backup failed validation. Preserve its files; identity recovery cannot repair its contents.',
        'conflict': 'The recovery preview no longer matches. Inspect a fresh preview; do not overwrite changed journal state.',
        'unsupported': 'Legacy identity recovery requires a stable local macOS volume identity.',
        'interrupted': 'Journal identity recovery was interrupted. Preserve the backup and retry the same --apply fingerprint; never reset the journals.',
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self._MESSAGES[code])


def _path(home, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {'.', '..'} for part in path.parts):
        raise JournalRecoveryError('invalid_journal')
    current = home
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise JournalRecoveryError('invalid_journal')
    return current


def _read(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                or info.st_uid != os.getuid() or not 0 < info.st_size <= limit):
            raise JournalRecoveryError('invalid_journal')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise JournalRecoveryError('invalid_journal')
        return raw
    finally:
        os.close(descriptor)


def _paths(home):
    result, visited = [], 0
    for name in _ROOTS:
        root = _path(home, name)
        if not root.exists():
            continue
        for directory, children, files in os.walk(root, followlinks=False):
            visited += len(children) + len(files)
            if visited > 100000:
                raise JournalRecoveryError('invalid_journal')
            # Approved worker code and recovery archives are not runtime journals.
            children[:] = sorted(part for part in children if part not in {'environments', 'journal-recovery', '__pycache__'})
            if any((Path(directory) / part).is_symlink() for part in children):
                raise JournalRecoveryError('invalid_journal')
            for filename in sorted(set(files) & _NAMES):
                path = Path(directory) / filename
                _path(home, path.relative_to(home))
                result.append(path)
                if len(result) > _MAX_FILES:
                    raise JournalRecoveryError('invalid_journal')
    return sorted(result)


def _owner(filename, record):
    """Reuse host-owned schemas without activating providers or storage factories."""
    from whab.plugins.sdk import valid_plugin_name
    scope = record.get('scope')
    if type(scope) is not dict or scope.get('environment') not in {'sandbox', 'production'}:
        raise JournalRecoveryError('invalid_journal')
    budget = scope.get('budget_id', scope.get('budget'))
    if type(budget) is not str or not 1 <= len(budget) <= 200:
        raise JournalRecoveryError('invalid_journal')
    if filename in {'visual-selection.json', 'remote-client.json'}:
        owner = 'visual-ui' if filename == 'visual-selection.json' else 'remote-client'
        if set(scope) != {'owner', 'budget_id', 'environment'} or scope['owner'] != owner:
            raise JournalRecoveryError('invalid_journal')
        if filename == 'visual-selection.json':
            from whab.plugins.ui_selection import _validate
        else:
            from whab.plugins.remote_selection import _validate
        return _validate, 128 * 1024
    if filename in {'namespace-effects.json', 'bank-continuations.json'}:
        if set(scope) != {'budget', 'environment', 'plugin'} or not valid_plugin_name(scope['plugin']):
            raise JournalRecoveryError('invalid_journal')
        if filename == 'namespace-effects.json':
            from whab.plugins.namespace_effects import MAX_JOURNAL_BYTES, _validate
            return _validate, MAX_JOURNAL_BYTES
        from whab.plugins.bank_continuations import MAX_CONTINUATION_BYTES, _validate
        return _validate, MAX_CONTINUATION_BYTES
    required = {'budget_id', 'environment', 'plugin_id'} | ({'instance_id'} if filename == 'storage-intents.json' else set())
    if set(scope) != required or not valid_plugin_name(scope['plugin_id']):
        raise JournalRecoveryError('invalid_journal')
    if filename == 'storage-upgrade.json':
        from whab.budgets.storage_upgrade import _validate
        from whab.plugins.storage_transfer import read_evidence
        def validate_upgrade(body):
            _validate(body)
            evidence_rows = [body['native']]
            if body['pending'] is not None:
                evidence_rows.append(body['pending']['evidence'])
            if body.get('repair') is not None:
                evidence_rows.append(body['repair']['native'])
            for value in evidence_rows:
                if value is not None:
                    evidence = read_evidence(value)
                    if any(getattr(evidence, key) != scope[key] for key in ('budget_id', 'environment')):
                        raise JournalRecoveryError('invalid_journal')
        return validate_upgrade, 64 * 1024
    if filename == 'storage-intents.json':
        from whab.plugins.storage_intents import _validate
        from whab.plugins.storage_transfer import read_evidence, storage_identifier
        storage_identifier(scope['instance_id'])
        def validate_intents(body):
            _validate(body)
            for row in body['completed'] + ([body['pending']] if body['pending'] is not None else []):
                evidence = read_evidence(row['before'])
                if any(getattr(evidence, key) != scope[key] for key in ('budget_id', 'environment', 'instance_id')):
                    raise JournalRecoveryError('invalid_journal')
        return validate_intents, 2 * 1024 * 1024
    if filename != 'storage-writer.json':
        raise JournalRecoveryError('invalid_journal')

    def validate(value):
        if type(value) is not dict or value != {'format': 1} or type(value['format']) is not int:
            raise JournalRecoveryError('invalid_journal')
    return validate, 4096


@contextmanager
def _host_lock(home, environment):
    path = _path(home, Path('run') / environment / 'host.lock')
    # A preview may initialize this ownership lock, but never changes a saved
    # journal. Even a concurrent first host must share this exact lock inode.
    _mkdir(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise JournalRecoveryError('invalid_runtime')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JournalRecoveryError('busy') from None
        yield
    finally:
        os.close(descriptor)


def _fingerprint(home, environment, entries):
    return digest(encode({'home': str(home), 'environment': environment, 'entries': entries}, 2 * 1024 * 1024))


def _write_once(path, raw):
    descriptor, temporary = tempfile.mkstemp(prefix='.recovery-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _read(path, max(len(raw), 1)) != raw:
                raise JournalRecoveryError('conflict') from None
        fsync_directory(path.parent)
    finally:
        os.unlink(temporary)


def recover_journal_locks(home, environment, *, apply=None, fault=None):
    """Preview, apply or resume exactly one private identity-only recovery plan."""
    home = Path(home).expanduser().resolve()
    if not home.is_dir() or environment not in {'sandbox', 'production'}:
        raise JournalRecoveryError('invalid_runtime')
    if apply is not None and (type(apply) is not str or re.fullmatch('[a-f0-9]{64}', apply) is None):
        raise JournalRecoveryError('conflict')
    publishing = False
    try:
        with _host_lock(home, environment), ExitStack() as owners:
            plan_root = _path(home, Path('plugin-workers') / 'journal-recovery')
            saved = None
            if apply:
                manifest = _path(home, plan_root.relative_to(home) / apply / 'plan.json')
                if manifest.exists():
                    saved = decode(_read(manifest, 2 * 1024 * 1024), 2 * 1024 * 1024)
                    if (type(saved) is not dict or saved.keys() != {'format', 'home', 'environment', 'entries'}
                            or type(saved['format']) is not int or saved['format'] != 1
                            or saved['home'] != str(home) or saved['environment'] != environment
                            or type(saved['entries']) is not list or not 1 <= len(saved['entries']) <= _MAX_FILES
                            or _fingerprint(home, environment, saved['entries']) != apply):
                        raise JournalRecoveryError('invalid_journal')
            paths = _paths(home) if saved is None else [_path(home, row['path']) for row in saved['entries']]
            opened, entries, total = [], [], 0
            for path in paths:
                raw = _read(path, 128 * 1024 * 1024)
                total += len(raw)
                if total > _MAX_BYTES:
                    raise JournalRecoveryError('invalid_journal')
                record = decode(raw, 128 * 1024 * 1024)
                if type(record) is not dict:
                    raise JournalRecoveryError('invalid_journal')
                validate, limit = _owner(path.name, record)
                if record['scope']['environment'] != environment:
                    if saved is not None:
                        raise JournalRecoveryError('conflict')
                    continue
                legacy = record.get('format') == 1
                kwargs = {'legacy_rebind_digest': digest(raw), 'defer_identity_upgrade': True} if legacy else {}
                store = owners.enter_context(EffectStore(path.parent, path.name, record['scope'], None, validate, limit=limit, **kwargs))
                if store._raw != raw:
                    raise JournalRecoveryError('conflict')
                if not legacy and saved is None:
                    continue
                after = encode({**store._record, 'format': 2, 'lock': store._writer_record()}, limit)
                if 'volume' not in store._writer_record():
                    raise JournalRecoveryError('unsupported')
                entry = {'path': str(path.relative_to(home)), 'before': digest(raw), 'after': digest(after)}
                if saved is not None:
                    expected = saved['entries'][len(entries)]
                    if set(expected) != {'path', 'before', 'after'} or any(type(expected[k]) is not str for k in expected):
                        raise JournalRecoveryError('invalid_journal')
                    directory = plan_root / apply
                    before = _read(directory / f'{len(entries)}.before', limit)
                    planned = _read(directory / f'{len(entries)}.after', limit)
                    if (digest(before) != expected['before'] or digest(planned) != expected['after']
                            or planned != after or raw not in (before, planned)):
                        raise JournalRecoveryError('conflict')
                    entry = expected
                opened.append((store, raw, after))
                entries.append(entry)
            fingerprint = _fingerprint(home, environment, entries)
            result = {'status': 'preview', 'fingerprint': fingerprint, 'count': len(entries),
                      'journals': [{'path': row['path'], 'kind': Path(row['path']).name,
                                    'action': 'bind-stable-volume-identity'} for row in entries]}
            if apply is None:
                return result
            if fingerprint != apply:
                raise JournalRecoveryError('conflict')
            if not entries:
                return {**result, 'status': 'complete', 'repaired': 0, 'backup': None}
            directory = _path(home, plan_root.relative_to(home) / apply)
            if saved is None:
                _mkdir(directory)
                for index, (_, before, after) in enumerate(opened):
                    _write_once(directory / f'{index}.before', before)
                    _write_once(directory / f'{index}.after', after)
                plan = {'format': 1, 'home': str(home), 'environment': environment, 'entries': entries}
                _write_once(directory / 'plan.json', encode(plan, 2 * 1024 * 1024))
                fsync_directory(directory)
            repaired = 0
            for store, before, after in opened:
                if before == after:
                    continue
                publishing = True
                store.upgrade_identity()
                if store._raw != after:
                    raise JournalRecoveryError('conflict')
                repaired += 1
                if fault:
                    fault('after_rebind', repaired)
            return {**result, 'status': 'complete', 'repaired': repaired,
                    'backup': str(directory.relative_to(home))}
    except JournalRecoveryError:
        raise
    except EffectError as error:
        if error.code == 'effect_busy':
            raise JournalRecoveryError('busy') from None
        if error.code in {'effect_conflict', 'effect_changed', 'effect_identity_recovery_required'}:
            raise JournalRecoveryError('conflict') from None
        if error.code == 'effect_identity_unavailable':
            raise JournalRecoveryError('unsupported') from None
        raise JournalRecoveryError('interrupted' if publishing else 'invalid_journal') from None
    except Exception:
        raise JournalRecoveryError('interrupted' if publishing else 'invalid_journal') from None
