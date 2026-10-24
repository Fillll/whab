"""Live branding creates one independent public-artwork cache per budget."""

from whab_plugin_bank_logos import plugin as provider

from whab.plugins import LogoImage, PluginRegistry


def test_scoped_factories_keep_domains_images_settings_and_cache_files_independent(tmp_path, monkeypatch):
    first = PluginRegistry((provider.plugin,)).bind_branding()
    second = PluginRegistry((provider.plugin,)).bind_branding()
    first.configure_logo_caches(tmp_path / "first")
    second.configure_logo_caches(tmp_path / "second")
    calls = []
    image = LogoImage(bytes.fromhex("89504e470d0a1a0a") + b"fictional", "image/png")

    def fetch(key):
        resolver = provider._domain_resolver.get()
        calls.append(resolver(key))
        return image

    monkeypatch.setattr(provider, "fetch_logo", fetch)
    first_key = first.bank_logo("", website="https://first-bank.example.org")[1]
    second_key = second.bank_logo("", website="https://second-bank.example.org")[1]
    assert first_key != second_key
    assert first.logo_image("bank-logos", second_key) is None
    assert first.logo_image("bank-logos", first_key) == image
    assert second.settings("bank-logos")["insights"][1].value == "0"
    assert second.logo_image("bank-logos", second_key) == image
    assert calls == ["first-bank.example.org", "second-bank.example.org"]
    assert provider._domain_resolver.get() is None
    original = (tmp_path / "second/cache/plugins/bank-logos" / (second_key + ".json")).read_bytes()
    assert first.settings_action("bank-logos", "flush-cache").startswith("Bank logo cache cleared")
    assert first.settings("bank-logos")["insights"][1].value == "0"
    assert second.settings("bank-logos")["insights"][1].value == "1"
    assert (tmp_path / "second/cache/plugins/bank-logos" / (second_key + ".json")).read_bytes() == original
    # A new budget-scoped instance reuses only that budget's public disk cache.
    reopened = PluginRegistry((provider.plugin,)).bind_branding()
    reopened.configure_logo_caches(tmp_path / "second")
    assert reopened.bank_logo("", website="https://second-bank.example.org")[1] == second_key
    assert reopened.logo_image("bank-logos", second_key) == image
    assert calls == ["first-bank.example.org", "second-bank.example.org"]
