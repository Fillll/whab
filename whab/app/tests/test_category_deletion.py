"""Atomic category deletion protects all financial references on both backends."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from whab.domain.categories import category_group_id
from whab.plugins import LedgerService, StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json
from whab.services.ledger import RevisionConflict


@pytest.fixture(params=['sqlite', 'json'])
def deletion_store(request, tmp_path):
    if request.param == 'sqlite':
        from whab_plugin_sqlite import Store
    else:
        from whab_plugin_json import Store
    path = tmp_path / 'fictional-delete'
    handles = [Store(path, month='2026-08')]
    yield handles[0], Store, path, handles
    for handle in handles:
        handle.close()


def command(service, name, payload, *, revision=None, request_id=None):
    return service.command(name, 'personal', payload, expected_revision=revision or service.revision,
                           request_id=request_id or uuid4().hex)


def child(service, name='Fictional 🏡 home', group='Fictional maintenance'):
    return command(service, 'add_category', {'name': name, 'group': group, 'target': 1234, 'currency': 'USD'})['data']


def test_delete_audit_replay_undo_redo_reopen_and_portable_files(deletion_store, tmp_path):
    store, factory, path, handles = deletion_store
    assert store.supports_category_deletion() is True
    service = LedgerService(store)
    identifier = child(service)
    parent = category_group_id('Fictional maintenance')
    before = deepcopy(store.export_budget())
    demo = store.export_budget('demo')
    revision, intent = service.revision, uuid4().hex
    saved = command(service, 'delete_category', {'categoryId': identifier}, revision=revision, request_id=intent)
    assert saved['data'] is None
    assert command(service, 'delete_category', {'categoryId': identifier}, revision=revision, request_id=intent) == saved
    assert all(row['id'] != identifier for row in store.categories('personal'))
    assert next(row for row in store.category_tree('personal') if row['id'] == parent)['children'] == []
    entry = store.budget_history('personal')['entries'][0]
    assert entry['operation'] == 'delete_category' and entry['requestId'] == intent and entry['reversible']
    preview = store.history_preview('personal', 'undo', entry['id'])
    command(service, 'history_apply', {'action': 'undo', 'target': entry['id'], 'fingerprint': preview['fingerprint']})
    assert next(row for row in store.categories('personal') if row['id'] == identifier)['target'] == 1234
    preview = store.history_preview('personal', 'redo', entry['id'])
    command(service, 'history_apply', {'action': 'redo', 'target': entry['id'], 'fingerprint': preview['fingerprint']})
    command(service, 'delete_category_group', {'parentId': parent})
    assert all(row['id'] != parent for row in store.category_tree('personal'))
    after = store.export_budget()
    for key in ['transactions', 'accounts', 'assignments']:
        assert after[key] == before[key]
    assert after['history']['entries'][:len(before['history']['entries'])] == before['history']['entries']
    assert store.export_budget('demo') == demo
    images = store.export_budget_files()
    store.close()
    reopened = factory(path, month='2026-08')
    handles.append(reopened)
    assert reopened.export_budget() == after
    # A restarted direct service refuses old revisions, rather than replaying a new intent.
    with pytest.raises(RevisionConflict):
        command(LedgerService(reopened), 'delete_category', {'categoryId': identifier}, revision=revision, request_id=intent)
    restored = factory(tmp_path / 'fictional-copy', month='2026-08')
    handles.append(restored)
    restored.restore_budget_files('personal', images)
    assert restored.export_budget() == after
    group_entry = reopened.budget_history('personal')['entries'][0]
    assert group_entry['operation'] == 'delete_category_group'
    preview = reopened.history_preview('personal', 'undo', group_entry['id'])
    command(LedgerService(reopened), 'history_apply', {'action': 'undo', 'target': group_entry['id'], 'fingerprint': preview['fingerprint']})
    assert next(row for row in reopened.category_tree('personal') if row['id'] == parent)['children'] == []


@pytest.mark.parametrize('changes', [
    {}, {'reviewed': True}, {'pending': True}, {'deleted': True}, {'amount': -321},
])
def test_every_current_transaction_reference_blocks_deletion_without_mutation(deletion_store, tmp_path, changes):
    store, factory, _, handles = deletion_store
    service = LedgerService(store)
    identifier = child(service)
    cash = command(service, 'add_account', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})['data']
    command(service, 'add_transaction', {'accountId': cash, 'date': '2026-08-01', 'merchant': 'Fictional transaction',
            'amount': 100, 'categoryId': identifier, 'note': 'Preserve annotation'})
    snapshot = store.export_budget()
    snapshot['transactions'][0].update(changes)
    # Restore a public fictional fixture into a separate pristine backend; no SQL/private writes.
    target = factory(tmp_path / 'fictional-reference', month='2026-08')
    handles.append(target)
    snapshot.pop('history')
    target.restore_budget('personal', snapshot)
    selected = LedgerService(target)
    before, evidence, revision = target.export_budget(), target.commit_evidence(), selected.revision
    with pytest.raises(StoreError, match='transactions still reference'):
        command(selected, 'delete_category', {'categoryId': identifier})
    assert target.export_budget() == before and target.commit_evidence() == evidence and selected.revision == revision


@pytest.mark.parametrize('month,amount', [('2026-08', 0), ('2026-08', 100), ('2026-09', 50)])
def test_assignment_rows_in_any_month_block_deletion_including_zero(deletion_store, month, amount):
    store, _, _, _ = deletion_store
    service = LedgerService(store)
    identifier = child(service)
    command(service, 'add_account', {'name': 'Fictional cash', 'type': 'depository', 'balance': 10000})
    command(service, 'assign', {'categoryId': identifier, 'month': month, 'assigned': amount})
    before, revision, evidence = store.export_budget(), service.revision, store.commit_evidence()
    with pytest.raises(StoreError, match='monthly assignments'):
        command(service, 'delete_category', {'categoryId': identifier})
    assert store.export_budget() == before and service.revision == revision and store.commit_evidence() == evidence


def test_missing_wrong_kind_nonempty_and_stale_deletes_leave_state_unchanged(deletion_store):
    store, _, _, _ = deletion_store
    service = LedgerService(store)
    identifier = child(service)
    parent = category_group_id('Fictional maintenance')
    for name, payload in [('delete_category', {'categoryId': 'missing'}), ('delete_category', {'categoryId': parent}),
                          ('delete_category_group', {'parentId': identifier}), ('delete_category_group', {'parentId': parent})]:
        before, revision = store.export_budget(), service.revision
        with pytest.raises(StoreError):
            command(service, name, payload)
        assert store.export_budget() == before and service.revision == revision
    old = service.revision
    child(service, 'Concurrent child', 'Fictional concurrent')
    before = store.export_budget()
    with pytest.raises(RevisionConflict):
        command(service, 'delete_category', {'categoryId': identifier}, revision=old)
    assert store.export_budget() == before


@pytest.mark.parametrize('kind', ['old', 'false', 'incomplete'])
def test_unsupported_deletion_is_rejected_before_any_write(kind):
    def forbidden(*args):
        pytest.fail('Unsupported storage cannot mutate')
    store = SimpleNamespace(delete_category=forbidden, delete_category_group=forbidden)
    if kind != 'old':
        store.supports_category_deletion = lambda: kind != 'false'
    for name, key in [('delete_category', 'categoryId'), ('delete_category_group', 'parentId')]:
        with pytest.raises(StoreError, match='audit history'):
            command(LedgerService(store), name, {key: 'fictional-id'})


def test_storage_operation_dispatch_commits_deletion_and_exact_receipt_once(deletion_store):
    store, _, _, _ = deletion_store
    service = LedgerService(store)
    identifier = child(service)
    for name, payload in [('delete_category', {'categoryId': identifier}),
                          ('delete_category_group', {'parentId': category_group_id('Fictional maintenance')})]:
        evidence = store.commit_evidence()
        body = {'operation': name, 'parameters': payload, 'context': {'operation': name, 'actor': 'Fictional agent', 'actorKind': 'agent'}}
        op = StorageOperation(uuid4().hex, evidence.authority_epoch, evidence.generation, 'personal', 'recorded_command', canonical_storage_json(body))
        first = store.execute_operation(op)
        assert store.execute_operation(op).replayed
        assert store.budget_history('personal')['entries'][0]['operation'] == name
        assert store.operation_receipt(op.operation_id) == first.receipt


def test_native_write_failure_rolls_back_deletion_and_history(deletion_store, monkeypatch):
    store, _, _, _ = deletion_store
    service = LedgerService(store)
    identifier = child(service)
    before, revision, evidence = store.export_budget(), service.revision, store.commit_evidence()
    def fail(*args, **kwargs):
        raise OSError('Fictional disk failure')
    with monkeypatch.context() as changes:
        changes.setattr(store, '_write_state' if hasattr(store, '_write_state') else '_commit', fail)
        with pytest.raises(OSError):
            command(service, 'delete_category', {'categoryId': identifier})
    assert store.export_budget() == before and service.revision == revision and store.commit_evidence() == evidence


def test_remote_category_deletion_is_guarded_and_audited_at_the_authority(deletion_store):
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from whab_plugin_client.plugin import RemoteStore
    from whab_plugin_server.plugin import plugin

    from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router

    store, _, _, _ = deletion_store
    token = 'fictional-delete-token-' + 'a' * 32
    app = FastAPI()
    binding = BoundRemoteInterface(plugin.interface.adapter, SimpleNamespace(
        service=LedgerService(store, budget_id='fictional'), config={'SERVER_TOKEN': token}, budget_id='fictional'))
    app.include_router(create_remote_router(lambda: binding))
    with TestClient(app, base_url='https://fictional.example') as server:
        def dispatch(request):
            response = server.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, json=response.json())
        remote = RemoteStore('https://fictional.example', token, 'fictional', transport=httpx.MockTransport(dispatch))
        try:
            assert remote.supports_category_deletion() is True
            parent = remote.add_category_group('personal', 'Fictional remote empty')
            identifier = remote.add_category('personal', {'name': 'Fictional leaf', 'parentId': parent, 'target': 0})
            remote.delete_category('personal', identifier)
            assert next(row for row in remote.category_tree('personal') if row['id'] == parent)['children'] == []
            remote.delete_category_group('personal', parent)
            assert not any(row['id'] == parent for row in store.category_tree('personal'))
            assert [row['operation'] for row in store.budget_history('personal')['entries'][:2]] == ['delete_category_group', 'delete_category']
        finally:
            remote.close()
