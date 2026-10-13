"""Real plugin discovery, optional Plaid and bank lifecycle regressions."""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.plugins import Plugin, PluginError, PluginRegistry, WebhookResult
from whab.storage.sqlite import Store


def settings(plugins=(), *, home=Path("unused-test-home"), ui="web"):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", plugins, ui=ui)


def test_plaid_can_be_disabled_without_importing_its_code(tmp_path):
    # A subprocess avoids contamination from Plaid integration tests in this process.
    script = """
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from whab.app import create_app
from whab.config import Settings
settings = Settings(Path(sys.argv[1]), 5173, "sandbox", "", "", "", False, "", "", ())
with TestClient(create_app(settings=settings), base_url="http://127.0.0.1:5173") as client:
    for page in ("budget", "transactions", "insights", "accounts"):
        assert client.get("/?page=" + page).status_code == 200
    assert [p["id"] for p in client.get("/api/plugins").json()["plugins"]] == ["sqlite", "web"]
    assert client.post("/api/plaid/link-token", json={}).status_code == 404
    assert client.post("/api/banks/plaid/link-token", json={}).status_code == 404
    assert client.get("/plugins/plaid/static/connect.js").status_code == 404
    assert client.get("/api/status").json()["enabled"] is False
    assert client.post("/api/accounts", json={"workspace":"personal","name":"Cash","type":"depository","balance":100}).status_code == 200
assert not any(name.startswith("whab.integrations.plaid") for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-c", script, str(tmp_path)], check=True, capture_output=True, text=True)


def test_bundled_plaid_is_loaded_through_entry_point_and_keeps_legacy_routes(tmp_path):
    with TestClient(
        create_app(settings=settings(("plaid",), home=tmp_path)), base_url="http://127.0.0.1:5173"
    ) as client:
        descriptors = client.get("/api/plugins").json()["plugins"]
        assert descriptors[0]["id"] == "plaid"
        assert descriptors[0]["has_bank"] is True
        for prefix in ("/api/plaid", "/api/banks/plaid"):
            assert client.get(prefix + "/items").json() == {"items": []}
            assert client.post(prefix + "/link-token", json={}).status_code == 400
            assert client.post(prefix + "/webhook", json={}).status_code == 401
        page = client.get("/?page=accounts&workspace=personal")
        assert "Finish Plaid setup" in page.text
        assert "/plugins/plaid/static/connect.js" in page.text
        assert client.get("/plugins/plaid/static/connect.js").status_code == 200


class FakeBank:
    config = {"configured": True, "missingConfig": [], "secret": "must-not-render"}

    def __init__(self, events):
        self.events = events

    async def link_token(self, item_id=None):
        return "fictional-link-token"

    async def exchange(self, public_token, institution_name=None):
        pass

    async def sync(self, item_id=None):
        return {"added": 0, "modified": 0, "removed": 0, "secret": "must-not-render"}

    async def disconnect(self, item_id):
        self.events.append(("disconnected", item_id))

    async def webhook(self, raw, headers):
        return WebhookResult(ok=False, status_code=401)

    async def close(self):
        self.events.append("closed")


def test_another_bank_uses_same_lifecycle_routes_and_scoped_store():
    events, contexts = [], []

    def factory(context):
        events.append("started")
        contexts.append(context)
        return FakeBank(events)

    plugins = PluginRegistry((Plugin("fictional", "Fictional bank", bank=factory),))
    store = Store(":memory:", month="2026-10")
    store.save_item("plaid-item", "fictional-ciphertext", "Other provider")
    try:
        with TestClient(
            create_app(store=store, settings=settings(ui=""), plugins=plugins),
            base_url="http://127.0.0.1:5173",
        ) as client:
            assert events == ["started"]
            assert contexts[0].environment == "sandbox"
            assert contexts[0].store.items() == []
            assert client.get("/api/banks/fictional/items").json() == {"items": []}
            assert client.post("/api/banks/fictional/link-token", json={}).json() == {
                "link_token": "fictional-link-token"
            }
            assert "secret" not in client.post("/api/banks/fictional/sync", json={}).text
            assert client.post("/api/plaid/link-token", json={}).status_code == 404
            assert client.delete("/api/banks/fictional/items/tenant%2Fitem").status_code == 200
            assert events[-1] == ("disconnected", "tenant/item")
            # Signed webhooks may arrive from a public host but must still be verified by the plugin.
            assert (
                client.post(
                    "/api/banks/fictional/webhook", json={}, headers={"host": "public.example"}
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/banks/fictional/sync", json={}, headers={"host": "public.example"}
                ).status_code
                == 403
            )
        assert events == ["started", ("disconnected", "tenant/item"), "closed"]
    finally:
        store.close()


def test_startup_failure_closes_previously_started_bank(store):
    events = []

    def fail(_context):
        raise RuntimeError("Fictional factory failed")

    plugins = PluginRegistry(
        (
            Plugin("first", "First bank", bank=lambda _context: FakeBank(events)),
            Plugin("second", "Second bank", bank=fail),
        )
    )
    with pytest.raises(PluginError, match="could not start"):
        with TestClient(create_app(store=store, settings=settings(ui=""), plugins=plugins)):
            pass
    assert events == ["closed"]


@pytest.mark.parametrize("invalid", [None, object(), {"config": {}}])
def test_invalid_bank_factory_result_fails_at_startup(invalid, store):
    plugins = PluginRegistry((Plugin("invalid", "Invalid bank", bank=lambda _context: invalid),))
    with pytest.raises(PluginError, match="could not start"):
        with TestClient(create_app(store=store, settings=settings(ui=""), plugins=plugins)):
            pass


def test_async_factory_is_rejected_without_leaking_a_coroutine(store):
    async def factory(_context):
        return FakeBank([])

    plugins = PluginRegistry((Plugin("invalid", "Invalid bank", bank=factory),))
    with pytest.raises(PluginError, match="could not start"):
        with TestClient(create_app(store=store, settings=settings(ui=""), plugins=plugins)):
            pass
