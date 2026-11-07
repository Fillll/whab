"""Noninteractive JSON CLI; public HTTPS operations only, with no model access."""

import argparse
import json
import os
import ssl
import sys
from copy import copy
from urllib.parse import quote, urlsplit

import httpx

from whab.plugins.documentation import core_document, installed_catalog

from .config import ConfigError, connection_defaults
from .help import contextual_help, describe, overview, render_catalog
from .identifiers import check_identifiers, host_id, plugin_identifiers


def parser(argv=(), *, defaults=None):
    catalog = installed_catalog()
    check_identifiers(catalog)
    root = argparse.ArgumentParser(description="Read and change an explicitly addressed WHAB budget.",
        usage="%(prog)s [CONFIG] [OPTIONS] {query,command,feature-query,feature-command,describe,plugins,sync,jobs} ...",
        epilog='Optional CONFIG is a JSON object. Local fields: home, budget, environment, ca, workspace. Remote fields: url, budget, ca, workspace, token_env. Choose home or url. Command-line options override the file. Relative home/CA paths use the config directory.\nLocal example: {"home": "runtime", "budget": "default", "environment": "sandbox"}\nRemote example: {"url": "https://localhost:5173", "budget": "default", "ca": "tls/ca.crt"}\nUsage: whab-cli default.budget --help\n\n' + overview(catalog),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    root.add_argument("--url", help="HTTPS server origin; omit for offline documentation")
    root.add_argument("--home", help="Local runtime home; reuse its host or start a temporary host")
    root.add_argument("--environment", choices=("sandbox", "production"), default="production", help="Local runtime environment (default: %(default)s)")
    root.add_argument("--budget", help="Stable budget ID; required for online operations")
    root.add_argument("--token-env", default="WHAB_CLI_TOKEN", help="Environment variable containing the server access token (default: %(default)s)")
    root.add_argument("--ca", help="Explicit CA certificate file; otherwise use system trust")
    root.add_argument("--workspace", choices=("personal", "demo"), default="personal", help="Addressed workspace (default: %(default)s)")
    root.set_defaults(**(defaults or {}))
    commands = root.add_subparsers(dest="kind", required=True)
    manage = commands.add_parser("plugins", help="Manage installed plugins on the local host")
    manage.add_argument("action", choices=("list", "status", "enable", "disable"))
    manage.add_argument("plugin", nargs="?", help="Installed plugin ID for enable/disable")
    sync = commands.add_parser("sync", help="Synchronize existing bank connections without opening Link")
    sync.add_argument("plugin", help="Enabled bank plugin ID, for example plaid")
    jobs = commands.add_parser("jobs", help="Inspect/run due bank jobs or enable/disable the OS schedule")
    jobs.add_argument("action", choices=("status", "run", "enable", "disable"))
    topic = contextual_help(argv, catalog)
    def group(name, text):
        return commands.add_parser(name, help=text, epilog=topic, formatter_class=argparse.RawDescriptionHelpFormatter)
    query = group("query", "Read a public ledger projection")
    query.add_argument("operation", choices=tuple(row["name"] for row in core_document()["operations"] if row["kind"] == "query"))
    query.add_argument("--month")
    query.add_argument("--parameters", help="JSON parameters for a generic feature query")
    command = group("command", "Apply an explicit public ledger command")
    command.add_argument("operation", choices=tuple(row["name"] for row in core_document()["operations"] if row["kind"] == "command"))
    for name in ("feature-query", "feature-command"):
        feature = group(name, "Call an operation listed by query features")
        feature.add_argument("plugin")
        feature.add_argument("action")
    for command_parser in (command, commands.choices["feature-query"], commands.choices["feature-command"]):
        command_parser.add_argument("--parameters", required=True, help="JSON object; use '-' to read standard input")
    for command_parser in (command, commands.choices["feature-command"]):
        command_parser.add_argument("--revision", required=True, help="Revision returned by the read used to make this decision")
        command_parser.add_argument("--request-id", required=True, help="Unique intent ID; retain it unchanged for an exact retry")
    docs = group("describe", "Read full operation documentation without executing operations")
    docs.add_argument("--json", action="store_true", help="Output machine-readable schemas and examples")
    docs.add_argument("--offline", action="store_true", help="Use installed package documentation even when a URL is supplied")
    docs.add_argument("--plugin", help="Filter to core or an installed plugin ID")
    docs.add_argument("--kind", dest="filter_kind", choices=("query", "command", "feature-query", "feature-command", "local", "sdk"))
    docs.add_argument("--operation", help="Filter by exact operation name")
    return root


def request(args, token=None):
    """Build a detached intent before opening a network connection."""
    if not args.url or not args.budget:
        raise ValueError("Online operations require --url and --budget. Use --help or describe offline.")
    origin = urlsplit(args.url)
    if (origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.password is not None or origin.path not in ("", "/")
            or origin.query or origin.fragment or any(c.isspace() for c in args.url)):
        raise ValueError("Choose an HTTPS server origin without credentials or a path.")
    origin.port
    token = os.environ.get(args.token_env, "") if token is None else token
    if not 32 <= len(token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("Set the named access-token environment variable to a valid server token.")
    if not args.budget or len(args.budget) > 200 or any(ord(c) < 32 for c in args.budget):
        raise ValueError("Choose a valid budget ID.")
    path = "/remote/v1/budgets/" + quote(args.budget, safe="")
    if args.kind == "describe":
        return token, "GET", path + "/queries/describe", {"params": {"workspace": args.workspace}}
    if args.kind == "query" and args.operation != "feature":
        params = {"workspace": args.workspace}
        if args.month is not None:
            params["month"] = args.month
        return token, "GET", path + "/queries/" + args.operation, {"params": params}
    raw = sys.stdin.read(1024 * 1024 + 1) if args.parameters == "-" else args.parameters
    if raw is None:
        raise ValueError("This operation requires JSON parameters.")
    if len(raw.encode()) > 1024 * 1024:
        raise ValueError("Parameters exceed 1 MiB.")
    values = json.loads(raw)
    if type(values) is not dict:
        raise ValueError("Parameters must be a JSON object.")
    json.dumps(values, allow_nan=False)
    payload = {"pluginId": host_id(args.plugin), "action": args.action, "parameters": values} if args.kind.startswith("feature-") else values
    if args.kind == "feature-query" or args.kind == "query":
        return token, "POST", path + "/queries", {"json": {
            "operation": "feature", "workspace": args.workspace, "parameters": payload,
        }}
    return token, "POST", path + "/commands", {"json": {
        "operation": "feature" if args.kind == "feature-command" else args.operation,
        "workspace": args.workspace, "payload": payload,
        "request_id": args.request_id, "expected_revision": args.revision,
    }}


def _local(args):
    from whab.plugins.local import local_connection

    if args.url or args.workspace != "personal" and args.kind in {"plugins", "sync", "jobs"}:
        raise ValueError("Choose a local home and personal workspace for local management.")
    if args.kind == "jobs" and args.action in {"enable", "disable"}:
        from whab.plugins.scheduling import configure_schedule
        return configure_schedule(args.home, args.environment, enabled=args.action == "enable")
    with local_connection(args.home, args.environment, ca=args.ca) as client:
        prefix = "/budgets/" + quote(args.budget or "default", safe="") + "/api/local/v1"
        kwargs = {}
        if args.kind == "plugins":
            if args.action in {"list", "status"}:
                method, path = "GET", prefix + "/plugins"
            else:
                if not args.plugin:
                    raise ValueError("Choose a plugin ID.")
                method, path = "POST", prefix + "/plugins"
                kwargs = {"json": {"action": args.action, "plugin": host_id(args.plugin)}}
        elif args.kind == "sync":
            method, path, kwargs = "POST", prefix + "/sync", {"json": {"plugin": host_id(args.plugin)}}
        elif args.kind == "jobs":
            method, path = ("POST", prefix + "/jobs/run") if args.action == "run" else ("GET", prefix + "/jobs")
            kwargs = {"json": {}} if args.action == "run" else {}
        else:
            local = copy(args)
            local.url, local.budget = str(client.base_url), args.budget or "default"
            _, method, path, kwargs = request(local, "x" * 32)
            path = prefix + path.partition("/budgets/" + quote(local.budget, safe=""))[2]
        with client.stream(method, path, **kwargs) as response:
            if response.status_code != 200:
                raise ValueError("Local operation failed. Inspect the budget before retrying.")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError("Local response exceeds 16 MiB.")
            result = json.loads(raw)
        if args.kind not in {"plugins", "sync", "jobs"} and (
                type(result) is not dict or result.get("budgetId") != (args.budget or "default")
                or type(result.get("revision")) is not str or not result["revision"] or "data" not in result):
            raise ValueError("Local host returned another budget or an invalid response.")
        return plugin_identifiers(result) if args.kind == "plugins" else result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        argv, defaults = connection_defaults(argv)
        args = parser(argv, defaults=defaults).parse_args(argv)
        if args.kind == "describe" and (not args.url or args.offline):
            data = describe(installed_catalog(), plugin=args.plugin, kind=args.filter_kind, name=args.operation)
            print(json.dumps(data, allow_nan=False) if args.json else render_catalog(data))
            return 0
        if args.home:
            print(json.dumps(_local(args), allow_nan=False))
            return 0
        if args.kind in {"plugins", "sync", "jobs"}:
            raise ValueError("Local management requires --home or a local JSON profile.")
        token, method, path, kwargs = request(args)
        with httpx.Client(base_url=args.url.rstrip("/"),
                          headers={"Authorization": "Bearer " + token},
                          verify=ssl.create_default_context(cafile=args.ca),
                          timeout=30, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, path, **kwargs) as response:
                if response.status_code != 200:
                    raise ValueError(f"WHAB returned HTTP {response.status_code}. Inspect the budget before changing or retrying an intent.")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError("Response exceeds 16 MiB.")
                result = json.loads(raw)
                if (type(result) is not dict or result.get("budgetId") != args.budget
                        or type(result.get("revision")) is not str or not result["revision"]
                        or "data" not in result):
                    raise ValueError("WHAB returned an invalid response or another budget.")
        if args.kind == "describe":
            result = describe(result["data"], plugin=args.plugin, kind=args.filter_kind, name=args.operation)
            print(json.dumps(result, allow_nan=False) if args.json else render_catalog(result))
        else:
            print(json.dumps(result, allow_nan=False))
        return 0
    except ConfigError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except (ValueError, OSError, httpx.HTTPError):
        # Do not echo transport exceptions, request payloads or credentials.
        print(json.dumps({"error": "WHAB request failed. Verify the connection, inputs and current budget. For an uncertain write, retain its original request ID, revision and parameters; do not submit a new intent."}), file=sys.stderr)
        return 1
