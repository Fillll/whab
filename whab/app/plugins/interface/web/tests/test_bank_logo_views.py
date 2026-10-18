"""The Web plugin owns institution artwork markup and fallback marks."""

import re

from tests.support.bank_logos import LOGO_PATH, PNG
from tests.support.bank_logos import imported_store as imported_store
from whab.plugins import BankLogos, LogoImage, Plugin


def test_pages_resolve_only_institution_names_and_defer_local_logo_fetch(imported_store, tmp_path, client_for):
    seen, fetched = [], []

    def resolve(institution):
        seen.append(institution)
        return "chase" if institution == "Chase" else None

    def fetch(key):
        fetched.append(key)
        return LogoImage(PNG, "image/png")

    plugin = Plugin("logos", "Fictional bank logos", bank_logos=BankLogos(resolve, fetch))
    with client_for(imported_store, tmp_path, plugin) as client:
        accounts = client.get("/budgets/home/accounts/2026-10/usd")
        assert accounts.status_code == 200
        # One local resolution per institution, despite multiple accounts and placements.
        assert seen == ["Chase", "Unknown Fixture Bank"]
        assert fetched == []
        assert accounts.text.count(f'data-logo-src="/budgets/home{LOGO_PATH}"') == 4
        assert "SYNTHETIC_ENCRYPTED_TOKEN" not in accounts.text
        images = re.findall(r"<img\b[^>]*\bdata-bank-logo\b[^>]*>", accounts.text)
        assert len(images) == 4
        assert all(f'data-logo-src="/budgets/home{LOGO_PATH}"' in image and " hidden" in image for image in images)
        assert all(re.search(r"\ssrc=", image) is None for image in images)
        assert all("onerror=" not in image and 'alt=""' in image for image in images)
        assert '<span class="bank-mark" aria-hidden="true"><span>U</span></span>' in accounts.text

        seen.clear()
        budget = client.get("/budgets/home/budget/2026-10/usd")
        assert budget.status_code == 200
        assert budget.text.count(f'data-logo-src="/budgets/home{LOGO_PATH}"') == 2  # Sidebar on dashboard pages.
        assert seen == ["Chase", "Unknown Fixture Bank"] and fetched == []
        description = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "logos")
        assert description["has_bank_logos"] and description["kinds"] == ["branding"]


def test_disabled_logos_keep_existing_marks_and_no_image_route(imported_store, tmp_path, client_for):
    with client_for(imported_store, tmp_path) as client:
        result = client.get("/budgets/home/accounts/2026-10/usd")
        assert result.status_code == 200 and "data-bank-logo" not in result.text
        assert '<span class="bank-mark" aria-hidden="true"><span>C</span></span>' in result.text
        assert client.get(LOGO_PATH).status_code == 404
