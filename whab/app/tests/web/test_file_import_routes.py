"""Local-only preview and commit transport with synthetic records and raw uploads."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.budgets.host import BudgetHost
from whab.config import Settings
from whab.plugins import FileImport, Plugin, PluginRegistry, StoreError, WorkspaceImport

RAW = b"fictional-export"
HEADERS = {"content-type": "application/zip", "x-import-currency": "USD"}
BASE = "/api/plugins/dump/import/"


def payload():
    return WorkspaceImport(
        accounts=({"id": "cash", "name": "Fictional cash", "institution": "Export",
                   "type": "other", "balance": 10000, "source": "dump", "stale": True,
                   "balanceUnknown": True},),
        categories=({"id": "food", "name": "Food", "group": "Everyday", "target": 0},),
        transactions=({"id": "tx", "accountId": "cash", "date": "2026-09-02",
                       "merchant": "Fictional shop", "amount": 100, "categoryId": "food",
                       "pending": False, "reviewed": True, "source": "dump", "note": "Memo",
                       "isTransfer": False, "inferredTransfer": False,
                       "categoryEdited": True, "deleted": False},),
        assignments=({"month": "2026-09", "categoryId": "food", "assigned": 1000},),
        budget_start_month="2026-09", warnings=("A source limitation",),
    )


def client_for(store, home, parser=lambda raw: payload(), enabled=True, budget_host=None):
    settings = Settings(home, 5173, "sandbox", "", "", "", False, "", "", ui="")
    plugins = PluginRegistry((Plugin("dump", "Dump", file_import=FileImport(parser)),) if enabled else ())
    return TestClient(create_app(store=store, settings=settings, plugins=plugins, budget_host=budget_host),
                      base_url="https://127.0.0.1:5173")


def test_preview_is_read_only_and_confirmed_commit_preserves_annotations_on_retry(store, tmp_path):
    seen = []

    def parser(raw):
        seen.append(raw)
        return payload()

    before = store.state("personal")
    demo = store.state("demo")
    with client_for(store, tmp_path, parser) as client:
        preview = client.post(BASE + "preview", content=RAW, headers=HEADERS)
        assert preview.status_code == 200
        result = preview.json()
        assert result["transactions"] == 1 and result["canImport"] is True
        assert result["fingerprint"] == hashlib.sha256(RAW).hexdigest()
        assert result["warnings"] == ["A source limitation"]
        assert "Fictional shop" not in preview.text and "Memo" not in preview.text
        assert store.state("personal") == before
        committed = client.post(BASE + "commit", content=RAW, headers={
            **HEADERS, "x-import-fingerprint": result["fingerprint"],
        })
        assert committed.status_code == 200 and not committed.json()["alreadyImported"]
        assert store.workspace("personal", "2026-09")["budget"][0]["assigned"] == 1000
        assert store.workspace("personal", "2026-09")["summary"]["readyToAssign"] <= 0
        store.edit_transaction("personal", "tx", reviewed=False, category_id=None)
        again = client.post(BASE + "commit", content=RAW, headers={
            **HEADERS, "x-import-fingerprint": result["fingerprint"],
        })
        assert again.status_code == 200 and again.json()["alreadyImported"]
        assert store.transactions("personal")[0]["reviewed"] is False
        assert store.transactions("personal")[0]["categoryId"] is None
        assert len(store.transactions("personal")) == 1
        assert client.post(BASE + "preview", content=RAW, headers=HEADERS).json()["alreadyImported"]
    assert seen == [RAW] * 4
    assert store.state("demo") == demo


def test_fingerprint_currency_empty_upload_and_disabled_plugin_are_guarded(store, tmp_path):
    with client_for(store, tmp_path) as client:
        assert client.post(BASE + "preview", content=RAW, headers={"content-type": "application/zip"}).status_code == 400
        assert client.post(BASE + "preview", content=RAW, headers={**HEADERS, "x-import-currency": "CAD"}).status_code == 400
        assert client.post(BASE + "preview", content=b"", headers=HEADERS).status_code == 400
        assert client.post(BASE + "commit", content=RAW, headers=HEADERS).status_code == 409
        assert client.post(BASE + "commit", content=RAW, headers={**HEADERS, "x-import-fingerprint": "0" * 64}).status_code == 409
    with client_for(store, tmp_path, enabled=False) as client:
        assert client.post(BASE + "preview", json={}).status_code == 404
        assert client.post(BASE + "commit", json={}).status_code == 404
    assert store.accounts("personal") == []


def test_nonempty_or_unsupported_backend_preview_cannot_commit(store, tmp_path, monkeypatch):
    store.add_category("personal", {"name": "Custom", "group": "Mine", "target": 0})
    with client_for(store, tmp_path) as client:
        result = client.post(BASE + "preview", content=RAW, headers=HEADERS).json()
        assert not result["canImport"] and result["reason"]
        assert client.post(BASE + "commit", content=RAW, headers={**HEADERS, "x-import-fingerprint": result["fingerprint"]}).status_code == 400
        monkeypatch.setattr(store, "import_workspace", None)
        result = client.post(BASE + "preview", content=RAW, headers=HEADERS).json()
        assert not result["canImport"] and "does not support" in result["reason"]
        assert client.post(BASE + "commit", content=RAW, headers={**HEADERS, "x-import-fingerprint": result["fingerprint"]}).status_code == 400


@pytest.mark.parametrize("failure", [RuntimeError("PRIVATE_PAYEE"), StoreError("Invalid authored export")])
def test_parser_failures_are_sanitized_and_leave_no_data(store, tmp_path, failure):
    def parser(raw):
        raise failure

    with client_for(store, tmp_path, parser) as client:
        response = client.post(BASE + "preview", content=RAW, headers=HEADERS)
        assert response.status_code == 400
        assert "PRIVATE_PAYEE" not in response.text
    assert not store.accounts("personal")


def test_invalid_and_async_parser_results_are_rejected(store, tmp_path):
    async def async_parser(raw):
        return payload()

    for parser in (lambda raw: {"private": "PRIVATE_PAYEE"}, async_parser):
        with client_for(store, tmp_path, parser) as client:
            response = client.post(BASE + "preview", content=RAW, headers=HEADERS)
            assert response.status_code == 400 and "PRIVATE_PAYEE" not in response.text


def test_raw_upload_retains_local_guards_and_bounds_streams(store, tmp_path):
    with client_for(store, tmp_path) as client:
        for headers in ({"origin": "https://evil.example"}, {"host": "evil.example"}, {"sec-fetch-site": "cross-site"}):
            assert client.post(BASE + "preview", content=RAW, headers={**HEADERS, **headers}).status_code == 403
        assert client.post(BASE + "preview", json={}).status_code == 415
        assert client.post("/api/accounts", content=RAW, headers=HEADERS).status_code == 415
        assert client.post(BASE + "preview", content=RAW, headers={**HEADERS, "content-length": "invalid"}).status_code == 400
        assert client.post(BASE + "preview", content=RAW, headers={**HEADERS, "content-length": str(11 * 1024 * 1024)}).status_code == 413
        chunks = (b"x" * 1024 * 1024 for _ in range(11))
        assert client.post(BASE + "preview", content=chunks, headers=HEADERS).status_code == 413



def test_confirmed_currency_must_match_parser_before_preview_or_commit(store, tmp_path):
    from dataclasses import replace

    with client_for(store, tmp_path, lambda raw: replace(payload(), currency="EUR")) as client:
        before = store.state("personal")
        mismatch = client.post(BASE + "preview", content=RAW, headers=HEADERS)
        assert mismatch.status_code == 400
        assert store.state("personal") == before
        headers = {**HEADERS, "x-import-currency": "EUR"}
        preview = client.post(BASE + "preview", content=RAW, headers=headers)
        assert preview.status_code == 200 and preview.json()["currency"] == "EUR"
        committed = client.post(BASE + "commit", content=RAW, headers={
            **headers, "x-import-fingerprint": preview.json()["fingerprint"],
        })
        assert committed.status_code == 200
        assert {a["currency"] for a in store.accounts("personal")} == {"EUR"}


def new_headers(raw=RAW, operation=None):
    return {
        **HEADERS, "x-import-operation": operation or str(uuid4()),
        "x-import-fingerprint": hashlib.sha256(raw).hexdigest(),
    }


def test_new_budget_preview_is_read_only_and_commit_keeps_source_and_demo_intact(store, tmp_path):
    store.add_category("personal", {"name": "Source only", "group": "Mine", "target": 0})
    original = store.state("personal")
    demo = store.state("demo")
    headers = new_headers()
    with client_for(store, tmp_path) as client:
        before = set(tmp_path.rglob("*"))
        preview = client.post(BASE + "new/preview", content=RAW, headers=headers)
        assert preview.status_code == 200, preview.text
        assert preview.json()["canImport"] and preview.json()["createsNewBudget"]
        assert "new budget" in preview.json()["reason"]
        assert set(tmp_path.rglob("*")) == before
        assert client.post(BASE + "preview", content=RAW, headers=HEADERS).json()["canImport"] is False

        imported = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert imported.status_code == 200, imported.text
        result = imported.json()
        target = headers["x-import-operation"]
        assert result["id"] == target and result["opened"] and not result["alreadyImported"]
        assert result["counts"] == {"accounts": 1, "categories": 1, "transactions": 1, "assignments": 1}
        assert result["url"] == "/budgets/imported-budget/accounts/current-month/usd"
        assert "set-cookie" not in imported.headers
        assert store.state("personal") == original and store.state("demo") == demo
        assert client.get("/api/budget").json()["id"] == "default"
        state = client.get(f"/budgets/{target}/api/workspace?month=2026-09").json()
        assert state["accounts"][0]["id"] == "cash"
        assert state["transactions"][0]["id"] == "tx"
        assert state["budget"][0]["assigned"] == 1000
        current = client.get(f"/budgets/{target}/api/budget").json()
        assert current["name"] == "Imported budget" and current["storage"] == "sqlite"
        assert all(not plugin["settings"] and not plugin["state"] for plugin in current["plugins"])


def test_demo_new_import_creates_personal_budget_without_unlocking_demo_mutations(store, tmp_path):
    original = store.state("personal")
    demo = store.state("demo")
    headers = new_headers()
    scoped = "/budgets/demo" + BASE
    with client_for(store, tmp_path) as client:
        for action in ("preview", "commit"):
            old = client.post(scoped + action, content=RAW, headers=headers)
            assert old.status_code == 403
            assert store.state("personal") == original and store.state("demo") == demo
        preview = client.post(scoped + "new/preview", content=RAW, headers=headers)
        assert preview.status_code == 200 and preview.json()["createsNewBudget"]
        assert len(client.get("/api/budgets").json()["budgets"]) == 1
        committed = client.post(scoped + "new/commit", content=RAW, headers=headers)
        assert committed.status_code == 200, committed.text
        target = committed.json()["id"]
        assert target == headers["x-import-operation"]
        state = client.get(f"/budgets/{target}/api/workspace?month=2026-09").json()
        assert len(state["accounts"]) == len(state["transactions"]) == 1
        assert store.state("personal") == original and store.state("demo") == demo
        assert client.get("/api/budget").json()["id"] == "default"
        assert client.post("/budgets/demo/api/plugins/dump/import/other/commit", json={}).status_code == 403
        assert client.post("/budgets/demo/api/accounts", json={"name": "No mutation"}).status_code == 400
        mismatch = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert mismatch.status_code == 409


def test_new_import_operation_retry_survives_restart_and_rejects_changed_export(store, tmp_path):
    headers = new_headers()
    target = headers["x-import-operation"]
    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ui="")
    host = BudgetHost(configuration)
    with client_for(store, tmp_path, budget_host=host) as client:
        first = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert first.status_code == 200, first.text
        again = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert again.status_code == 200 and again.json()["alreadyImported"]
        assert again.json()["id"] == target
        assert len(client.get("/api/budgets").json()["budgets"]) == 2
        imported_app = host.applications[target][0]
        imported_session = imported_app.state.budget_session
        imported_session.write_file("dump", "annotations.json", b'{"synthetic":"keep"}')
    host = BudgetHost(configuration)
    with client_for(store, tmp_path, budget_host=host) as client:
        again = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert again.status_code == 200, again.text
        assert again.json()["alreadyImported"] and again.json()["id"] == target
        imported_app = host.applications[target][0]
        assert imported_app.state.budget_session.read_file("dump", "annotations.json") == b'{"synthetic":"keep"}'
        other = b"another-export"
        changed = client.post(BASE + "new/commit", content=other, headers=new_headers(other, target))
        assert changed.status_code == 409
        assert len(client.get("/api/budgets").json()["budgets"]) == 2
        fresh = client.post(BASE + "new/commit", content=RAW, headers=new_headers())
        assert fresh.status_code == 200 and fresh.json()["id"] != target
        assert len(client.get("/api/budgets").json()["budgets"]) == 3


def test_new_import_failure_does_not_publish_empty_budget_and_same_operation_can_retry(store, tmp_path, monkeypatch):
    from whab_plugin_sqlite import Store

    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ui="")
    host = BudgetHost(configuration)
    original = Store.import_workspace
    headers = new_headers()

    def fail(*args):
        raise StoreError("Synthetic import failure")

    with client_for(store, tmp_path, budget_host=host) as client:
        monkeypatch.setattr(Store, "import_workspace", fail)
        failed = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert failed.status_code == 400
        assert set(host.applications) == {"default"} and host.contexts == []
        assert set(host._exports) == {"default"}
        assert len(client.get("/api/budgets").json()["budgets"]) == 1
        assert not (tmp_path / "budgets" / headers["x-import-operation"] / "sandbox").exists()
        monkeypatch.setattr(Store, "import_workspace", original)
        success = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert success.status_code == 200 and not success.json()["alreadyImported"]


def test_concurrent_new_import_commit_retries_publish_one_budget(store, tmp_path):
    headers = new_headers()
    with client_for(store, tmp_path) as client:
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(
                lambda _: client.post(BASE + "new/commit", content=RAW, headers=headers),
                range(3),
            ))
        assert all(response.status_code == 200 for response in responses)
        assert sum(not response.json()["alreadyImported"] for response in responses) == 1
        assert {response.json()["id"] for response in responses} == {headers["x-import-operation"]}
        assert len(client.get("/api/budgets").json()["budgets"]) == 2


def test_interrupted_reservation_resumes_without_catalog_entry_or_duplicate(store, tmp_path):
    headers = new_headers()
    target = tmp_path / "budgets" / headers["x-import-operation"] / "sandbox"
    target.mkdir(parents=True)
    (target / "import-operation.json").write_text(json.dumps({
        "source": "default", "plugin": "dump", "fingerprint": headers["x-import-fingerprint"],
    }))
    with client_for(store, tmp_path) as client:
        assert len(client.get("/api/budgets").json()["budgets"]) == 1
        response = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == headers["x-import-operation"]
        assert len(client.get("/api/budgets").json()["budgets"]) == 2


def test_unsaved_import_never_enters_catalog_and_preserves_source(store, tmp_path, monkeypatch):
    from whab.budgets.session import BudgetSession

    original = BudgetSession.checkpoint

    def pending_import(session):
        if session.store.accounts("personal"):
            session.status = "pending"
        else:
            original(session)

    headers = new_headers()
    before = store.state("personal")
    monkeypatch.setattr(BudgetSession, "checkpoint", pending_import)
    with client_for(store, tmp_path) as client:
        response = client.post(BASE + "new/commit", content=RAW, headers=headers)
        assert response.status_code == 400 and "could not be saved" in response.text
        assert len(client.get("/api/budgets").json()["budgets"]) == 1
        assert store.state("personal") == before
        assert not (tmp_path / "budgets" / headers["x-import-operation"]).exists()


@pytest.mark.parametrize("operation", [None, "", "../escape", "not-a-uuid", str(uuid4()).upper()])
def test_new_import_requires_explicit_canonical_operation(store, tmp_path, operation):
    headers = dict(HEADERS)
    if operation is not None:
        headers["x-import-operation"] = operation
    with client_for(store, tmp_path) as client:
        for action in ("preview", "commit"):
            assert client.post(BASE + "new/" + action, content=RAW, headers=headers).status_code == 400
        assert len(client.get("/api/budgets").json()["budgets"]) == 1


def test_new_import_routes_preserve_fingerprint_local_and_upload_guards(store, tmp_path):
    headers = new_headers()
    with client_for(store, tmp_path) as client:
        bad = client.post(BASE + "new/commit", content=RAW, headers={**headers, "x-import-fingerprint": "0" * 64})
        assert bad.status_code == 409
        for action in ("preview", "commit"):
            path = BASE + "new/" + action
            assert client.post(path, content=RAW, headers={**headers, "origin": "https://evil.example"}).status_code == 403
            assert client.post(path, json={}).status_code == 415
            assert client.post(path, content=RAW, headers={**headers, "content-length": str(11 * 1024 * 1024)}).status_code == 413
            larger = b"x" * 65536
            assert client.post(path, content=larger, headers=new_headers(larger)).status_code == 200
    with client_for(store, tmp_path, enabled=False) as client:
        assert client.post(BASE + "new/preview", json={}).status_code == 404
