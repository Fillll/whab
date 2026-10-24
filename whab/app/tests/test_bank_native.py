"""Synthetic provider captures use real public native receipts, never blind retries."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from whab.budgets.session import TrackedStorage
from whab.plugins import BudgetContext, StorageContext, load_plugins
from whab.plugins.bank_continuations import BankContinuations, ContinuationScope
from whab.plugins.bank_crypto import BankCipher
from whab.plugins.effect_store import EffectError
from whab.plugins.storage import close_storage, open_storage
from whab.services.bank_native import BankNativeBroker, CapturedBankOperation
from whab.storage.bank import BankStore

SCOPE = ContinuationScope('synthetic-budget', 'sandbox', 'fictional-bank', 7)


@pytest.fixture(params=['sqlite', 'json'])
def native(tmp_path, request):
    plugin = load_plugins((request.param,)).storage_plugin(request.param)
    backend = open_storage(plugin, StorageContext(tmp_path / 'native', 'sandbox', {},
        budget=BudgetContext(SCOPE.budget_id, {}, {}, lambda _: None)))
    tracked = TrackedStorage(backend)
    scoped = BankStore(tracked, SCOPE.provider_id)
    broker = BankNativeBroker(tracked, scoped, SCOPE.budget_id, SCOPE.environment, SCOPE.provider_id)
    cipher = BankCipher('11' * 32)
    journal = BankContinuations(tmp_path / 'flows', SCOPE.budget_id, SCOPE.environment, SCOPE.provider_id,
                                seal=cipher.seal, open=cipher.open, clock=lambda: 1000)
    try:
        yield broker, journal, cipher
    finally:
        journal.close()
        close_storage(backend)


def capture(native):
    broker, journal, cipher = native
    item = 'fictional-item'
    token = cipher.encrypt_token('fictional-access-token', broker.bank_store.item_identity(item))
    flow = journal.begin(SCOPE, 'link', secret=b'fictional original Link token')
    journal.exchange(SCOPE, flow.identifier, 'synthetic-native-operation', b'fictional public token',
                     lambda _: b'fictional raw provider response')
    command = broker.prepare('synthetic-native-operation', item, 'save_item', {
        'itemId': broker.bank_store.item_identity(item), 'encryptedToken': token,
        'institutionName': 'Fictional institution', 'providerId': SCOPE.provider_id})
    journal.prepare_native(SCOPE, flow.identifier, command.operation.fingerprint, command.encode())
    return flow.identifier, command


def apply(native, flow, command, *, commit=None):
    broker, journal, _ = native
    return journal.apply(SCOPE, flow, native_fingerprint=command.operation.fingerprint,
        lookup=lambda *_: broker.lookup(command, prove_absence=True),
        commit=lambda *_: (commit or broker.execute)(command))


def test_captured_command_and_encrypted_token_use_real_native_receipt_once(native):
    broker, journal, cipher = native
    flow, command = capture(native)
    assert CapturedBankOperation.read(command.encode()) == command
    assert broker.lookup(command).outcome == 'unknown'
    assert broker.lookup(command, prove_absence=True).outcome == 'not_committed'
    assert apply(native, flow, command) == b'null'
    saved = broker.bank_store.get_item(command.item_id)
    assert cipher.decrypt_token(saved['token'], broker.bank_store.item_identity(command.item_id)) == 'fictional-access-token'
    assert broker.lookup(command).outcome == 'committed'
    evidence = broker.tracked.commit_evidence()
    assert apply(native, flow, command, commit=lambda _: pytest.fail('Completed native command must replay')) == b'null'
    assert broker.tracked.commit_evidence() == evidence
    assert journal.native_request(SCOPE, flow)[1] == command.encode()


def test_native_commit_response_loss_reconciles_receipt_without_another_native_command(native):
    broker, journal, _ = native
    flow, command = capture(native)
    calls = []
    def lost(captured):
        calls.append(True)
        broker.execute(captured)
        raise OSError('fictional private lost native response')
    with pytest.raises(EffectError) as error:
        apply(native, flow, command, commit=lost)
    assert error.value.code == 'apply_uncertain' and calls == [True]
    journal.recover()
    stored_fp, stored = journal.native_request(SCOPE, flow)
    original = CapturedBankOperation.read(stored)
    assert stored_fp == original.operation.fingerprint
    assert apply(native, flow, original, commit=lambda _: pytest.fail('Native receipt is sole commit authority')) == b'null'
    assert calls == [True]


def test_missing_receipt_with_changed_generation_cannot_authorize_a_bank_reapply(native):
    broker, journal, _ = native
    flow, command = capture(native)
    broker.tracked.add_account('personal', {'name': 'Unrelated fictional cash', 'type': 'depository', 'balance': 0})
    assert broker.lookup(command, prove_absence=True).outcome == 'unknown'
    with pytest.raises(EffectError) as error:
        apply(native, flow, command, commit=lambda _: pytest.fail('Missing receipt is not rollback proof'))
    assert error.value.code == 'apply_uncertain'
    assert broker.bank_store.get_item(command.item_id) is None
    assert journal.status(SCOPE, flow).stage == 'captured'


def test_storage_proxy_pending_intent_blocks_absence_proof_even_at_original_generation(native, monkeypatch):
    broker, _, _ = native
    _, command = capture(native)
    monkeypatch.setattr(broker.tracked, 'worker_recovery_required', lambda: True, raising=False)
    assert broker.tracked.commit_evidence() == command.before
    assert broker.lookup(command, prove_absence=True).outcome == 'unknown'


@pytest.mark.parametrize('mismatch', ['instance', 'epoch', 'generation', 'operation', 'fingerprint', 'role'])
def test_bank_native_receipt_requires_exact_captured_authority_and_command(native, monkeypatch, mismatch):
    broker, journal, _ = native
    flow, command = capture(native)
    broker.execute(command)
    receipt = broker.tracked.operation_receipt(command.operation.operation_id)
    if mismatch == 'instance':
        invalid = replace(receipt, evidence=replace(receipt.evidence, instance_id=str(uuid4())))
    elif mismatch == 'epoch':
        invalid = replace(receipt, evidence=replace(receipt.evidence, authority_epoch=str(uuid4())))
    elif mismatch == 'generation':
        invalid = replace(receipt, evidence=replace(receipt.evidence, generation=receipt.evidence.generation + 1))
    elif mismatch == 'operation':
        invalid = replace(receipt, operation_id='other-operation', evidence=replace(receipt.evidence, operation_id='other-operation'))
    elif mismatch == 'fingerprint':
        invalid = replace(receipt, fingerprint='0' * 64)
    else:
        invalid = replace(receipt, evidence=replace(receipt.evidence, role='fenced'))
    monkeypatch.setattr(broker.tracked, 'operation_receipt', lambda _: invalid)
    assert broker.lookup(command).outcome == 'unknown'
    with pytest.raises(EffectError) as error:
        apply(native, flow, command, commit=lambda _: pytest.fail('Mismatched receipt cannot complete or authorize another command'))
    assert error.value.code == 'apply_uncertain'
    assert journal.status(SCOPE, flow).stage == 'captured'
    broker.tracked.add_account('personal', {'name': 'Later acknowledged', 'type': 'depository', 'balance': 1})
    monkeypatch.setattr(broker.tracked, 'operation_receipt', lambda _: receipt)
    assert apply(native, flow, command, commit=lambda _: pytest.fail('Original exact receipt remains valid at a later native generation')) == b'null'


@pytest.mark.parametrize('mutation', ['provider', 'budget', 'fingerprint', 'extra', 'generation'])
def test_captured_native_command_scope_shape_and_fingerprint_reject_corruption(native, mutation):
    broker, _, _ = native
    _, command = capture(native)
    value = json.loads(command.encode())
    if mutation == 'provider':
        value['provider_id'] = 'other-provider'
    elif mutation == 'budget':
        value['budget_id'] = 'other-budget'
    elif mutation == 'fingerprint':
        value['fingerprint'] = '0' * 64
    elif mutation == 'extra':
        value['operation']['callback'] = 'forbidden'
    else:
        value['operation']['expected_generation'] = True
    with pytest.raises(EffectError):
        broker.validate(CapturedBankOperation.read(json.dumps(value).encode()))
    with pytest.raises(EffectError):
        broker.validate(replace(command, provider_id='other-provider'))


def test_host_cipher_preserves_existing_item_token_format_and_aad_without_sending_key_to_provider():
    from whab_plugin_plaid.service import decrypt_token, encrypt_token

    cipher = BankCipher('11' * 32)
    original = encrypt_token('fictional existing access token', '11' * 32, 'existing-item')
    assert cipher.decrypt_token(original, 'existing-item') == 'fictional existing access token'
    current = cipher.encrypt_token('fictional new access token', 'existing-item')
    assert decrypt_token(current, '11' * 32, 'existing-item') == 'fictional new access token'
    with pytest.raises(ValueError):
        cipher.decrypt_token(current, 'other-item')
    assert '11111111' not in repr(cipher)
