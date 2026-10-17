"""Merchant artwork uses host-provided local routes without hiding payee details."""

import re

from jinja2 import Environment, FileSystemLoader, select_autoescape
from whab_plugin_web.plugin import resources
from whab_plugin_web.views import build_context

LOGO_PATH = "/api/plugins/plaid/merchant-logos/fictional-merchant"


def render_transactions(transactions, **extra):
    data = {
        "categories": [], "budget": [], "accounts": [],
        "trend": [{"month": "2026-10", "spent": 0}],
        "summary": {"spent": 0}, "transactions": transactions,
    }
    context = build_context(
        data, {}, [], workspace="personal", page="transactions", month="2026-10"
    )
    environment = Environment(
        loader=FileSystemLoader(resources / "templates"), autoescape=select_autoescape()
    )
    return environment.get_template("transactions.html").render(**context, **extra)


def transaction(identifier="purchase", **changes):
    return {
        "id": identifier, "accountId": "fixture", "date": "2026-10-01",
        "merchant": "Fictional Market", "note": "Groceries for dinner", "amount": 1500,
        "categoryId": None, "pending": False, "reviewed": False, "source": "plaid",
        **changes,
    }


def test_merchant_artwork_keeps_initial_until_loaded_and_preserves_payee_details():
    text = render_transactions(
        [transaction(pending=True)], merchant_logos={"purchase": LOGO_PATH}
    )
    images = re.findall(r"<img\b[^>]*\bdata-merchant-logo\b[^>]*>", text)
    assert len(images) == 1
    image = images[0]
    assert f'src="{LOGO_PATH}"' in image
    assert all(attribute in image for attribute in (
        'alt=""', 'width="28"', 'height="28"', 'decoding="async"', " hidden",
    ))
    assert "onerror=" not in image and "onload=" not in image
    # Hidden lazy images never enter the viewport, so they cannot trigger the load fallback.
    assert 'loading="lazy"' not in image
    assert '<span class="merchant-mark" aria-hidden="true"><span>F</span>' in text
    assert "<strong>Fictional Market</strong>" in text
    assert "<span>Groceries for dinner</span>" in text and "<span>Pending</span>" in text
    assert 'data-payee="fictional market groceries for dinner"' in text
    assert 'aria-label="Mark Fictional Market reviewed"' in text


def test_missing_merchant_artwork_keeps_text_only_and_ignores_raw_provider_values():
    transactions = [
        transaction(merchantLogo="https://untrusted.example/artwork.png"),
        transaction("manual", merchant="Manual market", source="manual"),
    ]
    for extra in ({}, {"merchant_logos": {}}):
        text = render_transactions(transactions, **extra)
        assert "data-merchant-logo" not in text and 'class="merchant-mark"' not in text
        assert "untrusted.example" not in text
        assert "<strong>Fictional Market</strong>" in text
        assert "<strong>Manual market</strong>" in text and "<span>Manual</span>" in text


def test_artwork_and_payee_content_remain_escaped():
    identifier = 'purchase" onmouseover="bad'
    text = render_transactions(
        [transaction(identifier, merchant='<script>bad</script>', note='<img src="bad">')],
        merchant_logos={identifier: LOGO_PATH + '?label="bad"&size=28'},
    )
    assert '<script>bad</script>' not in text and '<img src="bad">' not in text
    assert '<strong>&lt;script&gt;bad&lt;/script&gt;</strong>' in text
    assert '&lt;img src=&#34;bad&#34;&gt;' in text
    assert '<span class="merchant-mark" aria-hidden="true"><span>&lt;</span>' in text
    assert f'src="{LOGO_PATH}?label=&#34;bad&#34;&amp;size=28"' in text
    assert ' onmouseover="bad' not in text
    assert 'data-api="/transactions/purchase%22%20onmouseover%3D%22bad"' in text
