"""Lowercase browser addresses; readable aliases never replace ledger identities."""

import re
import unicodedata
from urllib.parse import urlencode

PAGES = {"budget", "transactions", "insights", "accounts"}

_CYRILLIC = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dzh",
    "ќ": "k", "ѓ": "g", "ѕ": "dz", "ә": "a", "ғ": "gh", "қ": "q",
    "ң": "ng", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "җ": "zh",
    "æ": "ae", "œ": "oe", "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th",
    "№": " ",
})


def name_slug(value):
    """Best-effort Latin/Cyrillic transliteration for a safe, readable path segment."""
    if not isinstance(value, str):
        return ""
    folded = unicodedata.normalize("NFC", value).casefold().translate(_CYRILLIC)
    plain = unicodedata.normalize("NFKD", folded)
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:180].rstrip("-")


def valid_route_key(value):
    return isinstance(value, str) and len(value) <= 200 and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is not None


def budget_key(identifier):
    return "home" if identifier == "default" else identifier


def page_url(key, page="budget", month="current-month", currency="USD", *, account_slug=None, **filters):
    account_path = f"/{account_slug}" if page == "transactions" and account_slug else ""
    path = f"/budgets/{key}{account_path}/{page}/{month}/{currency}".lower()
    query = urlencode({name: value for name, value in filters.items() if value is not None})
    # Encode uppercase identifier characters without changing their meaning.
    query = re.sub(
        r"%[0-9a-fA-F]{2}|[A-Z]",
        lambda match: match[0].lower() if match[0].startswith("%") else f"%{ord(match[0]):02x}",
        query,
    )
    return path + ("?" + query if query else "")
