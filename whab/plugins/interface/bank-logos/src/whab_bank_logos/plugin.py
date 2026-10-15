"""Fetch public bank icons without sending account information or arbitrary domains."""

from __future__ import annotations

import httpx

from whab.plugins import BankLogos, LogoImage, Plugin

_MAX_BYTES = 128 * 1024
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


def fetch(key: str) -> LogoImage | None:
    return fetch_logo(key)


plugin = Plugin(
    id="bank-logos",
    display_name="Bank logos (Icon Horse)",
    bank_logos=BankLogos(resolve=resolve, fetch=fetch),
)
