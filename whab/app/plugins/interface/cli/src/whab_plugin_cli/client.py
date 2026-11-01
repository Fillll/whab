"""Noninteractive JSON CLI; public HTTPS operations only, with no model access."""

import argparse
import json
import os
import ssl
import sys
from urllib.parse import quote, urlsplit

import httpx


def parser():
    root = argparse.ArgumentParser(description="Read and change an explicitly addressed WHAB budget.")
    root.add_argument("--url", required=True, help="HTTPS server origin")
    root.add_argument("--budget", required=True, help="Stable budget ID")
    root.add_argument("--token-env", default="WHAB_CLI_TOKEN", help="Environment variable containing the server access token")
    root.add_argument("--ca", help="Explicit CA certificate file; otherwise use system trust")
    root.add_argument("--workspace", choices=("personal", "demo"), default="personal")
    commands = root.add_subparsers(dest="kind", required=True)
    query = commands.add_parser("query", help="Read a public ledger projection")
    query.add_argument("operation", choices=("accounts", "categories", "transactions", "workspace", "features"))
    query.add_argument("--month")
    command = commands.add_parser("command", help="Apply an explicit public ledger command")
    command.add_argument("operation")
    for name in ("feature-query", "feature-command"):
        feature = commands.add_parser(name, help="Call an operation listed by query features")
        feature.add_argument("plugin")
        feature.add_argument("action")
    for command_parser in (command, commands.choices["feature-query"], commands.choices["feature-command"]):
        command_parser.add_argument("--parameters", required=True, help="JSON object; use '-' to read standard input")
    for command_parser in (command, commands.choices["feature-command"]):
        command_parser.add_argument("--revision", required=True, help="Revision returned by the read used to make this decision")
        command_parser.add_argument("--request-id", required=True, help="Unique intent ID; retain it unchanged for an exact retry")
    return root


def request(args):
    """Build a detached intent before opening a network connection."""
    origin = urlsplit(args.url)
    if (origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.password is not None or origin.path not in ("", "/")
            or origin.query or origin.fragment or any(c.isspace() for c in args.url)):
        raise ValueError("Choose an HTTPS server origin without credentials or a path.")
    origin.port
    token = os.environ.get(args.token_env, "")
    if not 32 <= len(token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("Set the named access-token environment variable to a valid server token.")
    if not args.budget or len(args.budget) > 200 or any(ord(c) < 32 for c in args.budget):
        raise ValueError("Choose a valid budget ID.")
    path = "/remote/v1/budgets/" + quote(args.budget, safe="")
    if args.kind == "query":
        params = {"workspace": args.workspace}
        if args.month is not None:
            params["month"] = args.month
        return token, "GET", path + "/queries/" + args.operation, {"params": params}
    raw = sys.stdin.read(1024 * 1024 + 1) if args.parameters == "-" else args.parameters
    if len(raw.encode()) > 1024 * 1024:
        raise ValueError("Parameters exceed 1 MiB.")
    values = json.loads(raw)
    if type(values) is not dict:
        raise ValueError("Parameters must be a JSON object.")
    json.dumps(values, allow_nan=False)
    payload = {"pluginId": args.plugin, "action": args.action, "parameters": values} if args.kind.startswith("feature-") else values
    if args.kind == "feature-query":
        return token, "POST", path + "/queries", {"json": {
            "operation": "feature", "workspace": args.workspace, "parameters": payload,
        }}
    return token, "POST", path + "/commands", {"json": {
        "operation": "feature" if args.kind == "feature-command" else args.operation,
        "workspace": args.workspace, "payload": payload,
        "request_id": args.request_id, "expected_revision": args.revision,
    }}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
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
        print(json.dumps(result, allow_nan=False))
        return 0
    except (ValueError, OSError, httpx.HTTPError):
        # Do not echo transport exceptions, request payloads or credentials.
        print(json.dumps({"error": "WHAB request failed. Verify the connection, inputs and current budget. For an uncertain write, retain its original request ID, revision and parameters; do not submit a new intent."}), file=sys.stderr)
        return 1
