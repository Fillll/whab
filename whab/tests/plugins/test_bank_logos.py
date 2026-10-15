"""Branding is optional, scoped to institution text and isolated from ledger hooks."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Event

import pytest

from whab.plugins import BankLogos, LogoImage, Plugin, PluginRegistry, load_plugins

PNG = b"\x89PNG\r\n\x1a\nfixture"


def registration(resolve=lambda _: "bank", fetch=lambda _: LogoImage(PNG, "image/png")):
    return Plugin("logos", "Logos", bank_logos=BankLogos(resolve, fetch))


def test_branding_is_independent_of_storage_ui_and_bank_services():
    plugin = registration()
    registry = PluginRegistry((plugin,))
    (description,) = registry.describe()
    assert description.kinds == ("branding",)
    assert description.has_bank_logos
    assert not description.has_bank and not description.has_ui
    assert registry.bank_plugins() == ()
    assert registry.bank_logo("Example institution") == ("logos", "bank")
    with pytest.raises(FrozenInstanceError):
        plugin.bank_logos.resolve = lambda _: "changed"
    with pytest.raises(ValueError):
        BankLogos(None, lambda _: None)
    with pytest.raises(ValueError):
        Plugin("logos", "Logos", bank_logos={})


def test_disabled_branding_entrypoint_is_never_imported():
    class Disabled:
        group = "whab.plugins"
        name = "bank-logos"

        def load(self):
            pytest.fail("Disabled branding must not be imported")

    registry = load_plugins((), (Disabled(),))
    assert registry.bank_logo("Chase") is None
    assert registry.logo_image("bank-logos", "chase") is None


@pytest.mark.parametrize("key", [None, "https://example.com", "../private", "", [], "X", "x" * 65])
def test_invalid_resolver_output_falls_back_without_fetching(key):
    registry = PluginRegistry((registration(resolve=lambda _: key),))
    assert registry.bank_logo("Unknown institution") is None


def test_resolver_failures_are_contained_and_next_plugin_can_supply_logo(caplog):
    def broken(_):
        raise RuntimeError("FICTIONAL_SECRET")

    registry = PluginRegistry((
        Plugin("broken", "Broken", bank_logos=BankLogos(broken, broken)), registration(),
    ))
    assert registry.bank_logo("Institution") == ("logos", "bank")
    assert registry.logo_image("broken", "bank") is None
    assert "FICTIONAL_SECRET" not in caplog.text
    assert registry.bank_logo({"institution": "Institution", "private": "record"}) is None
    assert registry.bank_logo("x" * 201) is None


@pytest.mark.parametrize(("data", "media_type"), [
    (b"<svg/>", "image/svg+xml"), (b"<html>error</html>", "image/png"),
    (PNG, "text/html"), (bytearray(PNG), "image/png"), (b"", "image/png"),
    (PNG + b"x" * (128 * 1024), "image/png"), (PNG, None),
])
def test_only_small_matching_raster_bytes_are_accepted(data, media_type):
    with pytest.raises(ValueError):
        LogoImage(data, media_type)


def test_logo_bytes_are_not_in_record_representation():
    image = LogoImage(PNG, "image/png")
    assert "fixture" not in repr(image)
    assert PluginRegistry((registration(),)).logo_image("logos", "bank") == image


def test_mutated_image_is_revalidated_at_host_boundary():
    image = LogoImage(PNG, "image/png")
    object.__setattr__(image, "data", b"<script>bad()</script>")
    registry = PluginRegistry((registration(fetch=lambda _: image),))
    assert registry.logo_image("logos", "bank") is None


def test_cache_ttls_retry_failures_and_expire_successes(monkeypatch):
    clock = [1000]
    calls = []
    monkeypatch.setattr("whab.plugins.runtime.monotonic", lambda: clock[0])

    def fetch(key):
        calls.append(key)
        return LogoImage(PNG, "image/png") if len(calls) > 1 else None

    registry = PluginRegistry((registration(fetch=fetch),))
    assert registry.logo_image("logos", "bank") is None
    clock[0] += 299
    assert registry.logo_image("logos", "bank") is None
    assert calls == ["bank"]
    clock[0] += 1
    assert registry.logo_image("logos", "bank") is not None
    clock[0] += 86399
    assert registry.logo_image("logos", "bank") is not None
    assert calls == ["bank", "bank"]
    clock[0] += 1
    assert registry.logo_image("logos", "bank") is not None
    assert len(calls) == 3


def test_cache_is_bounded_and_evicts_least_recently_used():
    calls = []
    registry = PluginRegistry((registration(fetch=lambda key: calls.append(key)),))
    for index in range(128):
        registry.logo_image("logos", f"bank-{index}")
    registry.logo_image("logos", "bank-0")
    registry.logo_image("logos", "bank-128")
    registry.logo_image("logos", "bank-0")
    registry.logo_image("logos", "bank-1")
    assert calls.count("bank-0") == 1 and calls.count("bank-1") == 2
    assert len(registry._logo_cache) == 128


def test_concurrent_account_images_fetch_the_same_bank_only_once():
    entered, release = Event(), Event()
    calls = []

    def fetch(key):
        calls.append(key)
        entered.set()
        assert release.wait(5)
        return LogoImage(PNG, "image/png")

    registry = PluginRegistry((registration(fetch=fetch),))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(registry.logo_image, "logos", "bank")
        assert entered.wait(5)
        second = pool.submit(registry.logo_image, "logos", "bank")
        release.set()
        assert first.result() == second.result() == LogoImage(PNG, "image/png")
    assert calls == ["bank"]


def test_a_slow_fetch_does_not_block_cached_images():
    entered, release = Event(), Event()

    def fetch(key):
        if key == "slow":
            entered.set()
            assert release.wait(5)
        return LogoImage(PNG, "image/png")

    registry = PluginRegistry((registration(fetch=fetch),))
    expected = registry.logo_image("logos", "cached")
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(registry.logo_image, "logos", "slow")
        assert entered.wait(5)
        try:
            assert pool.submit(registry.logo_image, "logos", "cached").result(timeout=1) == expected
        finally:
            release.set()
        assert pending.result() == expected
