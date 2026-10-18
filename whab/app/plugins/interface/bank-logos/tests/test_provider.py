"""Public artwork fetching is exact, bounded, and independent from bank credentials."""

import base64
import importlib

import httpx
import pytest
from whab_plugin_bank_logos.plugin import fetch_logo, plugin, resolve

provider = importlib.import_module("whab_plugin_bank_logos.plugin")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        (" Chase ", "chase"),
        ("CAPITAL   ONE", "capital-one"),
        ("Bank of America", "bank-of-america"),
        ("Wells Fargo", "wells-fargo"),
        ("Citibank", "citi"),
        ("U.S. Bank", "us-bank"),
        ("American Express", "american-express"),
        ("Discover Bank", "discover"),
        ("Fidelity Investments", "fidelity"),
        ("Robinhood", "robinhood"),
        ("Charles Schwab", "schwab"),
        ("Ally Bank", "ally"),
        ("SoFi", "sofi"),
        ("PNC Bank", "pnc"),
        ("TD Bank", "td"),
        ("BMO Harris Bank", "bmo"),
        ("Navy Federal Credit Union", "navy-federal"),
    ],
)
def test_curated_institution_names_resolve_locally(name, key):
    assert resolve(name) == key


@pytest.mark.parametrize(
    "name",
    [
        "", "Unknown Bank", "Manual", "Demo Bank", "Sandbox", "First Platypus Bank",
        "Chase Sandbox", "Demo Chase", "Chase checking 1234", "Chase.com",
        "https://chase.com", "http://127.0.0.1", "Bank of America @ malicious.example",
        None, {},
    ],
)
def test_unknown_names_are_not_guessed_or_sent_to_a_service(name):
    assert resolve(name) is None


def test_import_resolution_and_unknown_fetch_never_create_a_client(monkeypatch):
    def unexpected_client(**kwargs):
        raise AssertionError("No network client should be created")

    monkeypatch.setattr(provider.httpx, "Client", unexpected_client)
    importlib.reload(provider)
    assert provider.resolve("Chase") == "chase"
    assert provider.fetch("https://private.example") is None
    assert provider.fetch("unknown") is None
    assert provider.fetch_logo("unknown") is None
    assert provider.fetch_logo(None) is None


def test_descriptor_exposes_only_optional_public_artwork_hooks():
    assert plugin.id == "bank-logos"
    assert plugin.bank is None
    assert plugin.storage is None
    assert plugin.ui is None
    assert plugin.bank_logos.resolve("Chase") == "chase"


def test_fetch_uses_only_fixed_public_domain_and_returns_validated_raster():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "image/png; charset=binary"}, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_logo("capital-one", client=client)
    assert result.data == PNG
    assert result.media_type == "image/png"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://icon.horse/icon/capitalone.com"
    assert not requests[0].content


def test_owned_client_disables_redirects_and_environment_proxies(monkeypatch):
    options = []
    original_client = httpx.Client

    def client_factory(**kwargs):
        options.append(kwargs)
        return original_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)
            ),
        )

    monkeypatch.setattr(provider.httpx, "Client", client_factory)
    assert provider.fetch("chase").data == PNG
    assert options == [{"timeout": 3.0, "follow_redirects": False, "trust_env": False}]


@pytest.mark.parametrize(
    ("status", "headers", "data"),
    [
        (404, {"content-type": "image/png"}, PNG),
        (500, {"content-type": "image/png"}, PNG),
        (302, {"location": "http://127.0.0.1/private"}, b""),
        (200, {"content-type": "image/svg+xml"}, b"<svg />"),
        (200, {"content-type": "text/html"}, b"<html>not artwork</html>"),
        (200, {"content-type": "image/png"}, b"not a PNG"),
        (200, {"content-type": "image/jpeg"}, PNG),
        (200, {"content-type": "image/png", "content-length": "131073"}, PNG),
        (200, {"content-type": "image/png", "content-length": "invalid"}, PNG),
        (200, {"content-type": "image/png"}, PNG + b"x" * (128 * 1024)),
    ],
)
def test_missing_redirected_oversized_or_unsafe_responses_return_none(status, headers, data):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, content=data)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        assert fetch_logo("chase", client=client) is None
    assert len(requests) == 1


def test_stream_without_a_length_is_bounded_and_closed():
    class LargeStream(httpx.SyncByteStream):
        closed = False
        chunks_read = 0

        def __iter__(self):
            for _ in range(100):
                self.chunks_read += 1
                yield b"x" * (16 * 1024)

        def close(self):
            self.closed = True

    stream = LargeStream()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "image/png"}, stream=stream)
    )
    with httpx.Client(transport=transport) as client:
        assert fetch_logo("chase", client=client) is None
    assert stream.closed
    assert stream.chunks_read == 9


def test_network_failure_falls_back_without_exposing_provider_details():
    def handler(request):
        raise httpx.ConnectError("Untrusted service details", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_logo("chase", client=client) is None


@pytest.mark.parametrize(
    "hostname",
    [
        None, {}, "", "https://chase.com", "example.com/login", "example.com\\login",
        "example.com:443", "user@example.com", "example.com?account=private",
        "example.com#private", "localhost", "bank.local", "bank.internal", "127.0.0.1",
        "[::1]", "bank .com", "a" * 254,
    ],
)
def test_explicit_domain_hook_rejects_urls_private_hosts_and_invalid_values(hostname):
    assert provider.resolve_domain(hostname) is None


def test_explicit_public_hostname_resolution_is_local_normalized_and_opaque(monkeypatch):
    import hashlib

    def unexpected_client(**kwargs):
        raise AssertionError("Resolving a public hostname must not access the network")

    monkeypatch.setattr(provider.httpx, "Client", unexpected_client)
    key = provider.resolve_domain("BANK.EXAMPLE.COM")
    assert key == "domain-" + hashlib.sha256(b"bank.example.com").hexdigest()[:32]
    assert provider.resolve_domain("bank.example.com") == key
    assert "bank.example.com" not in key
    international = provider.resolve_domain("b\u00fccher.example.com")
    assert provider._cache.domain(international) == "xn--bcher-kva.example.com"
    assert provider.plugin.bank_logos.resolve_domain is provider.resolve_domain
    assert provider.plugin.bank_logos.provider_name == "Icon Horse"
    assert provider.plugin.bank_logos.provider_url == "https://icon.horse"


def test_explicit_hostname_fetch_uses_fixed_service_only_after_registration(monkeypatch):
    import hashlib

    hostname = "secure.bank.example.com"
    key = "domain-" + hashlib.sha256(hostname.encode()).hexdigest()[:32]
    monkeypatch.setattr(provider, "_cache", provider._LogoCache())
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert provider.fetch_logo(key, client=client) is None
        assert provider.resolve_domain(hostname) == key
        assert provider.fetch_logo(key, client=client).data == PNG
    assert [str(request.url) for request in requests] == [
        "https://icon.horse/icon/secure.bank.example.com",
    ]
    assert not requests[0].content
    assert "authorization" not in requests[0].headers
