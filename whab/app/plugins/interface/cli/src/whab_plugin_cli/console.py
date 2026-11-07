"""Name-addressed local console; the host owns storage and financial rules."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

from whab.plugins.documentation import installed_catalog
from whab.plugins.local import local_connection

from . import client
from .bank_commands import add_bank_parser, bank_error, bank_request
from .config import ConfigError, connection_defaults
from .errors import selection_error
from .help import account_classification, describe, overview
from .identifiers import check_identifiers, host_id, plugin_identifiers

PREFIX = "/api/local/v1"
ROOT_COMMANDS = {"budget", "plugin", "api", "BUDGET"}


class CommandError(ValueError):
    """A controlled diagnostic safe to show without transport/private values."""


def parser(options):
    catalog = installed_catalog()
    check_identifiers(catalog)
    root = argparse.ArgumentParser(prog="whab", parents=[options],
        description="Budget from the command line. Production is the default; choose --environment sandbox for tests.",
        epilog="Examples:\n  whab budget create empty-budget --path ~/budgets\n  whab empty-budget plugin enable ynab-zip-importer\n  whab empty-budget import ynab-zip-importer preview export.zip --currency USD\n  whab empty-budget insights summary\n  whab empty-budget trnx non-reviewed\n\nEvery command supports --help. Names containing spaces can be quoted.\nUse whab budget list to discover names and permanent IDs.\n\n" + account_classification(catalog),
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
        epilog="Enable for a budget: whab BUDGET plugin enable PLUGIN\n\n" + overview(catalog),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    plugin.add_argument("action", choices=("list",), nargs="?", default="list", help="List installed plugin documentation offline")
    scoped = groups.add_parser("BUDGET", help="Address a saved budget by its name or permanent ID",
        usage="whab BUDGET {plugin,import,insights,transactions,trnx} ...")
    scoped.add_argument("target", help=argparse.SUPPRESS)
    commands = scoped.add_subparsers(dest="command", required=True)
    plugins = commands.add_parser("plugin", help="List, enable or disable this budget's plugins",
        epilog="Example: whab empty-budget plugin enable ynab-zip-importer\nChanges apply live where supported; otherwise restart_required is returned. CLI is an installed management tool, not a budget toggle. Required storage cannot be disabled. Web is optional and can be disabled.")
    plugins.add_argument("action", choices=("list", "status", "enable", "disable"))
    plugins.add_argument("plugin", nargs="?", help="Identifier from whab plugin list; required for enable/disable")
    add_bank_parser(commands)
    imports = commands.add_parser("import", help="Preview or commit a ZIP import into this empty budget",
        epilog="Enable an importer first: whab BUDGET plugin enable ynab-zip-importer\nThen: whab BUDGET import ynab-zip-importer preview --help",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    imports.add_argument("plugin", help="Enabled file importer, for example ynab-zip-importer (ynab is also accepted)")
    stages = imports.add_subparsers(dest="stage", required=True)
    for stage in ("preview", "commit"):
        operation = stages.add_parser(stage, help="Read counts and warnings without changing financial data" if stage == "preview" else "Import the reviewed archive atomically into an empty budget",
            epilog="Example:\n  whab empty-budget import ynab-zip-importer preview '~/Downloads/export.zip' --currency USD\n  whab empty-budget import ynab-zip-importer commit '~/Downloads/export.zip' --currency USD --fingerprint SHA256_FROM_PREVIEW\n\nCommit verifies the reviewed fingerprint, previews again and echoes that parser's identity in the same host lease. Nonempty budgets and changed files/parsers are refused. No automatic retries; after an uncertain commit, preview the same file to inspect alreadyImported before retrying that fingerprint. Imported accounts require classification; exported balances remain unverified estimates.",
            formatter_class=argparse.RawDescriptionHelpFormatter)
        operation.add_argument("file", help="Local ZIP export, at most 10 MiB; quoted ~ expands")
        operation.add_argument("--currency", required=True, help="Confirm the export currency explicitly (USD for YNAB)")
        if stage == "commit":
            operation.add_argument("--fingerprint", required=True, help="Exact SHA-256 fingerprint returned by the reviewed preview")
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


def exchange(connection, method, path, *, import_errors=False, plugin_errors=False, bank_errors=False, **kwargs):
    with connection.stream(method, path, **kwargs) as response:
        raw = bytearray()
        for chunk in response.iter_bytes():
            raw.extend(chunk)
            if len(raw) > 16 * 1024 * 1024:
                raise CommandError("WHAB response exceeds 16 MiB.")
        if response.status_code != 200:
            if bank_errors and response.status_code in {400, 409}:
                message = bank_error(raw)
                if message:
                    raise CommandError(message)
            if plugin_errors and response.status_code in {400, 409}:
                message = selection_error(raw)
                if message:
                    raise CommandError(message)
            detail = None
            if import_errors and response.status_code in {400, 409, 413}:
                try:
                    body = json.loads(raw)
                    detail = body.get("detail", body.get("error")) if isinstance(body, dict) else None
                except ValueError:
                    pass
            if isinstance(detail, str) and 0 < len(detail) <= 1000:
                raise CommandError(f"WHAB import returned HTTP {response.status_code}: {detail}")
            raise CommandError(f"WHAB returned HTTP {response.status_code}. Inspect whab budget list and plugin status before retrying; no operation was automatically replayed.")
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


def import_file(args):
    """Read bounded bytes and bind explicit confirmation before host acquisition."""
    with Path(args.file).expanduser().open("rb") as stream:
        raw = stream.read(10 * 1024 * 1024 + 1)
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise CommandError("Choose a ZIP export no larger than 10 MiB.")
    if not re.fullmatch(r"[A-Z]{3}", args.currency):
        raise CommandError("Confirm a three-letter uppercase export currency, for example USD.")
    fingerprint = hashlib.sha256(raw).hexdigest()
    if args.stage == "commit" and args.fingerprint != fingerprint:
        raise CommandError("The file does not match the reviewed fingerprint. Preview it again before importing.")
    return raw, fingerprint


def execute_import(connection, identifier, args, raw, fingerprint):
    path = "/budgets/" + quote(identifier, safe="") + "/api/plugins/" + quote(host_id(args.plugin), safe="") + "/import/"
    headers = {"Content-Type": "application/zip", "X-Import-Currency": args.currency}
    preview = exchange(connection, "POST", path + "preview", content=raw, headers=headers, import_errors=True)
    if (not isinstance(preview, dict) or preview.get("fingerprint") != fingerprint or preview.get("currency") != args.currency
            or not isinstance(preview.get("parserIdentity"), str) or not re.fullmatch(r"[!-~]{1,256}", preview["parserIdentity"])):
        raise CommandError("WHAB returned an invalid import preview; nothing was committed.")
    if args.stage == "preview":
        return {**preview, "budgetId": identifier}
    if preview.get("canImport") is not True:
        raise CommandError("This budget cannot import the file. Run preview to inspect the reason; nothing was committed.")
    result = exchange(connection, "POST", path + "commit", content=raw,
        headers={**headers, "X-Import-Fingerprint": fingerprint, "X-Import-Parser": preview["parserIdentity"]}, import_errors=True)
    return {**result, "budgetId": identifier, "fingerprint": fingerprint}


def execute(args, home, environment, ca):
    raw, fingerprint = import_file(args) if args.scope == "BUDGET" and args.command == "import" else (None, None)
    with local_connection(home, environment, ca=ca) as connection:
        if args.scope == "budget":
            if args.action == "list":
                return exchange(connection, "GET", PREFIX + "/budgets")
            return exchange(connection, "POST", PREFIX + "/budgets",
                json={"name": args.name, "path": create_path(args.name, args.path)})
        identifier = resolve_budget(connection, args.target)
        prefix = "/budgets/" + quote(identifier, safe="") + PREFIX
        if args.command == "bank":
            method, path, kwargs = bank_request(args, identifier)
            return exchange(connection, method, path, bank_errors=True, **kwargs)
        if args.command == "import":
            return execute_import(connection, identifier, args, raw, fingerprint)
        if args.command == "plugin":
            if args.action in {"list", "status"}:
                result = exchange(connection, "GET", prefix + "/plugins")
            else:
                if not args.plugin:
                    raise CommandError("Choose a plugin identifier. Use whab plugin list to discover plugins.")
                result = exchange(connection, "POST", prefix + "/plugins", plugin_errors=True,
                                  json={"action": args.action, "plugin": host_id(args.plugin)})
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
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, httpx.HTTPError):
        print(json.dumps({"error": "WHAB command failed. Check the runtime, profile and selected budget. Inspect state before retrying; commands are never automatically replayed."}), file=sys.stderr)
        return 1
