"""Local-only preview and commit transport with synthetic records and raw uploads."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
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


def client_for(store, home, parser=lambda raw: payload(), enabled=True):
    settings = Settings(home, 5173, "sandbox", "", "", "", False, "", "", ui="")
    plugins = PluginRegistry((Plugin("dump", "Dump", file_import=FileImport(parser)),) if enabled else ())
    return TestClient(create_app(store=store, settings=settings, plugins=plugins),
                      base_url="http://127.0.0.1:5173")


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
