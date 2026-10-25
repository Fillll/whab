"""Storage copy controls share the host API without exposing private namespace data."""

import asyncio
import json
import re
from html.parser import HTMLParser
from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def dialog(text, name):
    return re.search(rf'<dialog\b[^>]*id="{name}".*?</dialog>', text, re.S)[0]


@pytest.mark.parametrize("page", ["budget", "transactions", "accounts", "insights"])
@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_copy_management_is_addressed_accessible_and_demo_read_only(client, page, workspace):
    key = "home" if workspace == "personal" else "demo"
    response = client.get(f"/budgets/{key}/{page}/2026-10/usd")
    assert response.status_code == 200
    settings = dialog(response.text, "settings-dialog")
    assert 'storage-copies-dialog' not in response.text
    assert 'Storage copies' not in settings
    copies = dialog(response.text, "plugin-options-sqlite")
    assert 'data-storage-plugin="sqlite"' in copies
    assert 'Storage for this budget' in copies
    assert f'data-workspace="{workspace}"' in copies
    assert "data-close-dialog" in copies and ">Installed plugins</button>" in copies
    assert "No automatic" not in copies
    if workspace == "personal":
        tags = Tags(copies).tags
        status = next(attrs for _, attrs in tags if "data-storage-copy-message" in attrs)
        assert status["role"] == "status" and status["aria-live"] == "polite"
        error = next(attrs for _, attrs in tags if "data-storage-copy-error" in attrs)
        assert error["role"] == "alert" and "hidden" in error
        refresh = next(attrs for tag, attrs in tags if tag == "button" and "data-storage-copy-refresh" in attrs)
        assert refresh["type"] == "button"
        assert "data-storage-copy-panel" in copies
    else:
        assert "Demo data stays separate" in copies
        assert "data-storage-copy-refresh" not in copies
        assert "data-storage-copy-panel" not in copies
        assert ">Open my budget</a>" in copies
    for private in ("source_instance_id", "namespace_digest", "storage-instances/", "TOKEN_ENCRYPTION_KEY"):
        assert private not in copies
    context = json.loads(re.search(r'<script id="whab-context"[^>]*>(.*?)</script>', response.text, re.S)[1])
    asset = f"/budgets/{key}/ui/{context['uiGeneration']}/static/storage-copies.js"
    assert f'{asset}?v={version("whab_plugin_web")}' in response.text
    module = client.get(asset)
    assert module.status_code == 200
    assert "createStorageCopies" in module.text
    assert "location.reload" not in module.text and "innerHTML" not in module.text


def test_unsupported_native_authority_returns_read_only_metadata_without_breaking_settings(client):
    response = client.get("/api/storage/copies")
    assert response.status_code == 200
    assert response.json()["primary"]["supported"] is False
    assert response.json()["errors"]["unsupported"]["code"] == "copy_unsupported"
    assert client.get("/budgets/home/accounts/current-month/usd").status_code == 200


@pytest.mark.parametrize(("primary", "destination"), [("sqlite", "json"), ("json", "sqlite")])
def test_dialog_api_roundtrip_reports_pending_saved_promotion_and_new_primary(tmp_path, primary, destination):
    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), storage=primary)
    app = create_app(settings=configuration)

    async def settle():
        await app.state.storage_replicas._synchronize()
        if app.state.storage_replicas.tasks:
            await asyncio.gather(*tuple(app.state.storage_replicas.tasks))

    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        preview = client.post("/api/storage/copies/preview", json={"destination": destination})
        assert preview.status_code == 200 and preview.json()["supported"] is True
        attached = client.post("/api/storage/copies/transition", json={"preview_id": preview.json()["preview_id"]})
        assert attached.status_code == 202 and attached.json()["state"] == "pending"
        identifier = attached.json()["id"]
        client.portal.call(settle)
        status = client.get("/api/storage/copies").json()
        assert status["primary"]["authoritative"] is True
        assert status["primary"]["health"] == "ready"
        row = next(row for row in status["copies"] if row["id"] == identifier)
        assert row["state"] == "saved"
        assert row["watermark"]["source_generation"] == status["primary"]["generation"]
        assert isinstance(row["watermark"]["namespace_generation"], int)
        accepted = client.post(f"/api/storage/copies/{identifier}/promote", json={})
        assert accepted.status_code == 202 and accepted.json()["state"] == "queued"
        client.portal.call(settle)
        promoted = client.get("/api/storage/copies").json()
        assert promoted["primary"]["plugin_id"] == destination
        assert promoted["primary"]["authoritative"] is True
        assert promoted["primary"]["authority_epoch"] != status["primary"]["authority_epoch"]
        assert promoted["operations"][-1]["state"] == "complete"
        assert next(row for row in promoted["copies"] if row["id"] == identifier)["state"] == "promoted"
        assert "data-storage-copy-panel" in client.get("/budgets/home/accounts/current-month/usd").text
