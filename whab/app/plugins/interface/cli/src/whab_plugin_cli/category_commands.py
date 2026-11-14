"""Name-addressed hierarchy management over guarded public ledger operations."""

import argparse
import json
from uuid import uuid4

lazy import httpx

from whab.plugins import validate_category_agent
from whab.plugins.documentation import operation

from .transaction_commands import checked
from .transaction_reads import terminal_text


def add_category_parser(commands, catalog):
    document = next(row["documentation"] for row in catalog["plugins"] if row["id"] == "cli")
    guide = operation(document, "local", "category.workflow")["summary"]
    group = commands.add_parser(
        "category",
        help="List, create, rename, move and safely delete categories by name or ID",
        description="Manage the two-level category hierarchy in one explicitly addressed budget.",
        epilog=guide,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = group.add_subparsers(dest="category_action", required=True)
    from .record_edits import add_rename_parser
    add_rename_parser(actions)
    from .category_classification import add_classify_parser
    add_classify_parser(actions)
    listing = actions.add_parser(
        "list",
        help="Read parent groups and child categories, including empty groups",
        description="Read names, stable IDs, parent paths, currencies and integer targets. No financial changes.",
        epilog=guide,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    listing.add_argument(
        "--parent", help="Only this parent group, by exact unique name or stable ID"
    )
    showing = actions.add_parser('show', help='Inspect one child and all deletion blockers without writing',
        description='Read live/deleted transaction counts and every assignment month, including zero rows.',
        epilog=guide, formatter_class=argparse.RawDescriptionHelpFormatter)
    showing.add_argument('category', help='Child ID, unique exact name or "Group / Name"')
    parents = actions.add_parser(
        "group",
        help="Manage nonassignable parent groups",
        epilog=guide,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parent_actions = parents.add_subparsers(dest="group_action", required=True)
    parent_create = parent_actions.add_parser(
        "create",
        help="Save a real empty parent group",
        description="Create an organizational parent without adding a placeholder child, target or assignment.",
        epilog=guide,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parent_create.add_argument(
        "name", help="New unique group name; quote spaces, emoji and non-Latin names"
    )
    parent_delete = parent_actions.add_parser(
        "delete", help="Delete an empty parent group; never deletes children",
        epilog=guide, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parent_delete.add_argument("category", help="Existing empty parent by unique exact name or stable ID")
    delete = actions.add_parser(
        "delete", help="Delete an unused child and its zero-only assignment rows",
        epilog=guide, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    delete.add_argument("category", help='Child stable ID, unique exact name or "Group / Name"; quote Unicode/spaces')
    create = actions.add_parser(
        "create",
        help="Create an assignable child under an existing parent",
        description="Create a child with target zero by default; duplicate sibling names are refused.",
        epilog=guide,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    create.add_argument(
        "name",
        help="New child name; duplicate siblings are refused, ignoring case/outer whitespace",
    )
    create.add_argument("--agent", type=validate_category_agent, help="Explicit agent audit label; default: manual User")
    create.add_argument("--batch", help="History grouping label; this creation remains one commit/save")
    create.add_argument("--type", dest="category_kind", choices=("expense", "income"), default="expense",
        help="Financial type (default: expense); income has target zero, no assignments, independent review. See category classify --help")
    create.add_argument(
        "--currency",
        default="USD",
        help="Supported ISO currency (default: USD); never converts money",
    )
    create.add_argument(
        "--target",
        dest="category_target",
        type=int,
        default=0,
        help="Nonnegative integer currency minor units (default: 0); USD 1234 is $12.34",
    )
    move = actions.add_parser(
        "move",
        help="Move an existing child without changing its identity or financial history",
        description="Reparent only; preserves ID, target, currency, transactions and monthly assignments.",
        epilog=guide,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    move.add_argument(
        "category", help='Stable child ID, unique name or "Group / Name"; no fuzzy matching'
    )
    for parser in (create, move):
        parser.add_argument(
            "--parent",
            required=True,
            help="Existing parent group by unique exact name or stable ID; children cannot be parents",
        )
    for parser in (listing, showing, parent_create, create, move, delete, parent_delete):
        parser.add_argument(
            "--format",
            choices=("json", "table"),
            default="json",
            help="Output format (default: json); tables may shorten text",
        )


def resolve_category(rows, value, error, *, parent=False):
    if type(value) is not str:
        raise error("WHAB returned an invalid category identity.")
    key = value.strip().casefold()
    matches = [row for row in rows if row["id"] == value] or [
        row
        for row in rows
        if row["name"].strip().casefold() == key
        or (not parent and f"{row['group'].strip()} / {row['name'].strip()}".casefold() == key)
    ]
    if len(matches) != 1:
        candidates = [
            {
                "id": row["id"],
                "name": row["name"],
                **({"group": row["group"]} if "group" in row else {}),
            }
            for row in (matches or rows)[:20]
        ]
        raise error(
            "Unknown or ambiguous category "
            + ("group" if parent else "child")
            + ". Candidates (up to 20): "
            + json.dumps(candidates, ensure_ascii=True)
            + ". Use category list and an exact ID; no fuzzy match was selected."
        )
    return matches[0]


def execute_categories(connection, identifier, prefix, args, exchange, error):
    def read():
        return checked(
            exchange(
                connection,
                "GET",
                prefix + "/queries/category_tree",
                params={"workspace": "personal"},
            ),
            identifier,
            error,
        )

    observed = read()
    groups = observed["data"]
    children = [row for group in groups for row in group["children"]]
    if args.category_action == "classify":
        from .category_classification import execute_classification
        return execute_classification(connection, identifier, prefix, args, exchange, error, observed, read,
                                      resolve_category(children, args.category, error))
    if args.category_action == "rename":
        from .record_edits import execute_rename
        return execute_rename(connection, identifier, prefix, args, exchange, error, observed, read,
                              resolve_category(children, args.category, error))
    if args.category_action == "list":
        selected = (
            [resolve_category(groups, args.parent, error, parent=True)] if args.parent else groups
        )
        return {**observed, "budget": args.target, "data": selected}
    if args.category_action == 'show':
        selected = resolve_category(children, args.category, error)
        status = checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True,
            json={'operation': 'category_deletion_status', 'workspace': 'personal',
                  'parameters': {'categoryId': selected['id']}}), identifier, error)
        if status['revision'] != observed['revision'] or status['data']['categoryId'] != selected['id']:
            raise error('Category changed during inspection. Read category show again before deciding.')
        return {**status, 'budget': args.target, 'data': {**selected, 'deletion': status['data']}}
    if args.category_action == "delete" or args.category_action == "group" and args.group_action == "delete":
        return execute_delete(connection, identifier, prefix, args, exchange, error, observed, read)
    if args.category_action == "group":
        if any(row["name"].strip().casefold() == args.name.strip().casefold() for row in groups):
            raise error(
                "Category group already exists. Inspect category list; no duplicate was created."
            )
        operation_name, payload = "add_category_group", {"name": args.name}
    else:
        parent = resolve_category(groups, args.parent, error, parent=True)
        if args.category_action == "create":
            if any(
                row["name"].strip().casefold() == args.name.strip().casefold()
                for row in parent["children"]
            ):
                raise error(
                    "A category with this name already exists under this parent; no duplicate was created."
                )
            operation_name = "add_category"
            payload = {
                "name": args.name,
                "parentId": parent["id"],
                "target": args.category_target,
                "currency": args.currency,
                **({"kind": "income"} if args.category_kind == "income" else {}),
            }
        else:
            child = resolve_category(children, args.category, error)
            if any(
                row["id"] != child["id"]
                and row["name"].strip().casefold() == child["name"].strip().casefold()
                for row in parent["children"]
            ):
                raise error(
                    "A category with this name already exists under this destination; nothing was moved."
                )
            operation_name, payload = (
                "move_category",
                {"categoryId": child["id"], "parentId": parent["id"]},
            )
    intent = {
        "operation": operation_name,
        "workspace": "personal",
        "payload": payload,
        "expected_revision": observed["revision"],
        "request_id": str(uuid4()),
    }
    if args.category_action == "create":
        history = {"actor": args.agent, "actorKind": "agent"} if args.agent is not None else {}
        if args.batch is not None:
            history["batchId"] = args.batch
        if history:
            intent["history"] = history
    try:
        saved = checked(
            exchange(connection, "POST", prefix + "/commands", json=intent), identifier, error
        )
        verified = read()
        if verified["revision"] != saved["revision"]:
            raise error("Category hierarchy changed during verification.")
        rows = (
            verified["data"]
            if args.category_action == "group"
            else [row for group in verified["data"] for row in group["children"]]
        )
        actual = resolve_category(
            rows,
            child["id"] if operation_name == "move_category" else saved["data"],
            error,
            parent=args.category_action == "group",
        )
        expected = (
            {"name": args.name.strip()}
            if args.category_action == "group"
            else {**child, "parentId": parent["id"], "group": parent["name"]}
            if operation_name == "move_category"
            else {
                "name": args.name.strip(),
                "parentId": parent["id"],
                "group": parent["name"],
                "target": args.category_target,
                "currency": args.currency,
                **({"kind": "income"} if args.category_kind == "income" else {}),
            }
        )
        if any(actual.get(key) != value for key, value in expected.items()):
            raise error("Saved category does not match the addressed intent.")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        raise error(
            "Category change was not confirmed; it may have been saved. No automatic retry. Inspect category list and history; "
            "do not rerun the write as a retry. Retain budgetId "
            + identifier
            + " and the exact original intent: "
            + json.dumps(intent)
            + "."
        ) from failure
    return {
        **verified,
        "budget": args.target,
        "data": actual,
        "requestId": intent["request_id"],
        "expectedRevision": observed["revision"],
    }


def execute_delete(connection, identifier, prefix, args, exchange, error, observed, read):
    parent = args.category_action == 'group'
    rows = observed['data'] if parent else [row for group in observed['data'] for row in group['children']]
    selected = resolve_category(rows, args.category, error, parent=parent)
    if parent and selected['children']:
        raise error('Cannot delete category group: it still has children. Inspect category list; delete or move each child explicitly first.')
    expected = ([row for row in observed['data'] if row['id'] != selected['id']] if parent else
                [{**group, 'children': [row for row in group['children'] if row['id'] != selected['id']]}
                 for group in observed['data']])
    intent = {'operation': 'delete_category_group' if parent else 'delete_category', 'workspace': 'personal',
              'payload': {'parentId' if parent else 'categoryId': selected['id']},
              'expected_revision': observed['revision'], 'request_id': str(uuid4())}
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=intent), identifier, error)
        verified = read()
        if saved['data'] is not None or verified['revision'] != saved['revision'] or verified['data'] != expected:
            raise error('Saved category deletion does not match the addressed hierarchy and revision.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        if getattr(failure, 'status_code', None) in {400, 409}:
            raise error('Category deletion refused; no deletion was saved. ' + str(failure)
                        + '. Inspect category show for child blockers or category list for group children. '
                        'No automatic retry. Retain the original intent: ' + json.dumps(intent) + '.') from failure
        raise error('Category deletion was not confirmed; it may have been saved. No automatic retry. '
                    + ('A parent group must have no children. ' if parent else
                       'A child must have no live/deleted transaction references or nonzero monthly assignments. '
                       'Zero-only assignment rows are removed atomically with the child and retained in history. ')
                    + (str(failure) + '. ' if getattr(failure, 'status_code', None) else '') +
                    'Inspect category list and history; do not rerun the write as a retry. Retain budgetId '
                    + identifier + ' and the exact original intent: ' + json.dumps(intent) + '.') from failure
    return {**verified, 'budget': args.target, 'data': {**selected, 'deleted': True},
            'requestId': intent['request_id'], 'expectedRevision': observed['revision']}


def category_table(result):
    data = result["data"]
    if result.get("dryRun"):
        return "Read-only category classification preview (no commit/save):\n" + json.dumps(result, ensure_ascii=True, indent=2)
    groups = data if isinstance(data, list) else [data]
    cells = [["Kind", "Name / path", "ID", "Currency", "Target"]]
    for row in groups:
        if "children" in row:
            cells.append(["group", row["name"], row["id"], "—", "—"])
            leaves = row["children"]
        else:
            leaves = [row]
        cells.extend(
            [
                child.get("kind", "expense"),
                f"{child['group']} / {child['name']}",
                child["id"],
                child["currency"],
                child["target"],
            ]
            for child in leaves
        )
    cells = [
        [terminal_text(value, limit) for value, limit in zip(row, (8, 48, 80, 8, 18), strict=True)]
        for row in cells
    ]
    widths = [max(len(row[index]) for row in cells) for index in range(5)]
    return "\n".join(
        [
            f"Budget: {terminal_text(result['budget'], 100)} ({terminal_text(result['budgetId'], 200)})",
            *(["Deleted and verified absent; other groups/children are unchanged."] if isinstance(data, dict) and data.get('deleted') else []),
            *(["Deletion inspection: " + json.dumps(data['deletion'], ensure_ascii=True)]
              if isinstance(data, dict) and 'deletion' in data else []),
            *[
                "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))
                for row in cells
            ],
            "Targets are integer currency minor units. Groups cannot receive assignments. JSON retains complete fields.",
        ]
    )
