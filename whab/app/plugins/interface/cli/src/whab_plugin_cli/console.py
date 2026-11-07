"""Name-addressed local console; the host owns storage and financial rules."""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

from whab.plugins.documentation import installed_catalog
from whab.plugins.local import local_connection

from . import client
from .config import ConfigError, connection_defaults
from .help import describe, overview
from .identifiers import host_id, plugin_identifiers

PREFIX = "/api/local/v1"
ROOT_COMMANDS = {"budget", "plugin", "api", "BUDGET"}


class CommandError(ValueError):
    """A controlled diagnostic safe to show without transport/private values."""


def parser(options):
    root = argparse.ArgumentParser(prog="whab", parents=[options],
        description="Budget from the command line. Production is the default; choose --environment sandbox for tests.",
        epilog="Examples:\n  whab budget create empty-budget --path ~/budgets\n  whab empty-budget plugin enable ynab-zip-importer\n  whab empty-budget insights summary\n  whab empty-budget trnx non-reviewed\n\nEvery command supports --help. Names containing spaces can be quoted.\nUse whab budget list to discover names and permanent IDs.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    groups = root.add_subparsers(dest="scope", required=True, metavar="{budget,plugin,BUDGET,api}")
    budget = groups.add_parser("budget", help="Create and list saved budgets")
    actions = budget.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="List budgets available in this environment")
    create = actions.add_parser("create", help="Create an empty SQLite budget with no optional plugins",
        epilog="Examples:\n  whab budget create empty-budget\n  whab budget create empty-budget --path '~/budgets'\n  whab budget create empty-budget --path ~/budgets/empty-budget.whab\n\nExisting files and duplicate names are refused. Default category templates are included.\nIf a result is uncertain, inspect whab budget list before trying again.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    create.add_argument("name", help="Unique budget name (1–100 characters)")
    create.add_argument("--path", help="Directory or .whab file; expands ~ even when quoted. Default: runtime-managed location")
    plugin = groups.add_parser("plugin", help="Discover installed plugins; enable them under a budget name",
        epilog="Enable for a budget: whab BUDGET plugin enable PLUGIN\n\n" + overview(installed_catalog()),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    plugin.add_argument("action", choices=("list",), nargs="?", default="list", help="List installed plugin documentation offline")
    scoped = groups.add_parser("BUDGET", help="Address a saved budget by its name or permanent ID",
        usage="whab BUDGET {plugin,insights,transactions,trnx} ...")
    scoped.add_argument("target", help=argparse.SUPPRESS)
    commands = scoped.add_subparsers(dest="command", required=True)
    plugins = commands.add_parser("plugin", help="List, enable or disable this budget's plugins",
        epilog="Example: whab empty-budget plugin enable ynab-zip-importer\nChanges apply live where supported; otherwise restart_required is returned.")
    plugins.add_argument("action", choices=("list", "status", "enable", "disable"))
    plugins.add_argument("plugin", nargs="?", help="Identifier from whab plugin list; required for enable/disable")
    insights = commands.add_parser("insights", help="Read financial reports without AI")
    reports = insights.add_subparsers(dest="report", required=True)
    summary = reports.add_parser("summary", help="Read the core monthly budget summary in one currency",
        epilog="Example: whab empty-budget insights summary --month 2026-11 --currency USD\nAmounts are integer currency minor units. No AI or bank sync is started.")
    summary.add_argument("--month", help="YYYY-MM; default: current month")
    summary.add_argument("--currency", default="USD", help="ISO currency code (default: USD); currencies are never combined")
    transactions = commands.add_parser("transactions", aliases=["trnx"], help="Read transactions (trnx is an alias)")
    reads = transactions.add_subparsers(dest="filter", required=True)
    unreviewed = reads.add_parser("non-reviewed", aliases=["unreviewed"], help="List unreviewed, non-deleted transactions",
        epilog="Example: whab empty-budget trnx non-reviewed --month 2026-11\nWithout --month, reads all available history. Never marks transactions reviewed.")
    unreviewed.add_argument("--month", help="Limit to YYYY-MM; default: all history")
    groups.add_parser("api", help="Advanced local operations; use whab api --help")
    return root


def options_parser():
    options = argparse.ArgumentParser(add_help=False)
    options.add_argument("--home", help="Runtime home; defaults to WHAB_HOME, profile home, then ~/.whab")
    options.add_argument("--environment", choices=("production", "sandbox"), help="Runtime environment (default: explicit profile environment, otherwise production)")
    options.add_argument("--profile", help="Local JSON {home, environment?, ca?}; default: ~/.config/whab/default.budget. Relative home/CA paths resolve beside the profile")
    options.add_argument("--ca", help="Override the runtime CA certificate")
    return options


def configuration(args):
    profile = Path(args.profile).expanduser() if args.profile else Path.home() / ".config" / "whab" / "default.budget"
    defaults = connection_defaults([str(profile)])[1] if args.profile or profile.exists() else {}
    if "url" in defaults:
        raise CommandError("Use a local home profile with whab; use whab-cli for remote profiles.")
    home = args.home or os.environ.get("WHAB_HOME") or defaults.get("home") or str(Path.home() / ".whab")
    environment = args.environment or defaults.get("environment", "production")
    return str(Path(home).expanduser().resolve()), environment, args.ca or defaults.get("ca")


def exchange(connection, method, path, **kwargs):
    with connection.stream(method, path, **kwargs) as response:
        if response.status_code != 200:
            raise CommandError(f"WHAB returned HTTP {response.status_code}. Inspect whab budget list and plugin status before retrying; no operation was automatically replayed.")
        raw = bytearray()
        for chunk in response.iter_bytes():
            raw.extend(chunk)
            if len(raw) > 16 * 1024 * 1024:
                raise CommandError("WHAB response exceeds 16 MiB.")
        return json.loads(raw)


def resolve_budget(connection, target):
    rows = exchange(connection, "GET", PREFIX + "/budgets")["budgets"]
    ids = [row for row in rows if row["id"] == target]
    matches = ids or [row for row in rows if row["name"].casefold() == target.casefold()]
    if len(matches) != 1:
        raise CommandError("Unknown or ambiguous budget name. Use a permanent ID from whab budget list.")
    return matches[0]["id"]


def create_path(name, value):
    if value is None:
        return None
    path = Path(value).expanduser().absolute()
    if path.suffix.lower() != ".whab":
        path = path / (quote(name.strip(), safe="") + ".whab")
    return str(path)


def execute(args, home, environment, ca):
    with local_connection(home, environment, ca=ca) as connection:
        if args.scope == "budget":
            if args.action == "list":
                return exchange(connection, "GET", PREFIX + "/budgets")
            return exchange(connection, "POST", PREFIX + "/budgets",
                json={"name": args.name, "path": create_path(args.name, args.path)})
        identifier = resolve_budget(connection, args.target)
        prefix = "/budgets/" + quote(identifier, safe="") + PREFIX
        if args.command == "plugin":
            if args.action in {"list", "status"}:
                result = exchange(connection, "GET", prefix + "/plugins")
            else:
                if not args.plugin:
                    raise CommandError("Choose a plugin identifier. Use whab plugin list to discover plugins.")
                result = exchange(connection, "POST", prefix + "/plugins", json={"action": args.action, "plugin": host_id(args.plugin)})
            return plugin_identifiers(result)
        if args.command == "insights":
            result = exchange(connection, "GET", prefix + "/summary", params={"currency": args.currency, **({"month": args.month} if args.month else {})})
        else:
            result = exchange(connection, "GET", prefix + "/queries/transactions", params={"workspace": "personal", **({"month": args.month} if args.month else {})})
        if result.get("budgetId") != identifier or not result.get("revision") or "data" not in result:
            raise CommandError("WHAB returned another budget or an invalid response.")
        if args.command in {"transactions", "trnx"}:
            result["data"] = [row for row in result["data"] if not row.get("reviewed", False) and not row.get("deleted", False)]
        return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        options = options_parser()
        global_args, remaining = options.parse_known_args(argv)
        if remaining and remaining[0] == "api":
            # The advanced adapter retains its existing operation-specific help.
            if "--help" in remaining or "-h" in remaining:
                return client.main(remaining[1:])
            home, environment, ca = configuration(global_args)
            forwarded = ["--home", home, "--environment", environment]
            if ca:
                forwarded += ["--ca", ca]
            return client.main([*forwarded, *remaining[1:]])
        if remaining and remaining[0] not in ROOT_COMMANDS and not remaining[0].startswith("-"):
            remaining.insert(0, "BUDGET")
        args = parser(options).parse_args(remaining, namespace=global_args)
        if args.scope == "plugin":
            result = describe(installed_catalog())
        else:
            result = execute(args, *configuration(args))
        print(json.dumps(result, allow_nan=False))
        return 0
    except (CommandError, ConfigError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError, TypeError, httpx.HTTPError):
        print(json.dumps({"error": "WHAB command failed. Check the runtime, profile and selected budget. Inspect state before retrying; commands are never automatically replayed."}), file=sys.stderr)
        return 1
