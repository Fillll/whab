"""Journal identity survives remounts without admitting copied or replaced locks."""

import ctypes
import json
from types import SimpleNamespace

import pytest

from whab.plugins import effect_store as stores
from whab.plugins import filesystem_identity as identity

VOLUME = 'darwin-volume-uuid:' + '1' * 32
SCOPE = {'owner': 'fictional', 'budget': 'synthetic', 'environment': 'sandbox'}
BODY = {'choice': 'fictional-view', 'receipts': [{'id': 'one', 'uncertain': True}], 'amount': 12345}


def validate(body):
    if body != BODY:
        stores.invalid()


def opened(root, **options):
    return stores.EffectStore(root, 'visual-selection.json', SCOPE, BODY, validate, limit=4096, **options)


def saved_legacy(root, monkeypatch, *, different_device=False):
    with monkeypatch.context() as legacy:
        legacy.setattr(stores, 'filesystem_identity', lambda _: None)
        with opened(root) as store:
            store.commit(BODY)
    path = root / 'visual-selection.json'
    record = json.loads(path.read_bytes())
    if different_device:
        record['lock']['device'] = str(int(record['lock']['device']) + 1)
        path.write_bytes(stores.encode(record, 4096))
    return path, record, path.read_bytes()


def test_darwin_descriptor_uuid_has_no_path_or_subprocess_dependency(monkeypatch):
    calls = []

    class Function:
        def __call__(self, descriptor, attributes, result, size, options):
            attrs = ctypes.cast(attributes, ctypes.POINTER(identity._Attributes)).contents
            calls.append((descriptor, attrs.bitmapcount, attrs.volattr, size, options))
            result.raw = (20).to_bytes(4, identity.sys.byteorder) + bytes.fromhex('1' * 32)
            return 0

    function = Function()
    monkeypatch.setattr(identity.sys, 'platform', 'darwin')
    monkeypatch.setattr(identity.ctypes, 'CDLL', lambda *_, **__: SimpleNamespace(fgetattrlist=function))
    assert identity.filesystem_identity(123) == VOLUME
    assert calls == [(123, 5, 0x80040000, 20, 0)]
    assert function.restype is ctypes.c_int


@pytest.mark.parametrize('status,length,value', [(1, 20, b'x' * 16), (0, 0, b'x' * 16), (0, 20, b'\0' * 16)])
def test_darwin_missing_or_invalid_uuid_never_falls_back_to_device(monkeypatch, status, length, value):
    class Function:
        def __call__(self, descriptor, attributes, result, size, options):
            result.raw = length.to_bytes(4, identity.sys.byteorder) + value
            return status

    monkeypatch.setattr(identity.sys, 'platform', 'darwin')
    monkeypatch.setattr(identity.ctypes, 'CDLL', lambda *_, **__: SimpleNamespace(fgetattrlist=Function()))
    with pytest.raises(OSError):
        identity.filesystem_identity(123)


def test_other_platform_keeps_existing_device_scheme_without_loading_darwin_api(monkeypatch):
    monkeypatch.setattr(identity.sys, 'platform', 'linux')
    monkeypatch.setattr(identity.ctypes, 'CDLL', lambda *_a, **_k: pytest.fail('Darwin API loaded'))
    assert identity.filesystem_identity(123) is None


def test_new_identity_reopens_on_same_volume_after_device_number_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    with opened(tmp_path) as store:
        store.commit(BODY)
    path = tmp_path / 'visual-selection.json'
    original = path.read_bytes()
    record = json.loads(original)
    assert record['format'] == 2 and record['lock']['volume'] == VOLUME and 'device' not in record['lock']
    stat, fstat = stores.os.stat, stores.os.fstat

    def remounted(value):
        return SimpleNamespace(st_dev=value.st_dev + 1, st_ino=value.st_ino, st_mode=value.st_mode, st_size=value.st_size)

    with monkeypatch.context() as reboot:
        reboot.setattr(stores.os, 'stat', lambda *a, **k: remounted(stat(*a, **k)))
        reboot.setattr(stores.os, 'fstat', lambda *a, **k: remounted(fstat(*a, **k)))
        with opened(tmp_path) as store:
            assert store.snapshot() == BODY
            assert store._record['revision'] == 1
    assert path.read_bytes() == original


def test_another_volume_cannot_adopt_matching_inode_and_nonce(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    with opened(tmp_path):
        pass
    path = tmp_path / 'visual-selection.json'
    original = path.read_bytes()
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: 'darwin-volume-uuid:' + '2' * 32)
    with pytest.raises(stores.EffectError) as raised:
        opened(tmp_path)
    assert raised.value.code == 'effect_changed' and path.read_bytes() == original


def test_same_device_legacy_upgrades_only_writer_metadata(tmp_path, monkeypatch):
    path, record, _ = saved_legacy(tmp_path, monkeypatch)
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    with opened(tmp_path) as store:
        assert store.snapshot() == record['body']
    new = json.loads(path.read_bytes())
    assert new == {**record, 'format': 2, 'lock': {'token': record['lock']['token'], 'inode': record['lock']['inode'], 'volume': VOLUME}}


def test_legacy_device_change_requires_exact_explicit_recovery(tmp_path, monkeypatch):
    path, record, original = saved_legacy(tmp_path, monkeypatch, different_device=True)
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    with pytest.raises(stores.EffectError) as blocked:
        opened(tmp_path)
    assert blocked.value.code == 'effect_identity_recovery_required' and path.read_bytes() == original
    with pytest.raises(stores.EffectError) as stale:
        opened(tmp_path, legacy_rebind_digest='0' * 64)
    assert stale.value.code == 'effect_conflict' and path.read_bytes() == original
    with opened(tmp_path, legacy_rebind_digest=stores.digest(original)) as store:
        assert store.snapshot() == record['body'] and store._record['revision'] == record['revision']
    after = path.read_bytes()
    with pytest.raises(stores.EffectError) as repeated:
        opened(tmp_path, legacy_rebind_digest=stores.digest(original))
    assert repeated.value.code == 'effect_conflict' and path.read_bytes() == after


def test_deferred_recovery_holds_original_record_until_explicit_identity_upgrade(tmp_path, monkeypatch):
    path, record, original = saved_legacy(tmp_path, monkeypatch, different_device=True)
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    with opened(tmp_path, legacy_rebind_digest=stores.digest(original), defer_identity_upgrade=True) as store:
        assert path.read_bytes() == original and store.snapshot() == record['body']
        with pytest.raises(stores.EffectError) as busy:
            opened(tmp_path, legacy_rebind_digest=stores.digest(original))
        assert busy.value.code == 'effect_busy'
        assert store.upgrade_identity() is True
        published = path.read_bytes()
        assert store.upgrade_identity() is False and path.read_bytes() == published
        assert store.snapshot() == record['body'] and store._record['revision'] == record['revision']


def test_deferred_recovery_cannot_overwrite_a_subsequent_external_change(tmp_path, monkeypatch):
    path, _, original = saved_legacy(tmp_path, monkeypatch, different_device=True)
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    with opened(tmp_path, legacy_rebind_digest=stores.digest(original), defer_identity_upgrade=True) as store:
        path.write_bytes(original + b' ')
        with pytest.raises(stores.EffectError) as raised:
            store.upgrade_identity()
        assert raised.value.code == 'effect_changed' and path.read_bytes() == original + b' '


@pytest.mark.parametrize('options', [{'defer_identity_upgrade': True}, {'defer_identity_upgrade': 1}])
def test_defer_requires_explicit_exact_legacy_recovery_intent(tmp_path, options):
    with pytest.raises(stores.EffectError):
        opened(tmp_path, **options)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('change', ['token', 'inode', 'body', 'scope', 'format', 'device-type', 'device-text', 'extra-lock'])
def test_legacy_recovery_never_accepts_other_corruption(tmp_path, monkeypatch, change):
    path, record, _ = saved_legacy(tmp_path, monkeypatch, different_device=True)
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    if change == 'token':
        record['lock']['token'] = '0' * 32
    elif change == 'inode':
        record['lock']['inode'] = str(int(record['lock']['inode']) + 1)
    elif change == 'body':
        record['body'] = {}
    elif change == 'scope':
        record['scope'] = {}
    elif change == 'format':
        record['format'] = 3
    elif change == 'device-type':
        record['lock']['device'] = 1
    elif change == 'device-text':
        record['lock']['device'] = 'not a device'
    else:
        record['lock']['unexpected'] = True
    raw = stores.encode(record, 4096)
    path.write_bytes(raw)
    with pytest.raises(stores.EffectError):
        opened(tmp_path, legacy_rebind_digest=stores.digest(raw))
    assert path.read_bytes() == raw


@pytest.mark.parametrize('fault', ['before_replace', 'after_replace', 'after_directory_fsync'])
def test_interrupted_identity_upgrade_retains_old_or_complete_new_record(tmp_path, monkeypatch, fault):
    path, record, original = saved_legacy(tmp_path, monkeypatch)
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)

    def fail(stage):
        if stage == fault:
            raise OSError('fictional disk failure')

    with pytest.raises(stores.EffectError) as raised:
        opened(tmp_path, fault=fail)
    assert raised.value.code == ('effect_commit_failed' if fault == 'before_replace' else 'effect_commit_uncertain')
    if fault == 'before_replace':
        assert path.read_bytes() == original
    else:
        published = json.loads(path.read_bytes())
        assert published['format'] == 2 and published['body'] == record['body'] and published['revision'] == record['revision']
    with opened(tmp_path) as reopened:
        assert reopened.snapshot() == record['body'] and reopened._record['revision'] == record['revision']


def test_recovery_needs_existing_record_and_stable_volume_and_valid_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: None)
    for digest in [0, 'bad', '0' * 64]:
        with pytest.raises(stores.EffectError):
            opened(tmp_path, legacy_rebind_digest=digest)
    assert not list(tmp_path.iterdir())
    path, _, original = saved_legacy(tmp_path, monkeypatch, different_device=True)
    with pytest.raises(stores.EffectError) as raised:
        opened(tmp_path, legacy_rebind_digest=stores.digest(original))
    assert raised.value.code == 'effect_identity_unavailable' and path.read_bytes() == original
    with monkeypatch.context() as legacy:
        legacy.setattr(stores, 'filesystem_identity', lambda _: None)
        record = json.loads(original)
        record['lock']['device'] = str(path.stat().st_dev)
        path.write_bytes(stores.encode(record, 4096))
        with opened(tmp_path) as store:
            with pytest.raises(stores.EffectError) as unsupported:
                store.upgrade_identity()
            assert unsupported.value.code == 'effect_conflict'


def test_uuid_failure_closes_lock_and_does_not_change_journal(tmp_path, monkeypatch):
    path, _, original = saved_legacy(tmp_path, monkeypatch)

    def fail(_):
        raise OSError('fictional filesystem has no stable identity')

    monkeypatch.setattr(stores, 'filesystem_identity', fail)
    with pytest.raises(stores.EffectError) as raised:
        opened(tmp_path)
    assert raised.value.code == 'effect_unavailable' and path.read_bytes() == original
    monkeypatch.setattr(stores, 'filesystem_identity', lambda _: VOLUME)
    with opened(tmp_path) as store:
        assert store.snapshot() == BODY
