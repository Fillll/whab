"""Ordinary atomic bulk workflows through guarded public operations only."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

lazy import httpx

from whab.plugins import StoreError, validate_bulk_edits, validate_category_agent

from .account_commands import resolve_account
from .bulk_help import BULK_HELP
from .transaction_commands import category_id, checked


def bulk_options(parser, *, attribution=True, formatting=True, recovery=True):
    parser.add_argument('--atomic', action='store_true', help='Use the bulk operation/result even for a single target; multi-target edits are always atomic')
    parser.add_argument('--dry-run', action='store_true', help='Read-only core-validated preview of every exact before/after record; no commit or save')
    if formatting:
        parser.add_argument('--format', choices=('json', 'table'), default='json', help='Complete JSON (default) or readable before/after differences')
    if attribution:
        parser.add_argument('--agent', type=validate_category_agent, help='Audit actor and default category attribution for explicit category edits')
        parser.add_argument('--batch', help='History grouping only; the bulk operation itself is one commit')
    if recovery:
        parser.add_argument('--revision', help='Observed decision/preview revision; automatic when omitted. Retain the original for an exact retry')
        parser.add_argument('--request-id', help='Original intent ID for exact recovery; a fresh UUID is generated only when omitted')


def add_edit_many(commands):
    edit = commands.add_parser('edit-many', help='Atomically edit 1–100 transaction annotations from JSON or stdin',
        description='Apply different category, agent, review and memo changes in ONE native commit and durable save.',
        epilog=BULK_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    edit.add_argument('--file', required=True, help='UTF-8 JSON file containing {"edits": [...]}; - reads stdin; max 2 MiB')
    bulk_options(edit)


def add_review_parsers(commands):
    for name in ("review", "unreview"):
        edit = commands.add_parser(name, help="Atomically mark 1–100 categorized transactions reviewed" if name == "review" else "Atomically mark 1–100 transactions non-reviewed",
            description="Reviewed requires a valid category (including System / Transfer or System / Income). No category always means non-reviewed. Categorizing alone never marks reviewed; imported clearing is not human-review provenance.",
            epilog="Examples:\n  whab \"My budget\" trnx review TX1 TX2 --agent Codex --dry-run\n  whab \"My budget\" trnx review TX1 TX2 --agent Codex\n  whab \"My budget\" trnx unreview TX1\n  whab \"My budget\" trnx list --since 2026-11-01\n\nAll targets validate before one native commit and durable save. JSON includes verified before/after records, budget, revision and original intent. No financial fields or category attribution change. --agent labels audit provenance, not a human identity. Invalid/missing/uncategorized targets reject the entire review batch. Preserve the exact reported intent after an uncertain response; inspect transactions/history before deliberately retrying with the original request ID and revision. Never retry with a new intent automatically.\n\n" + BULK_HELP,
            formatter_class=argparse.RawDescriptionHelpFormatter)
        edit.add_argument("transaction_id", nargs="+", help="1–100 distinct stable transaction IDs from trnx list; never fuzzy matched")
        bulk_options(edit)


def read_edits(filename, error):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field: ' + key)
            result[key] = value
        return result
    try:
        if filename == '-':
            raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        else:
            with Path(filename).expanduser().open('rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('JSON input exceeds 2 MiB; batches are never split.')
        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict or value.keys() != {'edits'} or type(value['edits']) is not list:
            raise ValueError('Expected exactly {"edits": [...]} with 1–100 items.')
        return value['edits']
    except (ValueError, OSError) as failure:
        raise error('Cannot read bulk edits: ' + str(failure)) from None


def resolve_categories(connection, identifier, prefix, edits, args, exchange, error):
    result = checked(exchange(connection, 'GET', prefix + '/queries/categories', params={'workspace': 'personal'}), identifier, error)
    if args.filter in {'review', 'unreview'} and not any(row.get('systemKind') for row in result['data']):
        raise error('Update the budget host/storage to WHAB 2.73 system-classification support before using review commands.')
    resolved = []
    for index, row in enumerate(edits, 1):
        if type(row) is not dict:
            raise error(f'Item {index}: expected an edit object.')
        item = dict(row)
        if 'category' in item:
            if 'categoryId' in item or type(item['category']) is not str:
                raise error(f'Item {index}: choose category (name/path/ID) OR categoryId (ID/null).')
            item['categoryId'] = category_id(result['data'], item.pop('category'), error)
        if args.agent is not None and 'categoryId' in item:
            item.setdefault('categoryAgent', args.agent)
        resolved.append(item)
    return result['revision'], resolved


def execute_bulk(connection, identifier, prefix, args, exchange, error, operation, edits, revision):
    try:
        edits = validate_bulk_edits(operation, edits)
    except StoreError as failure:
        raise error(str(failure)) from None
    recovering = args.revision is not None and args.revision != revision and not args.dry_run
    preview = None if recovering else checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True, json={
        'operation': 'preview_bulk_edits', 'workspace': 'personal',
        'parameters': {'operation': operation, 'edits': edits},
    }), identifier, error)
    if preview is not None and preview['revision'] != revision:
        raise error('Budget changed during name resolution. Read and reconsider; no items were changed.')
    if args.dry_run:
        if args.revision is not None and args.revision != revision:
            raise error('Preview revision is stale; no items were changed.')
        return {**preview, 'budget': args.target, 'dryRun': True, 'operation': operation, 'edits': edits}
    history = {'actor': args.agent, 'actorKind': 'agent'} if args.agent is not None else {}
    if args.batch is not None:
        history['batchId'] = args.batch
    intent = {'operation': operation, 'workspace': 'personal', 'payload': {'edits': edits},
              'expected_revision': revision if args.revision is None else args.revision,
              'request_id': str(uuid4()) if args.request_id is None else args.request_id,
              **({'history': history} if history else {})}
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=intent), identifier, error)
        replay = intent['expected_revision'] != revision
        if not replay and saved['data'] != preview['data']:
            raise error('Committed result does not match the exact validated preview.')
        if not isinstance(saved['data'], dict) or saved['data'].get('count') != len(edits) or [row['id'] for row in saved['data']['changes']] != [row['transactionId' if operation == 'edit_transactions' else 'accountId'] for row in edits]:
            raise error('Invalid saved batch receipt.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        refused = getattr(failure, 'status_code', None) in {400, 409}
        raise error(('Atomic bulk edit refused; no new items were saved. ' + str(failure) + '. ' if refused else 'Atomic bulk result was not confirmed; it may have been saved. ')
                    + 'No automatic retry. Inspect records/history. Retain budgetId ' + identifier + ' and the exact original intent: ' + json.dumps(intent) + '.') from failure
    return {**saved, 'budget': args.target, 'dryRun': False, 'replayed': replay, 'requestId': intent['request_id'],
            'expectedRevision': intent['expected_revision'], 'intent': intent}


def execute_transactions(connection, identifier, prefix, args, exchange, error):
    if args.filter in {'review', 'unreview'}:
        edits = [{'transactionId': value, 'reviewed': args.filter == 'review'} for value in args.transaction_id]
    elif args.filter == 'edit-many':
        edits = read_edits(args.file, error)
    else:
        edits = [{'transactionId': value, 'categoryId': None} if args.clear else {'transactionId': value, 'category': args.category} for value in args.transaction_id]
        if args.needs_review:
            edits = [{**row, 'reviewed': False} for row in edits]
    revision, edits = resolve_categories(connection, identifier, prefix, edits, args, exchange, error)
    return execute_bulk(connection, identifier, prefix, args, exchange, error, 'edit_transactions', edits, revision)


def execute_accounts(connection, identifier, prefix, args, exchange, error):
    observed = checked(exchange(connection, 'GET', prefix + '/queries/accounts', params={'workspace': 'personal'}), identifier, error)
    edits = []
    for value in args.account:
        row = resolve_account(observed['data'], value, error)
        edit = {'accountId': row['id'], 'type': args.type}
        if args.subtype is not None:
            edit['subtype'] = args.subtype
        if args.on_budget is not None:
            edit['onBudget'] = args.on_budget
        edits.append(edit)
    return execute_bulk(connection, identifier, prefix, args, exchange, error, 'classify_accounts', edits, observed['revision'])


def bulk_table(result):
    lines = ["Budget: " + json.dumps(result["budget"], ensure_ascii=True) + " (" + json.dumps(result["budgetId"], ensure_ascii=True) + ")", 'Read-only preview — no save' if result['dryRun'] else 'Original saved batch receipt (exact replay; current records may differ)' if result['replayed'] else 'Saved atomic batch',
             f"{result['data']['count']} targets; {result['data']['changed']} changed"]
    for row in result['data']['changes']:
        lines.append(json.dumps(row['id'], ensure_ascii=True))
        keys = sorted(set(row['before']) | set(row['after']))
        differences = [key for key in keys if row['before'].get(key) != row['after'].get(key)]
        for key in differences:
            lines.append('  ' + key + ': ' + json.dumps(row['before'].get(key), ensure_ascii=True) + ' -> ' + json.dumps(row['after'].get(key), ensure_ascii=True))
        if not differences:
            lines.append('  unchanged')
    lines.append('Revision: ' + result['revision'])
    if not result['dryRun']:
        lines.append('Request ID: ' + json.dumps(result['requestId'], ensure_ascii=True))
        lines.append('Original expected revision: ' + json.dumps(result['expectedRevision'], ensure_ascii=True))
    return '\n'.join(lines)
