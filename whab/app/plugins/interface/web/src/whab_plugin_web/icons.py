"""Small trusted SVG icons; no user content is interpolated into SVG markup."""

from markupsafe import Markup

PATHS = {
    "budget": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "transactions": '<path d="M18 6 6 18M6 7v11h11"/>',
    "insights": '<path d="m3 17 6-6 4 4 8-11M15 4h6v6"/>',
    "accounts": '<path d="m3 8 9-5 9 5M3 8h18M5 11v6M10 11v6M14 11v6M19 11v6M3 21h18M3 18h18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="m5 12 4 4 10-10"/>',
    "pencil": '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14zM4 14l5 5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "chevron-left": '<path d="m15 6-6 6 6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "arrow-right": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "arrow-up": '<path d="M7 17 17 7M7 7h10v10"/>',
    "shield": '<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m8 12 3 3 5-6"/>',
    "sparkles": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3M12 17h.01"/>',
    "credit": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>',
    "home": '<path d="m3 10 9-7 9 7v11H3zM9 21v-8h6v8"/>',
    "wallet": '<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 8h18M15 12h6v4h-6z"/>',
    "tag": '<path d="M3 3h8l10 10-8 8L3 11z"/><circle cx="7" cy="7" r="1"/>',
    "link": '<path d="m10 14 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(0 0) scale(.9)"/>',
    "refresh": '<path d="M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 14-2M19 16a8 8 0 0 1-14 2"/>',
    "search": '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "settings": '<path d="M10 3h4v2.3l1.3.5 1.6-1.6 2.9 2.9-1.6 1.6.5 1.3H21v4h-2.3l-.5 1.3 1.6 1.6-2.9 2.9-1.6-1.6-1.3.5V21h-4v-2.3l-1.3-.5-1.6 1.6-2.9-2.9 1.6-1.6-.5-1.3H3v-4h2.3l.5-1.3-1.6-1.6 2.9-2.9 1.6 1.6 1.3-.5z"/><circle cx="12" cy="12" r="3"/>',
}


def icon(name, size=18):
    path = PATHS.get(name, PATHS["tag"])
    size = int(size)
    return Markup(
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )


def category_icon(name):
    name = name.lower()
    if "rent" in name or "home" in name:
        return "home"
    if "grocer" in name or "dining" in name:
        return "wallet"
    if "travel" in name or "transport" in name:
        return "arrow-up"
    if "health" in name:
        return "plus"
    if "fun" in name:
        return "sparkles"
    return "tag"
