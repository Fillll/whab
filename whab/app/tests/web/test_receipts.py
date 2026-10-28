"""Private raster upload/view/delete stays local, scoped and portable."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tests.receipt_images import png
from whab.app import create_app
from whab.budgets.container import decode_container
from whab.config import Settings
from whab.domain.receipts import MAX_RECEIPT_BYTES
from whab.plugins import PluginRegistry


def client_for(store, tmp_path):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ui="")
    return TestClient(create_app(store=store, settings=settings, plugins=PluginRegistry()), base_url="https://127.0.0.1:5173")


def add_transaction(client):
    account = client.post("/api/accounts", json={"workspace": "personal", "name": "Synthetic cash", "type": "depository", "balance": 0}).json()["accountId"]
    client.post("/api/transactions", json={"workspace": "personal", "accountId": account, "date": "2026-10-01", "merchant": "Synthetic shop", "amount": 50, "categoryId": "groceries"})
    return client.get("/api/workspace?workspace=personal&month=2026-10").json()["transactions"][0]["id"]


def test_receipt_upload_view_replace_and_delete_are_local_and_do_not_change_ledger(store, tmp_path):
    with client_for(store, tmp_path) as client:
        key = add_transaction(client)
        endpoint = f"/api/transactions/{key}/receipt?workspace=personal"
        before = store.export_budget()
        assert client.get(endpoint).status_code == 404
        assert client.get("/api/receipts?workspace=personal").json() == {"supported": True, "transactionIds": []}
        assert client.put(endpoint, content=png(), headers={"content-type": "image/png"}).status_code == 200
        assert client.get("/api/receipts?workspace=personal").json()["transactionIds"] == [key]
        image = client.get(endpoint)
        assert image.content == png() and image.headers["content-type"] == "image/png"
        assert image.headers["cache-control"] == "no-store"
        assert image.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in image.headers["content-security-policy"]
        assert store.export_budget() == before
        assert client.put(endpoint, content=png(2, 1), headers={"content-type": "image/png"}).status_code == 200
        assert client.get(endpoint).content == png(2, 1)
        assert client.get(endpoint.replace("personal", "demo")).status_code == 400
        assert client.get(endpoint, headers={"host": "untrusted.invalid"}).status_code == 403
        assert client.get(endpoint, headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert client.get("http://127.0.0.1:5173" + endpoint).status_code == 400
        assert client.delete(endpoint).status_code == 200
        assert client.get(endpoint).status_code == 404


def test_native_receipt_routes_forward_scoped_lookup_without_loading_history(store, tmp_path, monkeypatch):
    with client_for(store, tmp_path) as client:
        key = add_transaction(client)
        monkeypatch.setattr(store, "transactions", lambda *_: pytest.fail("Receipt access must not load transaction history."))
        endpoint = f"/api/transactions/{key}/receipt?workspace=personal"
        assert client.get(endpoint).status_code == 404
        assert client.put(endpoint, content=png(), headers={"content-type": "image/png"}).status_code == 200
        assert client.get(endpoint).content == png()
        assert client.delete(endpoint).status_code == 200
        assert client.get(endpoint).status_code == 404


def test_receipt_types_lengths_workspace_and_transaction_are_checked(store, tmp_path):
    with client_for(store, tmp_path) as client:
        key = add_transaction(client)
        endpoint = f"/api/transactions/{key}/receipt?workspace=personal"
        for media_type, body, expected in [
            ("image/svg+xml", b"<svg/>", 415), ("application/json", b"{}", 415),
            ("image/jpeg", png(), 400), ("image/png", b"not an image", 400),
            ("image/png", b"x" * (MAX_RECEIPT_BYTES + 1), 413),
        ]:
            assert client.put(endpoint, content=body, headers={"content-type": media_type}).status_code == expected
        assert client.put(endpoint, content=png(), headers={"content-type": "image/png", "content-length": "oops"}).status_code == 400
        assert client.put(endpoint, content=png(), headers={"content-type": "image/png", "origin": "https://untrusted.invalid"}).status_code == 403
        assert client.put(endpoint.replace(key, "missing"), content=png(), headers={"content-type": "image/png"}).status_code == 400
        scoped = f"/budgets/demo/api/transactions/{key}/receipt?workspace=personal"
        assert client.put(scoped, content=png(), headers={"content-type": "image/png"}).status_code == 400
        assert client.put(endpoint, content=iter([b"x" * 1000000] * 6), headers={"content-type": "image/png"}).status_code == 413


def test_receipts_survive_portable_budget_checkpoint_export_and_isolate_other_budgets(tmp_path):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ui="")
    with TestClient(create_app(settings=settings), base_url="https://127.0.0.1:5173") as client:
        first = client.post("/api/budget/new", json={"name": "Synthetic first"}).json()["id"]
        key = add_transaction(client)
        endpoint = f"/budgets/{first}/api/transactions/{key}/receipt?workspace=personal"
        assert client.put(endpoint, content=png(), headers={"content-type": "image/png"}).status_code == 200
        raw = client.get(f"/budgets/{first}/api/budget/export").content
        assert "plugins/sqlite/budget.sqlite" in decode_container(raw).files
        second = client.post("/api/budget/new", json={"name": "Synthetic second"}).json()["id"]
        assert client.get(endpoint).content == png()
        assert client.get(endpoint.replace(first, second)).status_code == 400
        assert client.get(f"/budgets/{second}/api/receipts?workspace=personal").json()["transactionIds"] == []
    with TestClient(create_app(settings=replace(settings, budget_path=tmp_path / "budgets" / first / "sandbox" / "budget.whab")), base_url="https://127.0.0.1:5173") as client:
        assert client.get(endpoint).content == png()


def test_optional_receipt_storage_is_reported_without_local_fallback(store, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "save_receipt", None)
    with client_for(store, tmp_path) as client:
        key = add_transaction(client)
        assert client.get("/api/receipts?workspace=personal").json() == {"supported": False, "transactionIds": []}
        response = client.put(f"/api/transactions/{key}/receipt?workspace=personal", content=png(), headers={"content-type": "image/png"})
        assert response.status_code == 400 and "does not support" in response.json()["error"]


def test_original_workspace_and_demo_exports_keep_their_own_receipt_bytes(store, tmp_path):
    from whab_plugin_sqlite.portable import read_files

    with client_for(store, tmp_path) as client:
        personal_id = add_transaction(client)
        demo_id = store.transactions("demo")[0]["id"]
        for workspace, key, image in [("personal", personal_id, png()), ("demo", demo_id, png(2, 1))]:
            assert client.put(f"/api/transactions/{key}/receipt?workspace={workspace}", content=image, headers={"content-type": "image/png"}).status_code == 200
            exported = client.get(f"/api/budget/export?workspace={workspace}")
            assert exported.status_code == 200
            container = decode_container(exported.content)
            files = {name.removeprefix("plugins/sqlite/"): raw for name, raw in container.files.items()}
            snapshot, receipts = read_files(files, with_receipts=True)
            assert receipts == [(key, image)]
            assert any(transaction["id"] == key for transaction in snapshot["transactions"])


def test_pdf_upload_round_trip_and_export_preserve_documents_and_ledger(store, tmp_path):
    from whab_plugin_sqlite.portable import read_files

    from tests.receipt_images import pdf, webp

    document = pdf()
    with client_for(store, tmp_path) as client:
        key = add_transaction(client)
        endpoint = f"/api/transactions/{key}/receipt?workspace=personal"
        before = store.export_budget()
        assert client.put(endpoint, content=document, headers={"content-type": "application/pdf"}).status_code == 200
        saved = client.get(endpoint)
        assert saved.content == document and saved.headers["content-type"] == "application/pdf"
        assert saved.headers["content-disposition"] == 'inline; filename="receipt.pdf"'
        assert saved.headers["cache-control"] == "no-store" and saved.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in saved.headers["content-security-policy"]
        assert store.export_budget() == before
        container = decode_container(client.get("/api/budget/export?workspace=personal").content)
        _, receipts = read_files({name.removeprefix("plugins/sqlite/"): raw for name, raw in container.files.items()}, with_receipts=True)
        assert receipts == [(key, document)]
        for raw, media_type, expected in [(webp(), "image/webp", 415), (webp(), "image/png", 400), (png(), "application/pdf", 400), (document[:-10], "application/pdf", 400)]:
            assert client.put(endpoint, content=raw, headers={"content-type": media_type}).status_code == expected
            assert client.get(endpoint).content == document
        # Existing WebP remains readable/exportable through the storage compatibility path.
        store.save_receipt("personal", key, webp())
        assert client.get(endpoint).content == webp()
        assert client.get(endpoint).headers["content-type"] == "image/webp"
