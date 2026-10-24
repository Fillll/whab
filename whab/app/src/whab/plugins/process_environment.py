"""Avoid accidental caller configuration inheritance by owned child processes.

This policy does not sandbox trusted code or restrict filesystem/network access.
"""

import os

_STANDARD = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG", "LANGUAGE",
    "LC_ALL", "LC_CTYPE", "LC_NUMERIC", "LC_TIME", "LC_COLLATE", "LC_MONETARY",
    "LC_MESSAGES", "LC_PAPER", "LC_NAME", "LC_ADDRESS", "LC_TELEPHONE",
    "LC_MEASUREMENT", "LC_IDENTIFICATION",
})


def process_environment():
    """Copy only standard process variables, excluding the selected password alias."""
    password = os.environ.get("WHAB_BUDGET_PASSWORD_ENV")
    return {key: value for key, value in os.environ.items() if key in _STANDARD and key != password}
