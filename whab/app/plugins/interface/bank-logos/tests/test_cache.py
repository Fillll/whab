"""Public logo cache lifetime, persistence, flushes and concurrent requests."""

import base64
import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from whab.plugins import LogoImage

provider = importlib.import_module("whab_plugin_bank_logos.plugin")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)
IMAGE = LogoImage(data=PNG, media_type="image/png")


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(provider, "_cache", provider._LogoCache())


def record(saved_at=100, **overrides):
    return {
        "saved_at": saved_at,
        "media_type": "image/png",
        "data": base64.b64encode(PNG).decode("ascii"),
        **overrides,
    }


def test_descriptor_keeps_successful_logos_for_exactly_365_days(monkeypatch, tmp_path):
    now = [100.0]
    requests = []
    monkeypatch.setattr(provider.time, "time", lambda: now[0])
    monkeypatch.setattr(provider, "fetch_logo", lambda key: requests.append(key) or IMAGE)
    provider.plugin.bank_logos.configure_cache(tmp_path)
    assert provider.plugin.bank_logos.fetch("chase") == IMAGE
    assert provider.plugin.bank_logos.fetch("chase") == IMAGE
    now[0] += provider._CACHE_SECONDS - 1
    assert provider.fetch("chase") == IMAGE
    assert requests == ["chase"]
    now[0] += 1
    assert provider.fetch("chase") == IMAGE
    assert requests == ["chase", "chase"]
    saved = json.loads((tmp_path / "chase.json").read_text())
    assert saved == record(now[0])
    assert not (tmp_path / ".chase.json.tmp").exists()


def test_cache_restores_safe_successes_and_never_loads_arbitrary_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(provider.time, "time", lambda: 1000)
    (tmp_path / "chase.json").write_text(json.dumps(record(100)))
    (tmp_path / "unknown.json").write_text(json.dumps(record(100)))
    provider.configure_cache(tmp_path)
    monkeypatch.setattr(provider, "fetch_logo", lambda key: pytest.fail("cached logo downloaded"))
    assert provider.fetch("chase") == IMAGE
    assert provider.fetch("unknown") is None
    assert provider.fetch(None) is None
    settings = provider.plugin.settings.read()
    assert settings[0].value == "1 year (365 days)"
    assert settings[1].value == "1"
    assert "credentials" in settings[2].detail
    assert provider.plugin.settings.actions[0].id == "flush-cache"


def test_settings_count_only_unexpired_successes(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(provider.time, "time", lambda: now[0])
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE if key == "chase" else None)
    assert provider.fetch("chase") == IMAGE
    assert provider.fetch("citi") is None
    assert provider.read_settings()[1].value == "1"
    now[0] += provider._CACHE_SECONDS
    assert provider.read_settings()[1].value == "0"


@pytest.mark.parametrize(
    "content",
    [
        "not json", "[]", "{}", "[" * 100000 + "0" + "]" * 100000,
        json.dumps(record(saved_at=True)),
        json.dumps(record(saved_at="100")),
        json.dumps(record(saved_at=float("inf"))),
        json.dumps(record(saved_at=-1)),
        json.dumps(record(saved_at=1001)),
        json.dumps(record(saved_at=1000 - 365 * 24 * 60 * 60)),
        json.dumps(record(saved_at=10 ** 400)),
        json.dumps(record(data=1)),
        json.dumps(record(data="not base64")),
        json.dumps(record(data=base64.b64encode(b"not a PNG").decode("ascii"))),
        json.dumps(record(media_type="text/html")),
        "x" * (2 * 128 * 1024 + 1),
    ],
)
def test_invalid_expired_or_oversized_saved_files_are_ignored(content, monkeypatch, tmp_path):
    monkeypatch.setattr(provider.time, "time", lambda: 1000)
    (tmp_path / "chase.json").write_text(content)
    provider.configure_cache(tmp_path)
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    assert provider.read_settings()[1].value == "0"
    assert provider.fetch("chase") == IMAGE
    assert provider.read_settings()[1].value == "1"


def test_failures_retry_after_five_minutes_and_do_not_persist(monkeypatch, tmp_path):
    now = [100.0]
    requests = []
    monkeypatch.setattr(provider.time, "time", lambda: now[0])
    monkeypatch.setattr(provider, "fetch_logo", lambda key: requests.append(key))
    provider.configure_cache(tmp_path)
    assert provider.fetch("chase") is None
    now[0] += provider._FAILURE_SECONDS - 1
    assert provider.fetch("chase") is None
    assert requests == ["chase"]
    now[0] += 1
    assert provider.fetch("chase") is None
    assert requests == ["chase", "chase"]
    assert list(tmp_path.iterdir()) == []


def test_flush_removes_saved_and_temporary_logos_and_retries_failures(monkeypatch, tmp_path):
    requests = []
    monkeypatch.setattr(provider, "fetch_logo", lambda key: requests.append(key) or IMAGE)
    provider.configure_cache(tmp_path)
    provider.fetch("chase")
    provider.fetch("citi")
    (tmp_path / ".chase.json.tmp").write_text("interrupted write")
    (tmp_path / "unrelated").write_text("preserved")
    provider._cache._entries["ally"] = (provider.time.time() + 300, None)
    message = provider.plugin.settings.act("flush-cache")
    assert message == "Bank logo cache cleared. Logos will download again when requested."
    assert provider.read_settings()[1].value == "0"
    assert [path.name for path in tmp_path.iterdir()] == ["unrelated"]
    assert provider.fetch("chase") == IMAGE
    assert provider.fetch("ally") == IMAGE
    assert requests == ["chase", "citi", "chase", "ally"]
    with pytest.raises(ValueError, match="Unknown bank logos action"):
        provider.apply_setting("unknown")


def test_flush_works_without_a_persistent_directory(monkeypatch):
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    assert provider.fetch("chase") == IMAGE
    provider.apply_setting("flush-cache")
    assert provider.read_settings()[1].value == "0"


def test_unavailable_cache_directory_keeps_memory_cache_working(monkeypatch, tmp_path):
    (tmp_path / "file").write_text("not a directory")
    provider.configure_cache(tmp_path / "file" / "logos")
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    assert provider.fetch("chase") == IMAGE
    assert provider.fetch("chase") == IMAGE
    with pytest.raises(OSError):
        provider.apply_setting("flush-cache")


def test_disk_save_failure_does_not_interrupt_public_artwork(monkeypatch, tmp_path):
    provider.configure_cache(tmp_path)
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    # A directory at the temporary-file path prevents unlink/write, without changing live files.
    (tmp_path / ".chase.json.tmp").mkdir()
    assert provider.fetch("chase") == IMAGE
    assert provider.fetch("chase") == IMAGE
    assert not (tmp_path / "chase.json").exists()


def test_failed_disk_flush_does_not_report_success(monkeypatch, tmp_path):
    provider.configure_cache(tmp_path)
    (tmp_path / "chase.json").mkdir()
    with pytest.raises(OSError):
        provider.apply_setting("flush-cache")


def test_cache_write_does_not_follow_a_temporary_symlink(monkeypatch, tmp_path):
    provider.configure_cache(tmp_path)
    victim = tmp_path / "unrelated"
    victim.write_text("preserved")
    (tmp_path / ".chase.json.tmp").symlink_to(victim)
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    assert provider.fetch("chase") == IMAGE
    assert victim.read_text() == "preserved"
    assert not (tmp_path / ".chase.json.tmp").exists()


def test_concurrent_same_key_requests_share_one_download(monkeypatch, tmp_path):
    started = threading.Event()
    waiting = threading.Event()
    release = threading.Event()
    original_event = threading.Event
    requests = []

    class ObservedEvent:
        def __init__(self):
            self.event = original_event()

        def wait(self):
            waiting.set()
            return self.event.wait(5)

        def set(self):
            self.event.set()

    def fetch_logo(key):
        requests.append(key)
        started.set()
        assert release.wait(5)
        return IMAGE

    monkeypatch.setattr(provider, "fetch_logo", fetch_logo)
    provider.configure_cache(tmp_path)
    # Instrument only pending events, not the executor's global threading machinery.
    class Events:
        RLock = threading.RLock
        Event = ObservedEvent

    monkeypatch.setattr(provider, "threading", Events)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(provider.fetch, "chase")
        assert started.wait(5)
        second = executor.submit(provider.fetch, "chase")
        assert waiting.wait(5)
        release.set()
        assert first.result(5) == IMAGE
        assert second.result(5) == IMAGE
    assert requests == ["chase"]


def test_flush_during_download_never_restores_old_cache(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    old_image = LogoImage(data=b"GIF89aold", media_type="image/gif")
    requests = []

    def fetch_logo(key):
        requests.append(key)
        if len(requests) == 1:
            started.set()
            assert release.wait(5)
            return old_image
        return IMAGE

    monkeypatch.setattr(provider, "fetch_logo", fetch_logo)
    provider.configure_cache(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        old = executor.submit(provider.fetch, "chase")
        assert started.wait(5)
        provider.apply_setting("flush-cache")
        assert provider.fetch("chase") == IMAGE
        release.set()
        assert old.result(5) == old_image
    assert provider.fetch("chase") == IMAGE
    assert json.loads((tmp_path / "chase.json").read_text())["media_type"] == "image/png"
    assert requests == ["chase", "chase"]


def test_configuration_during_download_does_not_write_previous_generation(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    def fetch_logo(key):
        started.set()
        assert release.wait(5)
        return IMAGE

    monkeypatch.setattr(provider, "fetch_logo", fetch_logo)
    with ThreadPoolExecutor(max_workers=1) as executor:
        old = executor.submit(provider.fetch, "chase")
        assert started.wait(5)
        provider.configure_cache(tmp_path)
        release.set()
        assert old.result(5) == IMAGE
    assert list(tmp_path.iterdir()) == []
    assert provider.read_settings()[1].value == "0"


def test_unexpected_fetch_exception_releases_waiters_and_caches_short_failure(monkeypatch):
    def broken_fetch(key):
        raise RuntimeError("unexpected provider failure")

    monkeypatch.setattr(provider, "fetch_logo", broken_fetch)
    with pytest.raises(RuntimeError, match="unexpected provider failure"):
        provider.fetch("chase")
    assert provider.fetch("chase") is None
    assert not provider._cache._pending


def test_saved_logo_expires_at_exactly_one_year(monkeypatch, tmp_path):
    now = 100 + provider._CACHE_SECONDS
    monkeypatch.setattr(provider.time, "time", lambda: now)
    (tmp_path / "chase.json").write_text(json.dumps(record(100)))
    provider.configure_cache(tmp_path)
    assert provider.read_settings()[1].value == "0"
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    assert provider.fetch("chase") == IMAGE
    assert json.loads((tmp_path / "chase.json").read_text())["saved_at"] == now


def test_explicit_hostnames_restore_saved_images_only_after_registration(monkeypatch, tmp_path):
    key = provider.resolve_domain("bank.example.com")
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    provider.configure_cache(tmp_path)
    assert provider.fetch(key) == IMAGE
    assert json.loads((tmp_path / f"{key}.json").read_text())["data"] == record()["data"]

    # A restart cannot treat a file name alone as permission to fetch a public domain.
    monkeypatch.setattr(provider, "_cache", provider._LogoCache())
    provider.configure_cache(tmp_path)
    monkeypatch.setattr(provider, "fetch_logo", lambda key: pytest.fail("cached logo downloaded"))
    assert provider.fetch(key) is None
    assert provider.resolve_domain("bank.example.com") == key
    assert provider.fetch(key) == IMAGE
    assert provider.read_settings()[1].value == "1"

    # Cache reconfiguration also restores already registered explicit hostnames.
    provider.configure_cache(tmp_path)
    assert provider.fetch(key) == IMAGE


def test_flush_clears_explicit_files_from_earlier_processes_but_keeps_mappings(monkeypatch, tmp_path):
    key = provider.resolve_domain("bank.example.com")
    requests = []
    monkeypatch.setattr(provider, "fetch_logo", lambda key: requests.append(key) or IMAGE)
    provider.configure_cache(tmp_path)
    assert provider.fetch(key) == IMAGE
    old_key = "domain-" + "a" * 32
    (tmp_path / f"{old_key}.json").write_text(json.dumps(record()))
    (tmp_path / f".{old_key}.json.tmp").write_text("interrupted write")
    unrelated = tmp_path / "domain-user-owned.json"
    unrelated.write_text("preserved")
    provider.apply_setting("flush-cache")
    assert list(tmp_path.iterdir()) == [unrelated]
    assert provider._cache.domain(key) == "bank.example.com"
    assert provider.fetch(key) == IMAGE
    assert requests == [key, key]


def test_hostname_registration_is_bounded_and_refreshes_recently_used_keys(monkeypatch):
    monkeypatch.setattr(provider, "_MAX_DOMAIN_KEYS", 2)
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    first = provider.resolve_domain("first.example.com")
    second = provider.resolve_domain("second.example.com")
    assert provider.fetch(second) == IMAGE
    assert provider.resolve_domain("first.example.com") == first
    third = provider.resolve_domain("third.example.com")
    assert provider._cache.domain(first) == "first.example.com"
    assert provider._cache.domain(second) is None
    assert provider._cache.domain(third) == "third.example.com"
    assert provider.fetch(second) is None
    assert second not in provider._cache._entries
    assert len(provider._cache._domains) == 2


def test_memory_entries_and_persisted_explicit_images_are_bounded(monkeypatch, tmp_path):
    import os

    monkeypatch.setattr(provider, "_MAX_DOMAIN_KEYS", 2)
    monkeypatch.setattr(provider, "fetch_logo", lambda key: IMAGE)
    provider.configure_cache(tmp_path)
    old_paths = []
    for index in range(3):
        path = tmp_path / f"domain-{index:032x}.json"
        path.write_text(json.dumps(record()))
        os.utime(path, (index, index))
        old_paths.append(path)
    unrelated = tmp_path / "domain-user-owned.json"
    unrelated.write_text("preserved")
    key = provider.resolve_domain("bank.example.com")
    assert provider.fetch(key) == IMAGE
    assert not old_paths[0].exists()
    assert not old_paths[1].exists()
    assert old_paths[2].exists()
    assert unrelated.read_text() == "preserved"
    assert provider.fetch("chase") == IMAGE
    assert provider.fetch("citi") == IMAGE
    assert len(provider._cache._entries) == 2
    assert key not in provider._cache._entries


def test_eviction_during_download_does_not_reinsert_unregistered_key(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "_MAX_DOMAIN_KEYS", 1)
    started = threading.Event()
    release = threading.Event()
    first = provider.resolve_domain("first.example.com")

    def delayed_fetch(key):
        started.set()
        assert release.wait(5)
        return IMAGE

    monkeypatch.setattr(provider, "fetch_logo", delayed_fetch)
    provider.configure_cache(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        downloaded = executor.submit(provider.fetch, first)
        assert started.wait(5)
        provider.resolve_domain("second.example.com")
        release.set()
        assert downloaded.result(5) == IMAGE
    assert first not in provider._cache._entries
    assert not (tmp_path / f"{first}.json").exists()
    assert provider.fetch(first) is None
