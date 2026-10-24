"""Fetch public bank icons without sending account information or private URLs."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import threading
import time
from contextvars import ContextVar
from pathlib import Path

import httpx

from whab.plugins import (
    BankLogos,
    Insight,
    LogoImage,
    Plugin,
    PluginAction,
    PluginContract,
    PluginSettings,
    StoreError,
    bank_website,
)

_MAX_BYTES = 128 * 1024
_MAX_CACHE_BYTES = 2 * _MAX_BYTES
_MAX_DOMAIN_KEYS = 256
_DYNAMIC_KEY = re.compile(r"domain-[0-9a-f]{32}")
_FAILURE_SECONDS = 5 * 60
_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_DOMAINS = {
    "chase": "chase.com",
    "capital-one": "capitalone.com",
    "bank-of-america": "bankofamerica.com",
    "wells-fargo": "wellsfargo.com",
    "citi": "citi.com",
    "us-bank": "usbank.com",
    "american-express": "americanexpress.com",
    "discover": "discover.com",
    "fidelity": "fidelity.com",
    "robinhood": "robinhood.com",
    "schwab": "schwab.com",
    "ally": "ally.com",
    "sofi": "sofi.com",
    "pnc": "pnc.com",
    "td": "td.com",
    "bmo": "bmo.com",
    "navy-federal": "navyfederal.org",
    "usaa": "usaa.com",
    "citizens": "citizensbank.com",
    "barclays": "barclays.com",
    "santander": "santanderbank.com",
}


def _normalize(name: str) -> str:
    return " ".join(name.casefold().split())


# Deliberately exact aliases: never derive a domain or guess from an account name.
_NAMES = {
    _normalize(name): key
    for key, names in {
        "chase": (
            "Chase", "Chase Bank", "JPMorgan Chase", "JPMorgan Chase Bank",
            "JPMorgan Chase Bank, N.A.",
        ),
        "capital-one": ("Capital One", "Capital One Bank", "Capital One, N.A."),
        "bank-of-america": ("Bank of America", "Bank of America, N.A."),
        "wells-fargo": ("Wells Fargo", "Wells Fargo Bank", "Wells Fargo Bank, N.A."),
        "citi": ("Citi", "Citibank", "Citibank, N.A."),
        "us-bank": ("US Bank", "U.S. Bank", "U.S. Bank National Association"),
        "american-express": ("American Express", "American Express National Bank", "Amex"),
        "discover": ("Discover", "Discover Bank"),
        "fidelity": ("Fidelity", "Fidelity Investments"),
        "robinhood": ("Robinhood",),
        "schwab": ("Schwab", "Charles Schwab", "Charles Schwab Bank"),
        "ally": ("Ally", "Ally Bank"),
        "sofi": ("SoFi", "SoFi Bank"),
        "pnc": ("PNC", "PNC Bank", "PNC Bank, N.A."),
        "td": ("TD", "TD Bank", "TD Bank, N.A."),
        "bmo": ("BMO", "BMO Bank", "BMO Harris", "BMO Harris Bank", "BMO Bank N.A."),
        "navy-federal": ("Navy Federal", "Navy Federal Credit Union"),
        "usaa": ("USAA", "USAA Federal Savings Bank"),
        "citizens": ("Citizens", "Citizens Bank", "Citizens Bank, N.A."),
        "barclays": ("Barclays", "Barclays Bank", "Barclays US", "Barclays Bank Delaware"),
        "santander": ("Santander", "Santander Bank", "Santander Bank, N.A."),
    }.items()
    for name in names
}

_domain_resolver = ContextVar("bank_logo_domain_resolver", default=None)


def resolve(institution_name: str) -> str | None:
    """Resolve a known public institution name locally; no account data is accepted."""
    if type(institution_name) is not str:
        return None
    return _NAMES.get(_normalize(institution_name))


def resolve_domain(public_hostname: str) -> str | None:
    """Register an explicitly supplied public hostname without any network access."""
    return _resolve_domain(_cache, public_hostname)


def _resolve_domain(cache, public_hostname):
    if (type(public_hostname) is not str or not public_hostname or len(public_hostname) > 253
            or any(character in public_hostname for character in "/\\:@?#")):
        return None
    try:
        normalized = bank_website("https://" + public_hostname)
    except StoreError:
        return None
    hostname = normalized.removeprefix("https://")
    return cache.register_domain(hostname)


def _fetch(client: httpx.Client, domain: str) -> LogoImage | None:
    # The destination is fixed. The path contains only a validated public hostname.
    url = f"https://icon.horse/icon/{domain}"
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code != 200:
                return None
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type not in _MEDIA_TYPES:
                return None
            length = response.headers.get("content-length")
            if length is not None and int(length) > _MAX_BYTES:
                return None
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=16 * 1024):
                if len(data) + len(chunk) > _MAX_BYTES:
                    return None
                data.extend(chunk)
            return LogoImage(data=bytes(data), media_type=media_type)
    except (httpx.HTTPError, ValueError):
        # A missing icon or unavailable artwork service must never break the ledger UI.
        return None


def fetch_logo(key: str, *, client: httpx.Client | None = None) -> LogoImage | None:
    """Fetch a registered key; an injected client supports network-free provider tests."""
    resolver = _domain_resolver.get() or _cache.domain
    domain = resolver(key)
    if domain is None:
        return None
    if client is not None:
        return _fetch(client, domain)
    with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as owned_client:
        return _fetch(owned_client, domain)


class _LogoCache:
    """Retain saved public artwork indefinitely, with bounded memory and fetches."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[float, LogoImage | None]] = {}
        self._pending: dict[str, threading.Event] = {}
        self._generation = 0
        self._directory: Path | None = None
        self._domains: dict[str, str] = {}

    def domain(self, key: str) -> str | None:
        if type(key) is not str:
            return None
        with self._lock:
            return _DOMAINS.get(key) or self._domains.get(key)

    def register_domain(self, hostname: str) -> str:
        key = "domain-" + hashlib.sha256(hostname.encode("ascii")).hexdigest()[:32]
        with self._lock:
            self._domains.pop(key, None)
            if len(self._domains) >= _MAX_DOMAIN_KEYS:
                oldest = next(iter(self._domains))
                del self._domains[oldest]
                self._entries.pop(oldest, None)
            self._domains[key] = hostname
        return key

    def _remember(self, key: str, entry: tuple[float, LogoImage | None]) -> None:
        self._entries.pop(key, None)
        if len(self._entries) >= _MAX_DOMAIN_KEYS:
            del self._entries[next(iter(self._entries))]
        self._entries[key] = entry

    def _reset(self) -> None:
        self._generation += 1
        self._entries.clear()
        for pending in self._pending.values():
            pending.set()
        self._pending.clear()

    def configure(self, directory: Path) -> None:
        with self._lock:
            self._reset()
            self._directory = directory
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                return
            now = time.time()
            for key in (*_DOMAINS, *self._domains):
                entry = self._load(directory / f"{key}.json", now)
                if entry is not None:
                    self._remember(key, entry)

    @staticmethod
    def _load(path: Path, now: float) -> tuple[float, LogoImage] | None:
        try:
            with path.open("rb") as saved:
                content = saved.read(_MAX_CACHE_BYTES + 1)
            if len(content) > _MAX_CACHE_BYTES:
                return None
            record = json.loads(content)
            saved_at = record["saved_at"]
            if (
                type(saved_at) not in (int, float)
                or not math.isfinite(saved_at)
                or not 0 <= saved_at <= now
            ):
                return None
            image = LogoImage(
                data=base64.b64decode(record["data"], validate=True),
                media_type=record["media_type"],
            )
            # The legacy downloaded-at record remains readable, but age no
            # longer expires successful artwork or triggers another download.
            return math.inf, image
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return None

    def _save(self, key: str, image: LogoImage, saved_at: float) -> None:
        if self._directory is not None:
            path = self._directory / f"{key}.json"
            temporary = self._directory / f".{key}.json.tmp"
            content = json.dumps({
                "saved_at": saved_at,
                "media_type": image.media_type,
                "data": base64.b64encode(image.data).decode("ascii"),
            })
            try:
                temporary.unlink(missing_ok=True)
                with temporary.open("x", encoding="utf-8") as saved:
                    saved.write(content)
                temporary.replace(path)
            except OSError:
                # Public artwork remains optional when its cache directory is unavailable.
                pass

    def get(self, key: str) -> LogoImage | None:
        if self.domain(key) is None:
            return None
        while True:
            with self._lock:
                entry = self._entries.get(key)
                if entry is None and self._directory is not None:
                    entry = self._load(self._directory / f"{key}.json", time.time())
                    if entry is not None:
                        self._remember(key, entry)
                if entry is not None and time.time() < entry[0]:
                    return entry[1]
                pending = self._pending.get(key)
                if pending is None:
                    pending = threading.Event()
                    self._pending[key] = pending
                    generation = self._generation
                    break
            pending.wait()
        image = None
        domain_context = _domain_resolver.set(self.domain)
        try:
            image = fetch_logo(key)
        finally:
            _domain_resolver.reset(domain_context)
            with self._lock:
                if generation == self._generation and self.domain(key) is not None:
                    now = time.time()
                    expires = now + _FAILURE_SECONDS if image is None else math.inf
                    self._remember(key, (expires, image))
                    if image is not None:
                        self._save(key, image, now)
                if self._pending.get(key) is pending:
                    del self._pending[key]
                pending.set()
        return image

    def read(self) -> tuple[Insight, ...]:
        with self._lock:
            now = time.time()
            count = sum(
                image is not None and expires > now
                for expires, image in self._entries.values()
            )
        return (
            Insight(
                title="Cache duration",
                value="Indefinite",
                detail="Downloaded logos stay saved across restarts and do not expire. "
                "Unavailable logos are retried after 5 minutes.",
            ),
            Insight(title="Cached logos", value=str(count)),
            Insight(
                title="Public artwork",
                value="Public bank hostnames only",
                detail="Only curated bank domains or the public bank website you set are sent to Icon Horse. "
                "Account details and bank credentials are never sent.",
            ),
        )

    def flush(self) -> None:
        with self._lock:
            self._reset()
            if self._directory is not None:
                keys = set(_DOMAINS)
                for path in self._directory.iterdir():
                    name = path.name.removeprefix(".").removesuffix(".tmp").removesuffix(".json")
                    if _DYNAMIC_KEY.fullmatch(name):
                        keys.add(name)
                for key in keys:
                    (self._directory / f"{key}.json").unlink(missing_ok=True)
                    (self._directory / f".{key}.json.tmp").unlink(missing_ok=True)


_cache = _LogoCache()


def fetch(key: str) -> LogoImage | None:
    return _cache.get(key)


def configure_cache(directory: Path) -> None:
    _cache.configure(directory)


def read_settings() -> tuple[Insight, ...]:
    return _cache.read()


def apply_setting(action_id: str) -> str:
    return _apply_setting(_cache, action_id)


def _apply_setting(cache, action_id):
    if action_id != "flush-cache":
        raise ValueError("Unknown bank logos action.")
    cache.flush()
    return "Bank logo cache cleared. Logos will download again when requested."


def branding_instance():
    """One budget owns these cache and settings closures; no global reconfiguration."""
    cache = _LogoCache()
    return Plugin(
        id="bank-logos", display_name="Bank logos (Icon Horse)",
        bank_logos=BankLogos(resolve, cache.get, cache.configure, lambda hostname: _resolve_domain(cache, hostname),
                             "Icon Horse", "https://icon.horse"),
        settings=PluginSettings(cache.read, plugin.settings.actions, lambda action: _apply_setting(cache, action)),
        contract=PluginContract(lifecycle="live"),
    )


plugin = Plugin(
    id="bank-logos",
    display_name="Bank logos (Icon Horse)",
    bank_logos=BankLogos(
        resolve=resolve, fetch=fetch, configure_cache=configure_cache, resolve_domain=resolve_domain,
        provider_name="Icon Horse", provider_url="https://icon.horse",
    ),
    settings=PluginSettings(
        read=read_settings,
        actions=(PluginAction(
            id="flush-cache",
            label="Flush cache",
            description="Delete downloaded logos and retry unavailable logos. "
            "Saved bank websites stay unchanged; logos download again when requested.",
        ),),
        act=apply_setting,
    ),
    branding_factory=branding_instance,
    contract=PluginContract(lifecycle="live"),
)
