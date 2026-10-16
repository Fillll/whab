"""Resolve supplied Plaid artwork and fetch bounded public PNGs without bank credentials."""

import re

import httpx

from whab.plugins import LogoImage

_MAX_BYTES = 128 * 1024
_URL = re.compile(
    r"https://plaid-(merchant|counterparty)-logos\.plaid\.com/"
    r"([A-Za-z0-9][A-Za-z0-9_-]{0,159})\.png"
)
_KEY = re.compile(r"(merchant|counterparty)-([A-Za-z0-9][A-Za-z0-9_-]{0,159})")


def logo_key(url: object) -> str | None:
    """Accept only canonical artwork URLs supplied in Plaid's top-level logo_url."""
    match = _URL.fullmatch(url) if type(url) is str else None
    return f"{match[1]}-{match[2]}" if match is not None else None


def _fetch(client: httpx.Client, url: str) -> LogoImage | None:
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code != 200:
                return None
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "image/png":
                return None
            length = response.headers.get("content-length")
            if length is not None and not 0 <= int(length) <= _MAX_BYTES:
                return None
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=16 * 1024):
                if len(data) + len(chunk) > _MAX_BYTES:
                    return None
                data.extend(chunk)
            return LogoImage(data=bytes(data), media_type=media_type)
    except (httpx.HTTPError, ValueError):
        return None


def fetch(key: str, *, client: httpx.Client | None = None) -> LogoImage | None:
    """Reconstruct only allowlisted public CDN URLs; fetching never receives bank context."""
    match = _KEY.fullmatch(key) if type(key) is str else None
    if match is None:
        return None
    url = f"https://plaid-{match[1]}-logos.plaid.com/{match[2]}.png"
    if client is not None:
        return _fetch(client, url)
    with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as owned_client:
        return _fetch(owned_client, url)
