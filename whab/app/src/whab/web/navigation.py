"""Canonical, lowercase browser addresses independent of display names."""

import re
from urllib.parse import urlencode

PAGES = {"budget", "transactions", "insights", "accounts"}


def budget_key(identifier):
    return "home" if identifier == "default" else identifier


def page_url(key, page="budget", month="current-month", currency="USD", **filters):
    path = f"/budgets/{key}/{page}/{month}/{currency}".lower()
    query = urlencode({name: value for name, value in filters.items() if value is not None})
    # Encode uppercase identifier characters without changing their meaning.
    query = re.sub(
        r"%[0-9a-fA-F]{2}|[A-Z]",
        lambda match: match[0].lower() if match[0].startswith("%") else f"%{ord(match[0]):02x}",
        query,
    )
    return path + ("?" + query if query else "")
