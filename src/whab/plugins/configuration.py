"""Nonsecret optional-plugin selection stored independently of credentials and ledgers."""

import json
import os
import tempfile
from pathlib import Path

from .runtime import PluginError
from .sdk import valid_plugin_name

SELECTION_FILENAME = "plugins.json"
MAX_SELECTION_BYTES = 16 * 1024
MAX_OPTIONAL_PLUGINS = 100


def validate_selection(value):
    """Accept bounded, unique optional IDs without echoing malformed input."""
    if (
        type(value) is not tuple
        or len(value) > MAX_OPTIONAL_PLUGINS
        or any(not valid_plugin_name(name) for name in value)
    ):
        raise PluginError(
            "Choose installed lowercase plugin IDs, with at most 100 optional plugins.",
            code="invalid_configuration",
        )
    if len(set(value)) != len(value):
        raise PluginError(
            "Choose each optional plugin once.", code="duplicate_configuration"
        )
    return value


def read_selection(home):
    """Missing settings use the environment; malformed settings never silently fall back."""
    path = Path(home) / SELECTION_FILENAME
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_SELECTION_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise PluginError(
            "Saved plugin settings could not be read. Check plugins.json permissions.",
            code="saved_configuration_read_failed",
        ) from None
    try:
        if len(raw) > MAX_SELECTION_BYTES:
            raise ValueError("Oversized selection")
        data = json.loads(raw)
        if (
            type(data) is not dict
            or set(data) != {"version", "plugins"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or type(data["plugins"]) is not list
        ):
            raise ValueError("Invalid selection format")
        return validate_selection(tuple(data["plugins"]))
    except (ValueError, UnicodeError, TypeError):
        raise PluginError(
            "Saved plugin settings are invalid. Save a new selection in Settings or fix plugins.json before restarting.",
            code="invalid_saved_configuration",
        ) from None


def write_selection(home, selection):
    """Replace only the dedicated selection file, atomically, after caller validation."""
    validate_selection(selection)
    home = Path(home)
    temporary = None
    try:
        home.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".plugins-", suffix=".json", dir=home)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"version": 1, "plugins": list(selection)}, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, home / SELECTION_FILENAME)
    except OSError:
        raise PluginError(
            "Plugin settings could not be saved. Check your WHAB_HOME permissions and retry.",
            code="saved_configuration_write_failed",
        ) from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
