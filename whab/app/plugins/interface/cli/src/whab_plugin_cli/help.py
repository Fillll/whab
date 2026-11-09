"""Human-readable and machine-readable help from packaged operation contracts."""

import json
import shlex

from whab.plugins.documentation import installed_catalog, operation

from .bank_commands import BANK_FLAGS
from .identifiers import catalog_identifiers, cli_id, host_id

AGENT_WORKFLOW = """Agent workflow (examples use fictional names/IDs):
  whab budget list
  whab "My budget" trnx uncategorized --month 2026-11 --format table
  whab "My budget" trnx categories
  whab "My budget" trnx categorize TRANSACTION_ID --category "Food / Groceries" --agent Codex --needs-review --batch UNIQUE_RUN_ID
  whab "My budget" history list

Read transactions and categories before deciding; leave ambiguous payees uncategorized.
Use an explicit --agent LABEL for agent category edits. --batch groups history;
separate CLI calls are separate atomic commits. Verify the returned budget and changes.
Amounts are integer currency minor units (USD 1234 = $12.34); never combine currencies.
Revision is an opaque concurrency guard, not a historical version number. Advanced
writes require the revision used for the decision and a unique --request-id.
For an uncertain write, inspect the budget; preserve ALL original arguments, revision
and request ID for an exact retry. Never create a new intent just to retry.

Discovery:
  whab BUDGET --help                     name-addressed local commands
  whab BUDGET bank --help                mappings, schedules and investments
  whab BUDGET history --help             checkpoints and previewed undo/redo
  whab api command edit_transaction --help
  whab api describe --offline --json     installed schemas; no connection needed
  whab api --budget BUDGET_ID query describe
  whab api --budget BUDGET_ID query features
Advanced whab api is a supported CLI interface. Use permanent IDs from budget list,
not browser slugs. Offline documentation does not prove a plugin is enabled;
query describe/features checks the addressed running budget. Local/SDK entries
describe access boundaries; they are not arbitrary executable command names.
Production is the default. Use --environment sandbox for experiments.
JSON is the authoritative output; tables are for display. Success exits 0;
a successful preview has not committed. Failures use stderr (exit 1), usage errors 2.
See the CLI README's Agent workflow for scope, retry and recovery details."""


def describe(catalog, *, plugin=None, kind=None, name=None):
    """Detach and filter static descriptions; this never calls an operation."""
    value = catalog_identifiers(catalog)
    plugin = cli_id(plugin)
    if plugin is not None:
        if plugin == "core":
            value["plugins"] = []
        else:
            value["core"] = None
            value["plugins"] = [row for row in value["plugins"] if row["id"] == plugin]
            if not value["plugins"]:
                raise ValueError("No documentation for that plugin. Use describe to list installed plugins.")
    for document in ([value["core"]] if value["core"] else []) + [row["documentation"] for row in value["plugins"] if row["documented"]]:
        document["operations"] = [row for row in document["operations"]
                                  if (kind is None or row["kind"] == kind) and (name is None or row["name"] == name)]
    return value


def _fields(schema, prefix=""):
    lines = []
    required = set(schema.get("required", ()))
    for name, field in schema.get("properties", {}).items():
        kind = field.get("type", "SDK value")
        if isinstance(kind, list):
            kind = " | ".join(kind)
        values = f'; choices: {json.dumps(field["enum"])}' if "enum" in field else ""
        default = f'; default: {json.dumps(field["default"])}' if "default" in field else ""
        constraints = ''.join(f'; {key}: {json.dumps(field[key])}' for key in
                              ('const', 'minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems', 'pattern', 'format') if key in field)
        lines.append(f'  {prefix}{name} ({kind}, {"required" if name in required else "optional"}{values}{default}{constraints}): {field["description"]}')
        lines.extend(_fields(field, prefix + name + "."))
    items = schema.get("items")
    if isinstance(items, dict):
        lines.extend(_fields(items, prefix.rstrip(".") + "[]."))
    return lines


def invocation(row, plugin):
    """Only render routes supported by the public CLI adapter."""
    kind, name = row['kind'], row['name']
    if kind == 'local' and name.startswith('bank.') and name[5:] in BANK_FLAGS:
        action = name[5:]
        provider = 'PROVIDER' if plugin == 'cli' else cli_id(plugin)
        command = f'bank {action} {provider}'
        return ('CLI template (replace fictional IDs and review writes first):\n'
                f'  whab BUDGET {command}{BANK_FLAGS[action]}\n'
                f'  whab-cli CONFIG {command}{BANK_FLAGS[action]}\n'
                f'Flags: whab BUDGET {command} --help\n'
                'These local results are direct JSON, without a remote ledger response envelope.\n'
                'Use --credentials-file - for private profile input from stdin; never pass secrets in arguments.')
    if kind in {'query', 'command'} and (plugin == 'core' or name.startswith('categorization.')):
        route = f'{kind} ' + ('categorization' if name.startswith('categorization.') else name)
    elif kind in {'feature-query', 'feature-command'}:
        route = f'{kind} {cli_id(plugin)} {name}'
    else:
        return 'CLI access: use the documented local workflow or SDK integration below; this entry is not a generic CLI operation.'
    example = row['examples'][0]['input']
    parameters = shlex.quote(json.dumps(example, separators=(',', ':')))
    arguments = f' --parameters {parameters}'
    if kind == 'query' and name not in {'feature', 'history', 'history_preview'}:
        arguments = f' --month {shlex.quote(example["month"])}' if 'month' in example else ''
    guard = ' --revision OBSERVED_REVISION --request-id UNIQUE_INTENT_ID' if kind in {'command', 'feature-command'} else ''
    return (f'CLI template (replace fictional IDs/values; examples are not observed state):\n'
            f'  whab api --budget BUDGET_ID {route}{arguments}{guard}\n'
            'Output examples describe data inside the budgetId/revision/data response envelope.\n'
            'Use describe --json for full schemas, including conditional requirements.')


def render_operation(row, plugin='core'):
    lines = [f'{row["kind"]} {row["name"]}', row["summary"], "", invocation(row, plugin), "", "Input fields:"]
    lines.extend(_fields(row["input"]))
    if not row["input"].get("properties"):
        lines.append("  No operation-specific fields.")
    output = row["output"]
    lines += ["", "Output: " + output["description"]]
    lines.extend(_fields(output))
    lines += [
              "Scope: " + row["scope"], "Access: " + row["permissions"],
              "Availability: " + row["availability"], "Side effects: " + row["side_effects"],
              "Retry: " + row["retry"], "Errors:", *["  " + item for item in row["errors"]], "Examples:"]
    for example in row["examples"]:
        lines += ["  input: " + json.dumps(example["input"]), "  output: " + json.dumps(example["output"])]
    return "\n".join(lines)


def render_catalog(catalog):
    lines = ["WHAB operation documentation (" + catalog["source"] + ")", ""]
    documents = ([catalog["core"]] if catalog["core"] else [])
    for plugin in catalog["plugins"]:
        enabled = ("installed; management tool" if plugin.get("selection_control") == "management" else
                   "installed; enablement unknown offline" if plugin["enabled"] is None else "enabled" if plugin["enabled"] else "installed; disabled")
        lines.append(f'Plugin {cli_id(plugin["id"])} {plugin["version"]}: {enabled}')
        if plugin["documentation"]:
            documents.append(plugin["documentation"])
        else:
            lines.append("  Legacy plugin: no packaged operation documentation.")
    for document in documents:
        lines += ["", cli_id(document["plugin_id"]) + ": " + document["summary"]]
        if document["delegates"]:
            lines.append("Delegates: " + ", ".join(document["delegates"]))
        for row in document["operations"]:
            lines += ["", render_operation(row, document['plugin_id'])]
    if "http" in catalog:
        lines += ["", "HTTP API (running authority)", catalog["http"]["access"]]
        for path, methods in catalog["http"]["openapi"].get("paths", {}).items():
            for method, contract in methods.items():
                if method in {"get", "post", "put", "patch", "delete", "options", "head"}:
                    lines.append(method.upper() + " " + path + ": " + contract.get("summary", contract.get("operationId", "")))
        lines.append("Use describe --json for the complete HTTP parameter/body/response schemas. Documentation does not grant route access.")
    return "\n".join(lines)


def account_classification(catalog):
    """Present the core's account semantics with the existing CLI read/edit path."""
    fields = operation(catalog["core"], "query", "accounts")["output"]["items"]["properties"]
    return "\n".join([
        "Account classification:",
        "  type: " + fields["type"]["description"],
        "  onBudget: " + fields["onBudget"]["description"],
        "  balance: " + fields["balance"]["description"],
        "Inspect current values: whab-cli CONFIG query accounts (or whab api query accounts).",
        "Review edit fields/examples offline: whab-cli command update_account --help",
        "  or whab api command update_account --help.",
        "Account reads return the stable account ID and revision needed for an explicit edit.",
    ])


def overview(catalog):
    groups = []
    for kind in ("query", "command"):
        groups.append(kind.title() + " operations: " + ", ".join(row["name"] for row in catalog["core"]["operations"] if row["kind"] == kind))
    groups.append(account_classification(catalog))
    groups.append("Installed plugin documentation (local and SDK entries describe access, not CLI execution):")
    for plugin in catalog["plugins"]:
        document = plugin["documentation"]
        names = "; ".join(row["kind"] + " " + row["name"] for row in document["operations"]) if document else "legacy; undocumented"
        summary = document["summary"] + "\n    " if document else ""
        groups.append("  " + cli_id(plugin["id"]) + ": " + summary + names)
    groups.append("Use command OPERATION --help or feature-command PLUGIN ACTION --help for fields and examples.\nUse describe --json for machine-readable schemas, errors and access rules. An authenticated online describe also includes the running HTTP OpenAPI contract. Local/SDK descriptions do not enable remote access.")
    return "\n\n".join(groups)


def contextual_help(argv, catalog=None):
    """Choose an operation topic without executing it or reading credentials."""
    catalog = catalog or installed_catalog()
    if "describe" in argv and "--plugin" in argv:
        index = argv.index("--plugin")
        if index + 1 < len(argv):
            filters = {}
            for flag, key in (("--kind", "kind"), ("--operation", "name")):
                if flag in argv and argv.index(flag) + 1 < len(argv):
                    filters[key] = argv[argv.index(flag) + 1]
            return render_catalog(describe(catalog, plugin=argv[index + 1], **filters))
    for index, token in enumerate(argv):
        if token in {"query", "command"} and index + 1 < len(argv):
            row = operation(catalog["core"], token, argv[index + 1])
            return render_operation(row) if row else overview(catalog)
        if token in {"feature-query", "feature-command"} and index + 2 < len(argv):
            plugin, name = argv[index + 1:index + 3]
            document = next((row["documentation"] for row in catalog["plugins"] if row["id"] == host_id(plugin)), None)
            row = operation(document, token, name) if document else None
            return render_operation(row, plugin) if row else "No installed documentation for that operation. Use an authenticated describe --json against its authority."
    return overview(catalog)


def bank_help(argv, catalog):
    for index, token in enumerate(argv):
        if token == 'bank' and index + 2 < len(argv) and argv[index + 1] in BANK_FLAGS:
            plugin = host_id(argv[index + 2])
            document = next((row['documentation'] for row in catalog['plugins'] if row['id'] == plugin), None)
            row = operation(document, 'local', 'bank.' + argv[index + 1]) if document else None
            return render_operation(row, plugin) if row else 'No installed operation documentation for this provider; use whab api describe --help.'
    return None
