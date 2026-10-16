"""Fetch public bank icons without sending account information or arbitrary domains."""

from __future__ import annotations

import base64
import json
import math
import threading
import time
from pathlib import Path

import httpx

from whab.plugins import BankLogos, Insight, LogoImage, Plugin, PluginAction, PluginSettings

_MAX_BYTES = 128 * 1024
_MAX_CACHE_BYTES = 2 * _MAX_BYTES
_CACHE_SECONDS = 365 * 24 * 60 * 60
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


def resolve(institution_name: str) -> str | None:
    """Resolve a known public institution name locally; no account data is accepted."""
    if type(institution_name) is not str:
        return None
    return _NAMES.get(_normalize(institution_name))


def _fetch(client: httpx.Client, domain: str) -> LogoImage | None:
    # Both the host and domain come from constants; callers cannot choose a URL.
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
    """Fetch a curated key; an injected client supports network-free provider tests."""
    if type(key) is not str or key not in _DOMAINS:
        return None
    domain = _DOMAINS[key]
    if client is not None:
        return _fetch(client, domain)
    with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as owned_client:
        return _fetch(owned_client, domain)


class _LogoCache:
    """Bounded public artwork cache; a flush also invalidates in-flight writes."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[float, LogoImage | None]] = {}
        self._pending: dict[str, threading.Event] = {}
        self._generation = 0
        self._directory: Path | None = None

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
            for key in _DOMAINS:
                entry = self._load(directory / f"{key}.json", now)
                if entry is not None:
                    self._entries[key] = entry

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
                or now - saved_at >= _CACHE_SECONDS
            ):
                return None
            image = LogoImage(
                data=base64.b64decode(record["data"], validate=True),
                media_type=record["media_type"],
            )
            return saved_at + _CACHE_SECONDS, image
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
        if type(key) is not str or key not in _DOMAINS:
            return None
        while True:
            with self._lock:
                entry = self._entries.get(key)
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
        try:
            image = fetch_logo(key)
        finally:
            with self._lock:
                if generation == self._generation:
                    now = time.time()
                    lifetime = _FAILURE_SECONDS if image is None else _CACHE_SECONDS
                    self._entries[key] = now + lifetime, image
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
                value="1 year (365 days)",
                detail="Logos refresh on the next request after they expire. "
                "Unavailable logos are retried after 5 minutes.",
            ),
            Insight(title="Cached logos", value=str(count)),
            Insight(
                title="Public artwork",
                value="Curated bank domains only",
                detail="Only allowlisted public bank domains are sent to Icon Horse. "
                "Account details and bank credentials are never sent.",
            ),
        )

    def flush(self) -> None:
        with self._lock:
            self._reset()
            if self._directory is not None:
                for key in _DOMAINS:
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
    if action_id != "flush-cache":
        raise ValueError("Unknown bank logos action.")
    _cache.flush()
    return "Bank logo cache cleared. Logos will download again when requested."


plugin = Plugin(
    id="bank-logos",
    display_name="Bank logos (Icon Horse)",
    bank_logos=BankLogos(resolve=resolve, fetch=fetch, configure_cache=configure_cache),
    settings=PluginSettings(
        read=read_settings,
        actions=(PluginAction(
            id="flush-cache",
            label="Flush cache",
            description="Remove all cached bank logos and retry unavailable logos immediately.",
        ),),
        act=apply_setting,
    ),
)
