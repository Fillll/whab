"""Real parent creation and child moves preserve fictional ledgers across stores."""

from copy import deepcopy
from uuid import uuid4

import pytest

from whab.domain.categories import category_group_id, category_tree, validate_category_groups
from whab.plugins import LedgerService, StoreError
from whab.plugins.storage_transfer import decode_local_state
from whab.services.ledger import RevisionConflict


@pytest.fixture(params=["sqlite", "json"])
def category_store(request, tmp_path):
    if request.param == "sqlite":
        from whab_plugin_sqlite import Store
    else:
        from whab_plugin_json import Store
    path = tmp_path / "fictional-budget"
    handles = [Store(path, month="2026-11")]
    yield handles[0], Store, path, handles
    for handle in handles:
        handle.close()


def command(service, name, payload, **kwargs):
    return service.command(
        name,
        "personal",
        payload,
        request_id=kwargs.get("request_id", uuid4().hex),
        expected_revision=kwargs.get("revision", service.revision),
    )["data"]


def test_empty_parent_move_history_assignments_and_portable_native_reopen(category_store, tmp_path):
    store, factory, path, handles = category_store
    service = LedgerService(store)
    cash = command(
        service, "add_account", {"name": "Fictional cash", "type": "depository", "balance": 100000}
    )
    child = command(
        service,
        "add_category",
        {
            "name": "Fictional 工作室",
            "group": "Fictional finance",
            "target": 1234,
            "currency": "USD",
        },
    )
    transaction = command(
        service,
        "add_transaction",
        {
            "accountId": cash,
            "date": "2026-11-01",
            "merchant": "Fictional invoice",
            "amount": 100,
            "categoryId": child,
            "note": "Keep me",
        },
    )
    command(service, "assign", {"categoryId": child, "month": "2026-11", "assigned": 900})
    command(service, "assign", {"categoryId": child, "month": "2026-12", "assigned": 700})
    before = deepcopy(store.export_budget())
    original_groups = service.query("category_tree", "personal")["data"]
    group = command(service, "add_category_group", {"name": "🧰 Fictional проекты"})
    tree = service.query("category_tree", "personal")["data"]
    assert [row["id"] for row in tree] == [row["id"] for row in original_groups] + [group]
    assert next(row for row in tree if row["id"] == group)["children"] == []
    assert (
        next(
            row
            for row in store.workspace("personal", "2026-11")["categoryGroups"]
            if row["id"] == group
        )["children"]
        == []
    )
    with pytest.raises(StoreError):
        command(service, "assign", {"categoryId": group, "month": "2026-11", "assigned": 1})
    command(service, "move_category", {"categoryId": child, "parentId": group})
    saved = store.export_budget()
    assert saved["transactions"] == before["transactions"]
    assert saved["assignments"] == before["assignments"]
    assert saved["accounts"] == before["accounts"]
    assert next(row for row in saved["categories"] if row["id"] == child) == {
        **next(row for row in before["categories"] if row["id"] == child),
        "group": "🧰 Fictional проекты",
    }
    assert (
        saved["history"]["entries"][: len(before["history"]["entries"])]
        == before["history"]["entries"]
    )
    tree = service.query("category_tree", "personal")["data"]
    assert next(row for row in tree if row["name"] == "Fictional finance")["children"] == []
    assert next(row for row in tree if row["id"] == group)["children"][0]["id"] == child
    assert store.transactions("personal")[0]["id"] == transaction
    snapshot = store.export_local()
    assert (
        decode_local_state(snapshot.financial, snapshot.protected)["personal"]["categoryGroups"]
        == saved["categoryGroups"]
    )
    images = store.export_budget_files()
    store.close()
    reopened = factory(path, month="2026-11")
    handles.append(reopened)
    assert reopened.category_tree("personal") == tree
    assert reopened.export_budget() == saved
    for image in [False, True]:
        restored = factory(tmp_path / ("portable-" + str(image)), month="2026-11")
        handles.append(restored)
        if image:
            restored.restore_budget_files("personal", images)
        else:
            restored.restore_budget("personal", saved)
        assert restored.category_tree("personal") == tree
        assert restored.export_budget() == saved


def test_invalid_duplicate_and_stale_hierarchy_changes_are_atomic(category_store):
    store, _, _, _ = category_store
    service = LedgerService(store)
    group = command(service, "add_category_group", {"name": "Fictional destination"})
    duplicate = command(
        service, "add_category", {"name": "Groceries", "parentId": group, "target": 0}
    )
    for op, payload in [
        ("add_category_group", {"name": " FICTIONAL DESTINATION "}),
        ("move_category", {"categoryId": "groceries", "parentId": group}),
        ("move_category", {"categoryId": "groceries", "parentId": duplicate}),
        ("move_category", {"categoryId": group, "parentId": category_group_id("Home")}),
        ("move_category", {"categoryId": "missing", "parentId": group}),
        ("move_category", {"categoryId": "groceries", "parentId": "missing"}),
    ]:
        before = store.export_budget()
        with pytest.raises(StoreError):
            command(service, op, payload)
        assert store.export_budget() == before
    old_revision = service.revision
    command(service, "add_category_group", {"name": "Concurrent fictional group"})
    before = store.export_budget()
    with pytest.raises(RevisionConflict):
        command(
            service,
            "move_category",
            {"categoryId": "rent", "parentId": group},
            revision=old_revision,
        )
    assert store.export_budget() == before
    intent = uuid4().hex
    revision = service.revision
    command(
        service,
        "move_category",
        {"categoryId": "rent", "parentId": group},
        revision=revision,
        request_id=intent,
    )
    count = store.budget_history("personal")["total"]
    command(
        service,
        "move_category",
        {"categoryId": "rent", "parentId": group},
        revision=revision,
        request_id=intent,
    )
    assert store.budget_history("personal")["total"] == count


def test_history_can_undo_redo_group_creation_and_move(category_store):
    store, _, _, _ = category_store
    service = LedgerService(store)
    legacy = deepcopy(store.export_budget())
    assert "categoryGroups" not in legacy
    group = command(service, "add_category_group", {"name": "Fictional projects"})
    entry = store.budget_history("personal")["entries"][0]["id"]
    preview = store.history_preview("personal", "undo", entry)
    store.history_apply("personal", "undo", entry, preview["fingerprint"])
    assert "categoryGroups" not in store.export_budget()
    preview = store.history_preview("personal", "redo", entry)
    store.history_apply("personal", "redo", entry, preview["fingerprint"])
    assert any(row["id"] == group for row in store.category_tree("personal"))
    service = LedgerService(store)
    command(service, "move_category", {"categoryId": "rent", "parentId": group})
    entry = store.budget_history("personal")["entries"][0]["id"]
    preview = store.history_preview("personal", "undo", entry)
    store.history_apply("personal", "undo", entry, preview["fingerprint"])
    assert (
        next(row for row in store.categories("personal") if row["id"] == "rent")["group"] == "Home"
    )


@pytest.mark.parametrize(
    "groups",
    [
        None,
        {},
        "x",
        [{}],
        [{"id": "wrong", "name": "Fictional"}],
        [{"id": category_group_id("Fictional"), "name": "Fictional"}] * 2,
    ],
)
def test_malformed_saved_groups_are_rejected(groups):
    with pytest.raises(StoreError):
        validate_category_groups(groups)
    with pytest.raises(StoreError):
        category_tree({}, groups)


def test_older_storage_can_read_derived_groups_but_cannot_mutate_them():
    from types import SimpleNamespace

    store = SimpleNamespace(
        categories=lambda workspace: [
            {"id": "child", "name": "Child", "group": "Legacy", "target": 0}
        ]
    )
    service = LedgerService(store)
    assert service.query("category_tree", "personal")["data"][0]["name"] == "Legacy"
    for op, payload in [
        ("add_category_group", {"name": "Fictional"}),
        ("move_category", {"categoryId": "child", "parentId": "missing"}),
    ]:
        with pytest.raises(StoreError, match="storage plugin"):
            command(service, op, payload)


def test_remote_public_category_management_forwards_to_the_authority(category_store):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from whab_plugin_client.plugin import RemoteStore
    from whab_plugin_server.plugin import plugin

    from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router

    store, _, _, _ = category_store
    token = "fictional-category-token-" + "a" * 32
    app = FastAPI()
    binding = BoundRemoteInterface(
        plugin.interface.adapter,
        SimpleNamespace(
            service=LedgerService(store, budget_id="fictional"),
            config={"SERVER_TOKEN": token},
            budget_id="fictional",
        ),
    )
    app.include_router(create_remote_router(lambda: binding))
    with TestClient(app, base_url="https://fictional.example") as server:

        def dispatch(request):
            response = server.request(
                request.method,
                str(request.url),
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(response.status_code, json=response.json())

        remote = RemoteStore(
            "https://fictional.example", token, "fictional", transport=httpx.MockTransport(dispatch)
        )
        try:
            group = remote.add_category_group("personal", "Fictional remote group")
            remote.move_category("personal", "rent", group)
            assert (
                next(row for row in remote.category_tree("personal") if row["id"] == group)[
                    "children"
                ][0]["id"]
                == "rent"
            )
            assert (
                next(row for row in store.categories("personal") if row["id"] == "rent")["target"]
                == 260000
            )
        finally:
            remote.close()
