"""Merchant artwork stays attached to its bank provider and isolated from ledger data."""

from dataclasses import FrozenInstanceError

import pytest

from whab.plugins import BankLogos, LogoImage, MerchantLogos, Plugin, PluginRegistry, load_plugins

PNG = b"\x89PNG\r\n\x1a\nfixture"


def registration(fetch=lambda _: LogoImage(PNG, "image/png")):
    return Plugin("bank", "Bank", bank=lambda _: None, merchant_logos=MerchantLogos(fetch))


def test_merchant_artwork_is_an_optional_capability_of_the_existing_bank():
    plugin = registration()
    registry = PluginRegistry((plugin,))
    (description,) = registry.describe()
    assert description.has_merchant_logos and description.has_bank
    assert not description.has_bank_logos
    assert description.kinds == ("data_ingestion", "branding")
    assert registry.bank_plugins() == (plugin,)
    with pytest.raises(FrozenInstanceError):
        plugin.merchant_logos.fetch = lambda _: None
    with pytest.raises(ValueError, match="callable"):
        MerchantLogos(None)
    with pytest.raises(ValueError, match="MerchantLogos"):
        Plugin("bank", "Bank", bank=lambda _: None, merchant_logos={})
    standalone = Plugin("artwork", "Artwork", merchant_logos=MerchantLogos(lambda _: None))
    assert PluginRegistry((standalone,)).describe()[0].kinds == ("branding",)


def test_disabled_bank_artwork_never_loads_or_fetches():
    class Disabled:
        group = "whab.plugins"
        name = "bank"

        def load(self):
            pytest.fail("Disabled bank must not be imported")

    registry = load_plugins((), (Disabled(),))
    assert registry.merchant_logo_image("bank", "merchant-Fixture_123") is None
    assert PluginRegistry((Plugin("bank", "Bank", bank=lambda _: None),)).merchant_logo_image(
        "bank", "merchant-Fixture_123"
    ) is None


@pytest.mark.parametrize("key", [None, "", "../private", "https://example.com", "a.png", "a b", "a" * 201, []])
def test_invalid_keys_never_reach_a_merchant_hook(key):
    registry = PluginRegistry((registration(lambda _: pytest.fail("Invalid key reached hook")),))
    assert registry.merchant_logo_image("bank", key) is None
    assert registry.merchant_logo_image("invalid!plugin", "valid") is None


def test_merchant_and_bank_artwork_have_separate_cache_entries():
    calls = []
    bank_image = LogoImage(PNG + b"bank", "image/png")
    merchant_image = LogoImage(PNG + b"merchant", "image/png")
    plugin = Plugin(
        "bank", "Bank", bank=lambda _: None,
        bank_logos=BankLogos(lambda _: "same", lambda key: calls.append(("bank", key)) or bank_image),
        merchant_logos=MerchantLogos(lambda key: calls.append(("merchant", key)) or merchant_image),
    )
    registry = PluginRegistry((plugin,))
    for _ in range(2):
        assert registry.logo_image("bank", "same") == bank_image
        assert registry.merchant_logo_image("bank", "same") == merchant_image
    assert calls == [("bank", "same"), ("merchant", "same")]


def test_merchant_failures_are_sanitized_cached_and_retried(monkeypatch, caplog):
    clock, calls = [1000], []
    monkeypatch.setattr("whab.plugins.runtime.monotonic", lambda: clock[0])

    def fetch(key):
        calls.append(key)
        if len(calls) == 1:
            raise RuntimeError("FICTIONAL_PRIVATE_ERROR")
        return LogoImage(PNG, "image/png")

    registry = PluginRegistry((registration(fetch),))
    assert registry.merchant_logo_image("bank", "merchant-Fixture_123") is None
    clock[0] += 299
    assert registry.merchant_logo_image("bank", "merchant-Fixture_123") is None
    assert len(calls) == 1
    clock[0] += 1
    assert registry.merchant_logo_image("bank", "merchant-Fixture_123") == LogoImage(PNG, "image/png")
    assert len(calls) == 2
    assert "FICTIONAL_PRIVATE_ERROR" not in caplog.text


def test_merchant_hook_results_are_revalidated():
    invalid = LogoImage(PNG, "image/png")
    object.__setattr__(invalid, "data", b"<svg/>")
    registry = PluginRegistry((registration(lambda _: invalid),))
    assert registry.merchant_logo_image("bank", "merchant-Fixture_123") is None
