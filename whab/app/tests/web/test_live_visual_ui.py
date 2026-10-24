"""Public visual management preserves acknowledged data and exact asset generations."""

import asyncio
import json
import re

from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings

BASE = "https://127.0.0.1:5173"


async def settle(app):
    if app.state.visual_controller.tasks:
        await asyncio.gather(*tuple(app.state.visual_controller.tasks), return_exceptions=True)


def handover(client, app, plugin_id):
    preview = client.post("/api/ui/preview", json={"pluginId": plugin_id, "useInstalled": True})
    assert preview.status_code == 200, preview.text
    accepted = client.post("/api/ui/commit", json={"previewId": preview.json()["preview_id"]})
    assert accepted.status_code == 202, accepted.text
    duplicate = client.post("/api/ui/commit", json={"previewId": preview.json()["preview_id"]})
    assert duplicate.json()["id"] == accepted.json()["id"]
    client.portal.call(settle, app)
    operation = client.get("/api/ui/status").json()["operations"][-1]
    assert operation["preview_id"] == preview.json()["preview_id"]
    assert operation["id"] == accepted.json()["id"] and operation["state"] == "complete", operation


def test_headless_enable_disable_reenable_and_reopen_keep_ledger_and_retained_assets(tmp_path):
    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(settings=configuration)
    with TestClient(app, base_url=BASE) as client:
        ledger = app.state.ledger_service
        assert client.get("/budgets/home/accounts/current-month/usd").status_code == 404
        handover(client, app, "web")
        page = client.get("/budgets/home/accounts/current-month/usd")
        assert page.status_code == 200
        context = json.loads(re.search(r'<script id="whab-context"[^>]*>(.*?)</script>', page.text, re.S).group(1))
        generation = context["uiGeneration"]
        asset = f"/budgets/home/ui/{generation}/static/app.js"
        assert asset in page.text
        original_asset = client.get(asset)
        assert original_asset.status_code == 200 and "createUIViews" in original_asset.text
        assert original_asset.headers["cache-control"].endswith("immutable")
        saved = client.post("/api/accounts", json={"workspace": "personal", "name": "Fictional retained wallet",
                            "type": "depository", "balance": 1234, "currency": "USD"})
        assert saved.status_code == 200, saved.text
        handover(client, app, None)
        assert app.state.ui_plugin is None and app.state.ledger_service is ledger
        assert client.get("/static/app.js").status_code == 404
        assert client.get(asset).content == original_asset.content
        assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["name"] == "Fictional retained wallet"
        handover(client, app, "web")
        assert app.state.ui_plugin == "web" and app.state.ledger_service is ledger
        assert client.get("/api/ui/status").json()["active"]["resource_generation"] == generation
    reopened = create_app(settings=configuration)
    with TestClient(reopened, base_url=BASE) as client:
        assert reopened.state.ui_plugin == "web"
        assert client.get(asset).content == original_asset.content
        assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["name"] == "Fictional retained wallet"
        assert client.get("/budgets/home/accounts/current-month/usd").status_code == 200


def test_browser_acknowledgement_precedes_gate_and_control_routes_keep_local_guards(tmp_path):
    app = create_app(settings=Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="web"))
    with TestClient(app, base_url=BASE) as client:
        controller = app.state.visual_controller
        generation = client.get("/api/ui/status").json()["active"]["resource_generation"]
        registered = client.post("/api/ui/views", json={"generation": generation, "dirty": True}).json()
        view = registered["view"]
        entered = asyncio.Event()
        prepare = controller.views.prepare

        async def observe(*args, **kwargs):
            entered.set()
            return await prepare(*args, **kwargs)

        controller.views.prepare = observe
        preview = client.post("/api/ui/preview", json={"pluginId": None}).json()
        accepted = client.post("/api/ui/commit", json={"previewId": preview["preview_id"]})
        assert accepted.status_code == 202
        client.portal.call(entered.wait)
        status = client.get("/api/ui/status").json()
        assert status["browser_preparation"] and status["runtime"]["admission"] == "open"
        assert client.post("/api/accounts", json={"workspace": "personal", "name": "Fictional late write",
            "type": "depository", "balance": 0, "currency": "USD"}).status_code == 200
        for path in ("/api/ui/status", f"/api/ui/views/{view}"):
            assert client.get(path, headers={"Host": "foreign.example"}).status_code == 403
            assert client.get(path, headers={"Origin": "https://foreign.example"}).status_code == 403
            assert client.get("http://127.0.0.1:5173" + path).status_code == 400
        assert client.post("/budgets/demo/api/ui/preview", json={"pluginId": None}).status_code == 403
        acknowledgement = client.put(f"/api/ui/views/{view}", json={
            "generation": generation, "dirty": False, "prepared": status["browser_preparation"]["id"]})
        assert acknowledgement.status_code == 200
        client.portal.call(settle, app)
        assert app.state.ui_plugin is None
        assert client.get(f"/api/ui/views/{view}").json()["reload"]
        assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["name"] == "Fictional late write"
