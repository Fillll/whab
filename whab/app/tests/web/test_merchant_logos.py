"""Host merchant-artwork routes preserve local guards and defer provider requests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.support.bank_logos import PNG
from tests.support.bank_logos import imported_store as imported_store
from tests.support.ui import make_ui
from whab.app import create_app
from whab.config import Settings
from whab.plugins import LogoImage, MerchantLogos, Plugin, PluginRegistry

PATH = "/api/plugins/plaid/merchant-logos/merchant-Fixture_123"


def client_for(store, home, fetch=None):
    settings = Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui="fixture-ui")
    ui = make_ui(home)
    (ui.ui.templates / "base.html").write_text(
        "{% for id, url in merchant_logos.items() %}{{ id }} {{ url }}{% endfor %}"
    )
    bank = SimpleNamespace(config={"configured": False}, close=AsyncMock())
    optional = (
        (Plugin("plaid", "Plaid fixture", bank=lambda _: bank, merchant_logos=MerchantLogos(fetch)),)
        if fetch is not None else ()
    )
    return TestClient(
        create_app(store=store, settings=settings, plugins=PluginRegistry((ui, *optional)),
                   bank_services={"plaid": bank}),
        base_url="https://127.0.0.1:5173",
    )


def test_image_route_has_local_guards_and_a_bounded_server_cache(store, tmp_path):
    calls = []
    with client_for(store, tmp_path, lambda key: calls.append(key) or LogoImage(PNG, "image/png")) as client:
        for headers in (
            {"host": "foreign.example"},
            {"origin": "https://foreign.example"},
            {"sec-fetch-site": "cross-site"},
        ):
            assert client.get(PATH, headers=headers).status_code == 403
        assert calls == []
        for _ in range(2):
            response = client.get(PATH)
            assert response.status_code == 200 and response.content == PNG
            assert response.headers["content-type"] == "image/png"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
        assert calls == ["merchant-Fixture_123"]
        assert client.get(PATH.replace("/plaid/", "/disabled/")).status_code == 404
        assert client.get(PATH.replace("merchant-Fixture_123", "invalid!key")).status_code == 404
        assert calls == ["merchant-Fixture_123"]


@pytest.mark.parametrize("failure", ["missing", "exception", "malformed"])
def test_artwork_errors_return_sanitized_404_without_breaking_pages(store, tmp_path, failure):
    def fetch(_):
        if failure == "exception":
            raise RuntimeError("PRIVATE_FIXTURE_ERROR")
        if failure == "malformed":
            image = LogoImage(PNG, "image/png")
            object.__setattr__(image, "data", b"<html>PRIVATE_FIXTURE_ERROR</html>")
            return image
        return None

    with client_for(store, tmp_path, fetch) as client:
        response = client.get(PATH)
        assert response.status_code == 404 and "PRIVATE_FIXTURE_ERROR" not in response.text
        assert client.get("/budgets/demo/transactions/2026-10/usd").status_code == 200


def test_page_projects_only_enabled_providers_public_keys_and_never_fetches(imported_store, tmp_path):
    imported_store.apply_sync(
        "synthetic-item",
        {"added": [{
            "id": "fictional-purchase", "accountId": "synthetic-checking", "date": "2026-10-10",
            "merchant": "Fictional shop", "merchantLogo": "merchant-Fixture_123", "amount": 100,
            "suggestedCategoryId": None, "pending": False, "source": "plaid",
            "inferredTransfer": False, "pendingTransactionId": None,
        }], "modified": [], "removed": []},
        "next-fixture-cursor",
    )
    with client_for(imported_store, tmp_path, lambda _: pytest.fail("Page must not fetch artwork")) as client:
        page = client.get("/budgets/home/transactions/2026-10/usd")
        assert page.status_code == 200 and PATH in page.text
        assert "fictional-purchase" in page.text
        assert "plaid.com" not in page.text
        assert PATH not in client.get("/budgets/demo/transactions/2026-10/usd").text
        assert PATH not in client.get("/budgets/home/transactions/2026-09/usd").text
        transaction = next(t for t in client.get("/api/workspace?workspace=personal&month=2026-10").json()["transactions"] if t["id"] == "fictional-purchase")
        assert transaction["merchantLogo"] == "merchant-Fixture_123"
    with client_for(imported_store, tmp_path) as client:
        assert PATH not in client.get("/budgets/home/transactions/2026-10/usd").text
        assert client.get(PATH).status_code == 404
