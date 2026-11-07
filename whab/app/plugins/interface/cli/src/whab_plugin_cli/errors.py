"""Fixed diagnostics for rejected plugin controls; never echo server records."""

import json


class PluginSelectionError(ValueError):
    """An authored plugin-management rejection safe for the terminal."""


def selection_error(raw):
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    if code == "management_tool":
        return "CLI is an installed management tool and cannot be enabled or disabled for a budget."
    if code == "required_plugin":
        return "This budget's primary storage is required and cannot be disabled. Change storage through its settings."
    return None
