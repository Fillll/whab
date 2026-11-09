"""Name-addressed account classification over guarded public ledger operations."""

import argparse
import json
from uuid import uuid4

import httpx

from whab.plugins import ACCOUNT_SUBTYPES, ACCOUNT_TYPES, StoreError, normalize_account
from whab.plugins.documentation import operation

from .transaction_commands import checked
from .transaction_reads import terminal_text


def add_account_parser(commands, catalog):
    types = operation(catalog['core'], 'command', 'update_account')['input']['properties']['type']['enum']
    guide = '\n'.join(f'  {kind}: {ACCOUNT_TYPES[kind]}; subtypes: {", ".join(ACCOUNT_SUBTYPES.get(kind, {})) or "none"}' for kind in types)
    examples = ('Examples (fictional budget):\n'
                '  whab "My budget" account list --unclassified --format table\n'
                '  whab "My budget" account show "🍏Example Card"\n'
                '  whab "My budget" account classify "🍏Example Card" --type credit --subtype credit_card --on-budget\n'
                '  whab "My budget" account classify ACCOUNT_ID --type investment --off-budget\n\n')
    account = commands.add_parser('account', help='List, inspect and classify accounts by name or ID',
        description='Read and classify accounts in one explicitly addressed budget; no interactive wizard.',
        epilog=examples + 'Names match exactly, ignoring case and outer whitespace; IDs take precedence. Missing or ambiguous names fail with candidates. Quote spaces, emoji and non-Latin names. JSON is the default; --format table is for display. No bank synchronization is requested.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = account.add_subparsers(dest='account_action', required=True)
    listing = actions.add_parser('list', help='List account identities and classifications',
        description='List this budget’s accounts, including name, stable ID, type, subtype, onBudget and unclassified (type=other).',
        epilog=examples + 'Reads only; --unclassified selects type=other, independently of balance verification. Imported balance estimates stay unverified.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    listing.add_argument('--unclassified', action='store_true', help='Only accounts whose type is other (generic/unclassified)')
    show = actions.add_parser('show', help='Read one account by unique name or stable ID',
        description='Read one saved account without changing it. Names ignore case and outer whitespace; no fuzzy matching.',
        epilog=examples + 'A missing or ambiguous name returns candidate names and IDs. Use the stable ID to disambiguate. Imported balances remain unverified.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    edit = actions.add_parser('classify', help='Save an explicit account classification without JSON or revision arguments',
        description='Classify one existing account using the public guarded update_account operation. Reads its current revision and generates one unique request ID automatically.',
        epilog=examples + 'Types and permitted subtypes:\n' + guide + '\n\nOnly depository and credit support --on-budget. Scope omitted: preserve the existing scope, even when changing to credit. An incompatible preserved scope is rejected; choose --off-budget explicitly to move a cash/card account to a tracking type. Subtype omitted: preserve it for the same type, clear it when type changes.\n\nClassification applies across account history without rewriting transactions, notes, categories, reviews or bank mappings. Imported balance estimates remain unverified. No bank sync is requested. Success includes the saved classification after verification. Stale revisions fail safely; no automatic retries. After an uncertain response, inspect account show and history before deciding anything else. Keep the reported original intent/revision/payload for reconciliation or an exact advanced retry; do not rerun classify to retry an uncertain write.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    edit.add_argument('--type', required=True, choices=types, help='Financial classification; other means unclassified, credit means card/line of credit')
    edit.add_argument('--subtype', help='Optional subtype from the type-specific list below; invalid combinations are refused')
    scope = edit.add_mutually_exclusive_group()
    scope.add_argument('--on-budget', dest='on_budget', action='store_const', const=True, default=None, help='Include in monthly envelopes; only depository/credit support this')
    scope.add_argument('--off-budget', dest='on_budget', action='store_const', const=False, help='Track outside monthly envelopes')
    for parser in (show, edit):
        parser.add_argument('account', help='Exact stable ID or unique account name; quote spaces/emoji/non-Latin text')
    for parser in (listing, show, edit):
        parser.add_argument('--format', choices=('json', 'table'), default='json', help='Output format (default: json); JSON retains complete public fields')


def resolve_account(rows, value, error):
    matches = [row for row in rows if row['id'] == value] or [row for row in rows if row['name'].strip().casefold() == value.strip().casefold()]
    if len(matches) != 1:
        candidates = [{'id': row['id'], 'name': row['name']} for row in (matches or rows)[:20]]
        raise error('Unknown or ambiguous account name. Candidates (up to 20): ' + json.dumps(candidates, ensure_ascii=True) + '. Use account list and an exact stable ID; no fuzzy match was selected.')
    return matches[0]


def classified(row):
    return {**row, 'unclassified': row['type'] == 'other'}


def execute_account(connection, identifier, prefix, args, exchange, error):
    def read():
        return checked(exchange(connection, 'GET', prefix + '/queries/accounts', params={'workspace': 'personal'}), identifier, error)
    observed = read()
    if args.account_action == 'list':
        return {**observed, 'budget': args.target, 'data': [classified(row) for row in observed['data'] if not args.unclassified or row['type'] == 'other']}
    row = resolve_account(observed['data'], args.account, error)
    if args.account_action == 'show':
        return {**observed, 'budget': args.target, 'data': classified(row)}
    # Send explicit scope even when omitted: core type changes may otherwise move
    # an on-budget account off budget. The SDK owns combination validation.
    scope = row['onBudget'] if args.on_budget is None else args.on_budget
    subtype = args.subtype if args.subtype is not None else row.get('subtype') if args.type == row['type'] else None
    try:
        normalize_account({'type': args.type, 'onBudget': scope, 'subtype': subtype})
    except StoreError as failure:
        raise error(str(failure)) from failure
    payload = {'accountId': row['id'], 'type': args.type, 'onBudget': scope, 'subtype': subtype}
    intent = {'operation': 'update_account', 'workspace': 'personal', 'payload': payload,
              'expected_revision': observed['revision'], 'request_id': str(uuid4())}
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', json=intent), identifier, error)
        verified = read()
        actual = resolve_account(verified['data'], row['id'], error)
        if verified['revision'] != saved['revision'] or any(actual.get(key) != payload[key] for key in ('type', 'onBudget', 'subtype')):
            raise error('Account changed during verification.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        raise error('Classification was not confirmed; it may have been saved. No automatic retry. Inspect account show and history; do not rerun classify as a retry. Retain budgetId ' + identifier + ' and the exact original intent: ' + json.dumps(intent) + '.') from failure
    return {**verified, 'budget': args.target, 'data': classified(actual),
            'requestId': intent['request_id'], 'expectedRevision': observed['revision']}


def account_table(result):
    data = result['data']
    rows = data if isinstance(data, list) else [data]
    lines = [f"Budget: {terminal_text(result['budget'], 100)} ({terminal_text(result['budgetId'], 200)})"]
    cells = [['ID', 'Name', 'Type', 'Subtype', 'Scope', 'Unclassified']]
    cells += [[row['id'], row['name'], row['type'], row.get('subtype') or '—',
               'on-budget' if row['onBudget'] else 'off-budget', 'yes' if row['unclassified'] else 'no'] for row in rows]
    cells = [[terminal_text(value, limit) for value, limit in zip(row, (40, 36, 18, 22, 10, 12), strict=True)] for row in cells]
    widths = [max(len(row[index]) for row in cells) for index in range(6)]
    lines += ['  '.join(value.ljust(width) for value, width in zip(row, widths, strict=True)) for row in cells]
    if not rows:
        lines.append('No matching accounts.')
    lines.append('Classification does not verify imported balance estimates. JSON retains complete public fields.')
    return '\n'.join(lines)
