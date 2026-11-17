"""Offline identity recovery preserves decisions and requires an exact reviewed plan."""

import base64
import json
import os
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from whab import journal_recovery as recovery
from whab.hosting import process_lock
from whab.plugins import effect_store
from whab.plugins.bank_continuations import BankContinuations, ContinuationScope
from whab.plugins.namespace_effects import (
    NamespaceJournal,
    NamespaceOperation,
    NamespaceScope,
    NamespaceSnapshot,
)
from whab.plugins.remote_client_adapter import RemoteLedgerClient, client_record
from whab.plugins.storage_transfer import CommitEvidence, evidence_record

VOLUME = 'darwin-volume-uuid:' + '1' * 32
VISUAL_SCOPE = {'owner': 'visual-ui', 'budget_id': 'fictional', 'environment': 'sandbox'}
VISUAL_BODY = {'format': 1, 'selection': 'web', 'generation': None, 'worker': None,
               'known': ['web'], 'workers': {'web': None}, 'decisions': []}


@pytest.fixture(autouse=True)
def stable_volume(monkeypatch):
    monkeypatch.setattr(effect_store, 'filesystem_identity', lambda _: VOLUME)


def seed(home, monkeypatch, relative='visual-ui/visual-selection.json', *, scope=None, body=None):
    path = home / relative
    scope = deepcopy(scope or VISUAL_SCOPE)
    body = deepcopy(body or VISUAL_BODY)
    validate, limit = recovery._owner(path.name, {'scope': scope})
    with monkeypatch.context() as legacy:
        legacy.setattr(effect_store, 'filesystem_identity', lambda _: None)
        with effect_store.EffectStore(path.parent, path.name, scope, body, validate, limit=limit) as store:
            store.commit(body)
    record = json.loads(path.read_bytes())
    record['lock']['device'] = str(int(record['lock']['device']) + 1)
    path.write_bytes(effect_store.encode(record, limit))
    return path, path.read_bytes()


def inspect(home, *, environment='sandbox', apply=None, fault=None):
    return recovery.recover_journal_locks(home, environment, apply=apply, fault=fault)


def test_preview_apply_backup_and_exact_noop_retry_preserve_all_original_decisions(tmp_path, monkeypatch):
    path, original = seed(tmp_path, monkeypatch)
    record = json.loads(original)
    preview = inspect(tmp_path)
    assert preview['status'] == 'preview' and preview['count'] == 1 and path.read_bytes() == original
    assert preview['journals'] == [{'path': 'visual-ui/visual-selection.json', 'kind': 'visual-selection.json',
                                    'action': 'bind-stable-volume-identity'}]
    result = inspect(tmp_path, apply=preview['fingerprint'])
    assert result['status'] == 'complete' and result['repaired'] == 1
    saved = path.read_bytes()
    current = json.loads(saved)
    assert current == {**record, 'format': 2, 'lock': {'token': record['lock']['token'], 'inode': record['lock']['inode'], 'volume': VOLUME}}
    backup = tmp_path / result['backup']
    assert (backup / '0.before').read_bytes() == original
    assert (backup / '0.after').read_bytes() == saved
    assert (backup.stat().st_mode & 0o777) == 0o700
    assert all((file.stat().st_mode & 0o777) == 0o600 for file in backup.iterdir())
    before_retry = {p.name: p.read_bytes() for p in backup.iterdir()}
    repeated = inspect(tmp_path, apply=preview['fingerprint'])
    assert repeated['repaired'] == 0 and repeated['backup'] == result['backup']
    assert path.read_bytes() == saved and before_retry == {p.name: p.read_bytes() for p in backup.iterdir()}
    assert inspect(tmp_path)['count'] == 0


def test_same_fingerprint_resumes_partial_apply_without_republishing_completed_journal(tmp_path, monkeypatch):
    first, before_first = seed(tmp_path, monkeypatch, 'budgets/a/visual-ui/visual-selection.json')
    second, before_second = seed(tmp_path, monkeypatch, 'budgets/b/visual-ui/visual-selection.json')
    preview = inspect(tmp_path)

    def interrupt(stage, count):
        assert stage == 'after_rebind'
        if count == 1:
            raise OSError('fictional interruption')

    with pytest.raises(recovery.JournalRecoveryError) as raised:
        inspect(tmp_path, apply=preview['fingerprint'], fault=interrupt)
    assert raised.value.code == 'interrupted'
    first_saved = first.read_bytes()
    assert first_saved != before_first and second.read_bytes() == before_second
    result = inspect(tmp_path, apply=preview['fingerprint'])
    assert result['repaired'] == 1 and result['count'] == 2
    assert first.read_bytes() == first_saved and second.read_bytes() != before_second
    assert inspect(tmp_path, apply=preview['fingerprint'])['repaired'] == 0


@pytest.mark.parametrize('corruption', ['body', 'scope', 'environment', 'format', 'permission', 'duplicate-key'])
def test_invalid_later_journal_rejects_entire_batch_without_rewriting_first(tmp_path, monkeypatch, corruption):
    first, original = seed(tmp_path, monkeypatch, 'budgets/a/visual-ui/visual-selection.json')
    last, _ = seed(tmp_path, monkeypatch, 'budgets/z/visual-ui/visual-selection.json')
    preview = inspect(tmp_path)
    record = json.loads(last.read_bytes())
    if corruption == 'body':
        record['body']['selection'] = 'not-known'
    elif corruption == 'scope':
        record['scope']['owner'] = 'remote-client'
    elif corruption == 'environment':
        record['scope']['environment'] = 'invalid'
    elif corruption == 'format':
        record['format'] = 99
    last.write_bytes(effect_store.encode(record, 128 * 1024))
    if corruption == 'permission':
        last.chmod(0o644)
    elif corruption == 'duplicate-key':
        last.write_bytes(last.read_bytes()[:-1] + b',"format":1}')
    invalid = last.read_bytes()
    with pytest.raises(recovery.JournalRecoveryError):
        inspect(tmp_path, apply=preview['fingerprint'])
    assert first.read_bytes() == original and last.read_bytes() == invalid
    assert not (tmp_path / 'plugin-workers/journal-recovery').exists()


def test_environment_isolation_does_not_upgrade_other_environment(tmp_path, monkeypatch):
    sandbox, _ = seed(tmp_path, monkeypatch)
    production, before = seed(tmp_path, monkeypatch, 'budgets/other/visual-ui/visual-selection.json',
                              scope={**VISUAL_SCOPE, 'environment': 'production'})
    preview = inspect(tmp_path)
    assert preview['count'] == 1
    inspect(tmp_path, apply=preview['fingerprint'])
    assert json.loads(sandbox.read_bytes())['format'] == 2 and production.read_bytes() == before
    with pytest.raises(recovery.JournalRecoveryError):
        inspect(tmp_path, environment='production', apply=preview['fingerprint'])
    assert production.read_bytes() == before


def test_active_host_blocks_preview_and_apply_without_touching_journals(tmp_path, monkeypatch):
    path, before = seed(tmp_path, monkeypatch)
    preview = inspect(tmp_path)
    lock = tmp_path / 'run/sandbox/host.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with process_lock(lock, blocking=False):
        for fingerprint in [None, preview['fingerprint']]:
            with pytest.raises(recovery.JournalRecoveryError) as raised:
                inspect(tmp_path, apply=fingerprint)
            assert raised.value.code == 'busy'
    assert path.read_bytes() == before


def test_active_journal_owner_blocks_the_whole_batch(tmp_path, monkeypatch):
    first, before_first = seed(tmp_path, monkeypatch, 'budgets/a/visual-ui/visual-selection.json')
    last, before_last = seed(tmp_path, monkeypatch, 'budgets/z/visual-ui/visual-selection.json')
    preview = inspect(tmp_path)
    validate, limit = recovery._owner(last.name, {'scope': VISUAL_SCOPE})
    with effect_store.EffectStore(last.parent, last.name, VISUAL_SCOPE, None, validate, limit=limit,
                                 legacy_rebind_digest=effect_store.digest(before_last), defer_identity_upgrade=True):
        with pytest.raises(recovery.JournalRecoveryError) as raised:
            inspect(tmp_path, apply=preview['fingerprint'])
        assert raised.value.code == 'busy'
    assert first.read_bytes() == before_first and last.read_bytes() == before_last


@pytest.mark.parametrize('change', ['journal-bytes', 'lock-token', 'lock-inode', 'volume'])
def test_stale_preview_and_replaced_lock_cannot_authorize_recovery(tmp_path, monkeypatch, change):
    path, before = seed(tmp_path, monkeypatch)
    preview = inspect(tmp_path)
    lock = path.with_name(path.name + '.lock')
    if change == 'journal-bytes':
        path.write_bytes(before + b' ')
    elif change == 'lock-token':
        lock.write_bytes(b'0' * 32 + b'\n')
    elif change == 'lock-inode':
        copied = lock.with_name('copied.lock')
        copied.write_bytes(lock.read_bytes())
        copied.chmod(0o600)
        os.replace(copied, lock)
    else:
        monkeypatch.setattr(effect_store, 'filesystem_identity', lambda _: 'darwin-volume-uuid:' + '2' * 32)
    changed = path.read_bytes()
    with pytest.raises(recovery.JournalRecoveryError):
        inspect(tmp_path, apply=preview['fingerprint'])
    assert path.read_bytes() == changed


@pytest.mark.parametrize('redirect', ['journal', 'lock', 'directory', 'backup'])
def test_symlinks_are_refused_without_following_external_content(tmp_path, monkeypatch, redirect):
    path, original = seed(tmp_path, monkeypatch)
    preview = inspect(tmp_path)
    external = tmp_path / 'external'
    external.mkdir()
    untouched = external / 'unchanged'
    untouched.write_bytes(b'fictional external data')
    if redirect in {'journal', 'lock'}:
        target = path if redirect == 'journal' else path.with_name(path.name + '.lock')
        target.unlink()
        target.symlink_to(untouched)
    elif redirect == 'directory':
        (tmp_path / 'budgets').symlink_to(external, target_is_directory=True)
    else:
        (tmp_path / 'plugin-workers').mkdir(exist_ok=True)
        (tmp_path / 'plugin-workers/journal-recovery').symlink_to(external, target_is_directory=True)
    with pytest.raises(recovery.JournalRecoveryError):
        inspect(tmp_path, apply=preview['fingerprint'])
    assert untouched.read_bytes() == b'fictional external data'
    if redirect != 'journal':
        assert path.read_bytes() == original


@pytest.mark.parametrize('archive', ['0.before', '0.after', 'plan.json'])
def test_corrupt_backup_never_republishes_or_discards_saved_state(tmp_path, monkeypatch, archive):
    path, _ = seed(tmp_path, monkeypatch)
    preview = inspect(tmp_path)
    result = inspect(tmp_path, apply=preview['fingerprint'])
    saved = path.read_bytes()
    backup = tmp_path / result['backup'] / archive
    backup.write_bytes(b'not the acknowledged recovery plan')
    with pytest.raises(recovery.JournalRecoveryError):
        inspect(tmp_path, apply=preview['fingerprint'])
    assert path.read_bytes() == saved


def test_unsupported_volume_refuses_before_backup_or_mutation(tmp_path, monkeypatch):
    path, original = seed(tmp_path, monkeypatch)
    monkeypatch.setattr(effect_store, 'filesystem_identity', lambda _: None)
    with pytest.raises(recovery.JournalRecoveryError) as raised:
        inspect(tmp_path)
    assert raised.value.code == 'unsupported' and path.read_bytes() == original
    assert not (tmp_path / 'plugin-workers/journal-recovery').exists()


@pytest.mark.parametrize('home,environment,apply', [('missing', 'sandbox', None), ('existing', 'other', None),
                                                  ('existing', 'sandbox', 'bad'), ('existing', 'sandbox', 5)])
def test_invalid_runtime_environment_or_fingerprint_is_actionable(tmp_path, home, environment, apply):
    root = tmp_path / 'missing' if home == 'missing' else tmp_path
    with pytest.raises(recovery.JournalRecoveryError) as raised:
        recovery.recover_journal_locks(root, environment, apply=apply)
    assert raised.value.code in {'invalid_runtime', 'conflict'}


def test_empty_recovery_plan_is_a_stable_nonmutating_noop(tmp_path):
    preview = inspect(tmp_path)
    assert preview['count'] == 0
    result = inspect(tmp_path, apply=preview['fingerprint'])
    assert result['status'] == 'complete' and result['repaired'] == 0 and result['backup'] is None


def evidence():
    return evidence_record(CommitEvidence('fictional', 'sandbox', '11111111-1111-4111-8111-111111111111',
                                         '22222222-2222-4222-8222-222222222222', 7, 'prior', 'primary', 'a' * 64))


def intent_body():
    return {'format': 1, 'pending': {'operation_id': 'pending-write', 'fingerprint': 'b' * 64,
                                    'before': evidence(), 'request': None}, 'completed': []}


def test_every_journal_owner_preserves_private_payloads_and_uncertain_outcomes(tmp_path, monkeypatch):
    common_scope = {'budget_id': 'fictional', 'environment': 'sandbox', 'plugin_id': 'fictional'}
    namespace_scope = {'budget': 'fictional', 'environment': 'sandbox', 'plugin': 'fictional'}
    bank_record = {'id': 'a' * 32, 'generation': 4, 'kind': 'update', 'created': 1000, 'deadline': 2000,
                   'stage': 'exchange_uncertain', 'input': base64.b64encode(b'fictional-opaque-ciphertext').decode(),
                   'response': None, 'result': None, 'operation': 'retained-exchange', 'fingerprint': 'c' * 64,
                   'native_fingerprint': None, 'native_request': None}
    cases = [
        ('visual-ui/visual-selection.json', VISUAL_SCOPE, VISUAL_BODY),
        ('plugin-workers/namespace/namespace-effects.json', namespace_scope,
         {'generation': 0, 'materialized': 0, 'namespace': NamespaceSnapshot({'model': 'fictional'}, {'weights.bin': b'weights'}).record(), 'receipts': []}),
        ('plugin-workers/bank/bank-continuations.json', namespace_scope, {'formatVersion': 2, 'records': [bank_record]}),
        ('plugin-workers/native/storage-intents.json', {**common_scope, 'instance_id': evidence()['instance_id']}, intent_body()),
        ('plugin-workers/storage/storage-upgrade.json', common_scope,
         {'format': 2, 'kind': 'storage-code', 'active': None, 'native': None, 'pending': None, 'repair': None}),
        ('plugin-workers/writer/storage-writer.json', common_scope, {'format': 1}),
        ('remote-client/remote-client.json', {'owner': 'remote-client', 'budget_id': 'fictional', 'environment': 'sandbox'},
         {'format': 1, 'binding': 'd' * 64, 'origin_plugin': 'client', 'original_source': None,
          'choice': {'plugin': 'client', 'adapter': client_record(RemoteLedgerClient()), 'enabled': True, 'worker': None},
          'decisions': [], 'pending': {'request_id': '33333333-3333-4333-8333-333333333333',
                                      'fingerprint': 'e' * 64, 'expected_revision': 'unchanged', 'binding': 'd' * 64},
          'acknowledged': None}),
    ]
    originals = []
    for relative, scope, body in cases:
        originals.append(seed(tmp_path, monkeypatch, relative, scope=scope, body=body))
    preview = inspect(tmp_path)
    assert preview['count'] == 7
    result = inspect(tmp_path, apply=preview['fingerprint'])
    assert result['repaired'] == 7
    for path, before in originals:
        old, new = json.loads(before), json.loads(path.read_bytes())
        assert new['format'] == 2
        assert {key: value for key, value in new.items() if key not in {'format', 'lock'}} == {
            key: value for key, value in old.items() if key not in {'format', 'lock'}}
    assert inspect(tmp_path, apply=preview['fingerprint'])['repaired'] == 0


@pytest.mark.parametrize('field,value', [('budget_id', 'foreign'), ('environment', 'production'),
                                       ('instance_id', '44444444-4444-4444-8444-444444444444')])
def test_native_request_evidence_must_match_its_retained_scope(tmp_path, monkeypatch, field, value):
    scope = {'budget_id': 'fictional', 'environment': 'sandbox', 'plugin_id': 'fictional',
             'instance_id': evidence()['instance_id']}
    path, _ = seed(tmp_path, monkeypatch, 'plugin-workers/native/storage-intents.json', scope=scope, body=intent_body())
    record = json.loads(path.read_bytes())
    record['body']['pending']['before'][field] = value
    path.write_bytes(effect_store.encode(record, 2 * 1024 * 1024))
    before = path.read_bytes()
    with pytest.raises(recovery.JournalRecoveryError):
        inspect(tmp_path)
    assert path.read_bytes() == before


def test_interrupted_backup_preparation_is_retryable_before_any_identity_changes(tmp_path, monkeypatch):
    path, original = seed(tmp_path, monkeypatch)
    preview = inspect(tmp_path)
    link = recovery.os.link
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise OSError('fictional backup publication interruption')
        return link(*args, **kwargs)

    with monkeypatch.context() as failure:
        failure.setattr(recovery.os, 'link', fail_second)
        with pytest.raises(recovery.JournalRecoveryError):
            inspect(tmp_path, apply=preview['fingerprint'])
    assert path.read_bytes() == original
    result = inspect(tmp_path, apply=preview['fingerprint'])
    assert result['repaired'] == 1
    assert (tmp_path / result['backup'] / '0.before').read_bytes() == original
    assert not list((tmp_path / result['backup']).glob('.recovery-*'))


def test_preview_creates_a_private_host_ownership_lock_when_missing(tmp_path, monkeypatch):
    path, original = seed(tmp_path, monkeypatch)
    inspect(tmp_path)
    lock = tmp_path / 'run/sandbox/host.lock'
    assert lock.exists() and lock.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes() == original


@pytest.mark.parametrize('target', ['native', 'pending', 'repair'])
@pytest.mark.parametrize('field,value', [('budget_id', 'foreign'), ('environment', 'production')])
def test_storage_upgrade_evidence_must_match_its_retained_scope(tmp_path, monkeypatch, target, field, value):
    scope = {'budget_id': 'fictional', 'environment': 'sandbox', 'plugin_id': 'fictional'}
    code = {'identifier': 'a' * 64, 'distribution': 'whab-fictional', 'version': '1.0.0'}
    body = {'format': 2, 'kind': 'storage-code', 'active': code, 'native': evidence(), 'pending': None, 'repair': None}
    if target == 'pending':
        body['pending'] = {'source': None, 'candidate': code, 'evidence': evidence()}
    elif target == 'repair':
        body['repair'] = {'expected_revision': 'b' * 64, 'environment': code, 'native': evidence()}
    path, _ = seed(tmp_path, monkeypatch, 'plugin-workers/storage/storage-upgrade.json', scope=scope, body=body)
    record = json.loads(path.read_bytes())
    row = (record['body']['native'] if target == 'native' else record['body']['pending']['evidence']
           if target == 'pending' else record['body']['repair']['native'])
    row[field] = value
    path.write_bytes(effect_store.encode(record, 64 * 1024))
    before = path.read_bytes()
    with pytest.raises(recovery.JournalRecoveryError):
        inspect(tmp_path)
    assert path.read_bytes() == before


def legacy_remount(path):
    record = json.loads(path.read_bytes())
    record['lock']['device'] = str(int(record['lock']['device']) + 1)
    path.write_bytes(effect_store.encode(record, 128 * 1024 * 1024))


def test_namespace_exact_retry_after_recovery_returns_original_receipt_without_recommit(tmp_path, monkeypatch):
    root = tmp_path / 'plugin-workers/namespace'
    scope = NamespaceScope('fictional', 'sandbox', 'fictional')
    initial, saved = NamespaceSnapshot({}, {}), NamespaceSnapshot({'model': 'saved'}, {'weights.bin': b'fictional weights'})
    with monkeypatch.context() as legacy:
        legacy.setattr(effect_store, 'filesystem_identity', lambda _: None)
        journal = NamespaceJournal(root, scope, initial)
        operation = NamespaceOperation('retained-operation', 'train', journal.generation, journal.snapshot.digest, b'fictional input')
        receipt = journal.commit(operation, saved, b'original result').receipt
        journal.close()
    path = root / 'namespace-effects.json'
    legacy_remount(path)
    preview = inspect(tmp_path)
    inspect(tmp_path, apply=preview['fingerprint'])
    recovered = path.read_bytes()
    journal = NamespaceJournal(root, scope, initial)
    try:
        assert journal.lookup(operation).receipt == receipt
        repeated = journal.commit(operation, NamespaceSnapshot({'must': 'not replace saved'}, {}), b'not the original')
        assert repeated.receipt == receipt and repeated.replayed
        assert journal.snapshot.digest == saved.digest and journal.generation == 1
        assert path.read_bytes() == recovered
    finally:
        journal.close()


def test_bank_uncertain_exchange_stays_fenced_without_reexchange_after_recovery(tmp_path, monkeypatch):
    root = tmp_path / 'plugin-workers/bank'
    scope = ContinuationScope('fictional', 'sandbox', 'fictional', 4)
    cipher = AESGCM(b'\x11' * 32)

    def seal(raw, aad):
        nonce = os.urandom(12)
        return nonce + cipher.encrypt(nonce, raw, aad)

    def unseal(raw, aad):
        return cipher.decrypt(raw[:12], raw[12:], aad)

    def coordinator():
        return BankContinuations(root, scope.budget_id, scope.environment, scope.provider_id,
                                 seal=seal, open=unseal, clock=lambda: 1000)

    calls = []

    def unknown_reply(_):
        calls.append(True)
        raise OSError('fictional provider response loss')

    with monkeypatch.context() as legacy:
        legacy.setattr(effect_store, 'filesystem_identity', lambda _: None)
        bank = coordinator()
        flow = bank.begin(scope, 'update')
        with pytest.raises(effect_store.EffectError) as uncertain:
            bank.exchange(scope, flow.identifier, 'retained-exchange', b'fictional request', unknown_reply)
        assert uncertain.value.code == 'exchange_uncertain'
        bank.close()
    path = root / 'bank-continuations.json'
    legacy_remount(path)
    preview = inspect(tmp_path)
    inspect(tmp_path, apply=preview['fingerprint'])
    recovered = path.read_bytes()
    bank = coordinator()
    try:
        assert bank.status(scope, flow.identifier).stage == 'exchange_uncertain'
        with pytest.raises(effect_store.EffectError) as refused:
            bank.exchange(scope, flow.identifier, 'retained-exchange', b'fictional request', unknown_reply)
        assert refused.value.code == 'exchange_uncertain'
        assert calls == [True] and path.read_bytes() == recovered
    finally:
        bank.close()
