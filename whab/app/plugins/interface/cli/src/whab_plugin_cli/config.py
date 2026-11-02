"""JSON connection defaults, with compatibility for existing flag files."""

import json
import shlex
from pathlib import Path

CONFIG_LIMIT = 64 * 1024
COMMANDS = frozenset({"query", "command", "feature-query", "feature-command", "describe"})
OPTIONS = {"--url": "url", "--budget": "budget", "--ca": "ca",
           "--workspace": "workspace", "--token-env": "token_env"}


class ConfigError(ValueError):
    """An authored config error that never includes file contents."""


def _unique_fields(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ConfigError("Duplicate keys in CLI JSON config.")
        values[key] = value
    return values


def _json_defaults(text):
    try:
        defaults = json.loads(text, object_pairs_hook=_unique_fields)
    except (json.JSONDecodeError, RecursionError):
        raise ConfigError("Invalid JSON in CLI config.") from None
    if type(defaults) is not dict:
        raise ConfigError("CLI JSON config must be an object.")
    if not defaults.keys() <= set(OPTIONS.values()):
        raise ConfigError("CLI JSON config accepts only url, budget, ca, workspace and token_env.")
    if any(type(value) is not str or not value for value in defaults.values()):
        raise ConfigError("CLI JSON config values must be nonempty strings.")
    return defaults


def _legacy_defaults(text):
    try:
        tokens = iter(shlex.split((text + "\n").replace("\\\r\n", "").replace("\\\n", ""), comments=True))
    except ValueError:
        raise ConfigError("Invalid quoting in CLI config.") from None
    defaults = {}
    for token in tokens:
        option, equals, value = token.partition("=")
        if option not in OPTIONS:
            raise ConfigError("CLI config accepts only --url, --budget, --ca, --workspace and --token-env.")
        if not equals:
            value = next(tokens, None)
        if not value or not equals and value.startswith("--"):
            raise ConfigError("Each CLI config option requires a value.")
        defaults[OPTIONS[option]] = value
    return defaults


def connection_defaults(argv):
    if not argv or argv[0].startswith("-") or argv[0] in COMMANDS:
        return argv, {}
    try:
        path = Path(argv[0]).expanduser().absolute()
        with path.open("rb") as source:
            raw = source.read(CONFIG_LIMIT + 1)
        if len(raw) > CONFIG_LIMIT:
            raise ConfigError("CLI config exceeds 64 KiB.")
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError, RuntimeError):
        raise ConfigError("Cannot read CLI config. Supply a readable UTF-8 connection-options file.") from None
    stripped = text.lstrip()
    defaults = _legacy_defaults(text) if not stripped or stripped.startswith(("-", "#")) else _json_defaults(text)
    if "workspace" in defaults and defaults["workspace"] not in {"personal", "demo"}:
        raise ConfigError("CLI config workspace must be personal or demo.")
    if "ca" in defaults:
        try:
            ca = Path(defaults["ca"]).expanduser()
        except RuntimeError:
            raise ConfigError("Cannot resolve the CLI config CA path.") from None
        defaults["ca"] = str(ca if ca.is_absolute() else path.parent / ca)
    return argv[1:], defaults
