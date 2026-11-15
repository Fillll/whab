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
        assert re.search(r'<button class="bank-mark account-logo-toggle"[^>]+aria-label="Change logo for [^"]+"[^>]*><span aria-hidden="true">U</span></button>', accounts.text)

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
        assert re.search(r'<button class="bank-mark account-logo-toggle"[^>]*><span aria-hidden="true">C</span></button>', result.text)
        assert client.get(LOGO_PATH).status_code == 404


def account_cells(text):
    return re.findall(r'<td class="account-cell"[^>]*>(.*?)</td>', text, re.S)[1:]


def test_register_account_logos_use_the_same_scoped_lazy_artwork_in_full_and_paged_rows(imported_store, tmp_path, client_for):
    seen, fetched = [], []

    def resolve(institution):
        seen.append(institution)
        return "chase" if institution == "Chase" else None

    def fetch(key):
        fetched.append(key)
        return LogoImage(PNG, "image/png")

    imported_store.update_account("personal", "synthetic-checking", {"name": '共同 🍏 <wallet> "primary"'})
    for index in range(102):
        imported_store.add_transaction("personal", {
            "accountId": "synthetic-checking" if index < 101 else "synthetic-unknown",
            "date": "2026-10-05", "merchant": f"Fictional purchase {index:03}",
            "amount": 100, "categoryId": None,
        })
    imported_store.apply_sync("synthetic-item", {"added": [{
        "id": "synthetic-pending", "accountId": "synthetic-checking", "date": "2026-10-05",
        "merchant": "Fictional purchase 102", "amount": 100, "suggestedCategoryId": None,
        "pending": True, "source": "plaid", "inferredTransfer": False, "pendingTransactionId": None,
    }], "modified": [], "removed": []}, "synthetic-next-cursor", [])
    plugin = Plugin("logos", "Fictional bank logos", bank_logos=BankLogos(resolve, fetch))
    with client_for(imported_store, tmp_path, plugin) as client:
        path = "/budgets/home/transactions/2026-10?sort=merchant:asc"
        full = client.get(path)
        assert full.status_code == 200
        cells = account_cells(full.text)
        assert len(cells) == 100
        assert all(f'data-logo-src="/budgets/home{LOGO_PATH}"' in cell for cell in cells)
        assert all('data-transaction-account-mark aria-hidden="true"' in cell for cell in cells)
        assert all('共同 🍏 &lt;wallet&gt; &#34;primary&#34;' in cell for cell in cells)
        assert all('<wallet>' not in cell for cell in cells)
        assert seen == ["Chase", "Unknown Fixture Bank"] and fetched == []
        images = re.findall(r"<img\b[^>]*\bdata-bank-logo\b[^>]*>", "".join(cells))
        assert all('alt=""' in image and 'width="18"' in image and " hidden" in image for image in images)
        assert all(re.search(r"\ssrc=", image) is None for image in images)

        fragment = client.get(path + "&tx_page=2", headers={
            "X-WHAB-Transactions-Fragment": "1", "X-WHAB-Transactions-Sidebar": "omit",
        })
        assert fragment.status_code == 200
        cells = account_cells(fragment.text)
        assert len(cells) == 3
        assert f'data-logo-src="/budgets/home{LOGO_PATH}"' in cells[0]
        assert "Fictional account" in cells[1] and "data-bank-logo" not in cells[1]
        assert 'data-transaction-account-mark aria-hidden="true"' in cells[1]
        assert f'data-logo-src="/budgets/home{LOGO_PATH}"' in cells[2]
        assert 'data-pending="true"' in fragment.text
        assert fetched == []


def test_register_disabled_bank_logos_retains_account_names_and_fallbacks(imported_store, tmp_path, client_for):
    imported_store.add_transaction("personal", {
        "accountId": "synthetic-checking", "date": "2026-10-05",
        "merchant": "Fictional purchase", "amount": 100, "categoryId": None,
    })
    with client_for(imported_store, tmp_path) as client:
        response = client.get("/budgets/home/transactions/2026-10")
        assert response.status_code == 200
        cells = account_cells(response.text)
        assert len(cells) == 1
        assert "Fictional checking" in cells[0] and "data-bank-logo" not in cells[0]
        assert 'data-transaction-account-mark aria-hidden="true"' in cells[0]
