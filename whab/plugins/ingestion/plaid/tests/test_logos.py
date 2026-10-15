"""Plaid-supplied artwork accepts only bounded public PNGs on its known CDNs."""

import importlib

import httpx
import pytest
from whab_plaid.logos import fetch, logo_key
from whab_plaid.mapping import normalize_transaction
from whab_plaid.plugin import plugin

from whab.plugins import LogoImage

provider = importlib.import_module("whab_plaid.logos")
PNG = b"\x89PNG\r\n\x1a\nfixture"
URL = "https://plaid-merchant-logos.plaid.com/fixture_123-Ab.png"
KEY = "merchant-fixture_123-Ab"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (URL, KEY),
        ("https://plaid-counterparty-logos.plaid.com/9.png", "counterparty-9"),
        (
            f"https://plaid-merchant-logos.plaid.com/{'a' * 160}.png",
            f"merchant-{'a' * 160}",
        ),
    ],
)
def test_canonical_supplied_urls_become_opaque_keys(url, expected):
    assert logo_key(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        None, 1, {}, "", "https://example.com/icon.png", "http://plaid-merchant-logos.plaid.com/a.png",
        "https://plaid-merchant-logos.plaid.com.evil.test/a.png",
        "https://plaid-merchant-logos.plaid.com@evil.test/a.png",
        "https://evil.test@plaid-merchant-logos.plaid.com/a.png",
        "https://plaid-merchant-logos.plaid.com:443/a.png",
        "https://PLAID-MERCHANT-LOGOS.plaid.com/a.png",
        "https://plaid-merchant-logos.plaid.com/a.png?x=1",
        "https://plaid-merchant-logos.plaid.com/a.png#fragment",
        "https://plaid-merchant-logos.plaid.com/../a.png",
        "https://plaid-merchant-logos.plaid.com/%61.png",
        "https://plaid-merchant-logos.plaid.com/a/b.png",
        "https://plaid-merchant-logos.plaid.com/.png",
        "https://plaid-merchant-logos.plaid.com/_a.png",
        "https://plaid-merchant-logos.plaid.com/a.svg",
        "https://plaid-merchant-logos.plaid.com/a.PNG",
        "https://plaid-merchant-logos.plaid.com/a.png\n",
        f"https://plaid-merchant-logos.plaid.com/{'a' * 161}.png",
    ],
)
def test_unsupported_or_untrusted_urls_are_ignored(url):
    assert logo_key(url) is None


@pytest.mark.parametrize(
    "key",
    [
        None, 1, {}, "", "merchant-", "counterparty-", "bank-a", "Merchant-a",
        "merchant-a.png", "merchant-../a", "merchant-a/b", "merchant-%61",
        "merchant-_a", "merchant-a?x=1", "merchant-a#fragment", "merchant-a\n",
        f"merchant-{'a' * 161}", "https://example.com/a.png",
    ],
)
def test_invalid_keys_do_not_open_a_client(key, monkeypatch):
    def unexpected_client(**kwargs):
        pytest.fail("Invalid artwork keys must never make a request")

    monkeypatch.setattr(provider.httpx, "Client", unexpected_client)
    assert fetch(key) is None


@pytest.mark.parametrize(
    ("key", "url"),
    [
        (KEY, URL),
        ("counterparty-9", "https://plaid-counterparty-logos.plaid.com/9.png"),
    ],
)
def test_fetch_reconstructs_allowlisted_cdn_urls_without_credentials(key, url):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == url
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "client_id" not in request.headers
        return httpx.Response(200, headers={"content-type": "IMAGE/PNG; charset=binary"}, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch(key, client=client) == LogoImage(PNG, "image/png")
    assert len(requests) == 1


def test_owned_client_has_short_timeout_and_no_environment_or_redirect_configuration(monkeypatch):
    client_type = httpx.Client
    options = []

    def create_client(**kwargs):
        options.append(kwargs)
        return client_type(
            **kwargs,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)
            ),
        )

    monkeypatch.setattr(provider.httpx, "Client", create_client)
    assert plugin.merchant_logos.fetch(KEY) == LogoImage(PNG, "image/png")
    assert options == [{"timeout": 3.0, "follow_redirects": False, "trust_env": False}]


@pytest.mark.parametrize(
    ("status", "headers", "data"),
    [
        (404, {"content-type": "image/png"}, PNG),
        (302, {"location": "https://example.com/private"}, b""),
        (200, {}, PNG),
        (200, {"content-type": "text/html"}, PNG),
        (200, {"content-type": "image/jpeg"}, b"\xff\xd8\xfffixture"),
        (200, {"content-type": "image/png", "content-length": str(128 * 1024 + 1)}, PNG),
        (200, {"content-type": "image/png", "content-length": "-1"}, PNG),
        (200, {"content-type": "image/png", "content-length": "invalid"}, PNG),
        (200, {"content-type": "image/png"}, b"not a png"),
    ],
)
def test_bad_responses_keep_the_fallback_without_following_redirects(status, headers, data):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(status, headers=headers, content=data)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        assert fetch(KEY, client=client) is None
    assert requested == [URL]


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


@pytest.mark.parametrize("declared_length", [None, "0"])
def test_streaming_limit_rejects_oversize_bytes_even_without_a_truthful_length(declared_length):
    headers = {"content-type": "image/png"}
    if declared_length is not None:
        headers["content-length"] = declared_length
    response = httpx.Response(
        200, headers=headers, stream=Chunks([PNG, b"x" * (128 * 1024)])
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        assert fetch(KEY, client=client) is None


def test_stream_accepts_the_exact_size_limit_without_a_content_length():
    data = PNG + b"x" * (128 * 1024 - len(PNG))
    response = httpx.Response(
        200, headers={"content-type": "image/png"}, stream=Chunks([data])
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        assert fetch(KEY, client=client) == LogoImage(data, "image/png")


def test_network_failure_returns_none():
    def handler(request):
        raise httpx.ConnectError("unavailable", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch(KEY, client=client) is None


def test_midstream_failure_returns_none():
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield PNG
            raise httpx.ReadError("interrupted")

    response = httpx.Response(200, headers={"content-type": "image/png"}, stream=Interrupted())
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        assert fetch(KEY, client=client) is None


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, None),
        ({"logo_url": None}, None),
        ({"logo_url": "https://example.com/a.png"}, None),
        ({"logo_url": URL}, KEY),
        ({"counterparties": [{"logo_url": URL}]}, None),
    ],
)
def test_mapping_uses_only_supplied_top_level_artwork_without_fetching(
    extra, expected, bank_transaction, monkeypatch
):
    def unexpected_client(**kwargs):
        pytest.fail("Normalization must never download artwork")

    monkeypatch.setattr(provider.httpx, "Client", unexpected_client)
    record = normalize_transaction(bank_transaction(merchant_name="Fixture merchant", **extra))
    assert record["merchant"] == "Fixture merchant"
    assert record["merchantLogo"] == expected
