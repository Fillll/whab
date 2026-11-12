"""Name-addressed account classification over guarded public ledger operations."""

import argparse
import json
from uuid import uuid4

lazy import httpx

from whab.plugins import ACCOUNT_SUBTYPES, ACCOUNT_TYPES, StoreError, normalize_account
from whab.plugins.documentation import operation

from .transaction_commands import checked
from .transaction_reads import terminal_text


def add_account_parser(commands, catalog):
    from .bulk_commands import bulk_options
    from .bulk_help import BULK_HELP
    types = operation(catalog['core'], 'command', 'update_account')['input']['properties']['type']['enum']
    guide = '\n'.join(f'  {kind}: {ACCOUNT_TYPES[kind]}; subtypes: {", ".join(ACCOUNT_SUBTYPES.get(kind, {})) or "none"}' for kind in types)
    examples = ('Examples (fictional budget):\n'
                '  whab "My budget" account list --unclassified --format table\n'
                '  whab "My budget" account show "🍏Example Card"\n'
                '  whab "My budget" account classify "🍏Example Card" --type credit --subtype credit_card --on-budget\n'
                '  whab "My budget" account classify ACCOUNT_ID --type investment --off-budget\n'
                '  whab "My budget" account classify "Example CD" --type deposit --subtype cd --off-budget\n'
                '  whab "My budget" account close "Example CD"\n'
                '  whab "My budget" account reopen "Example CD"\n\n')
    account = commands.add_parser('account', help='List, classify and archive accounts by name or ID',
        description='Read, classify, close or reopen accounts in one explicitly addressed budget; no interactive wizard.',
        epilog=examples + 'Names match exactly, ignoring case and outer whitespace; IDs take precedence. Missing or ambiguous names fail with candidates. Quote spaces, emoji and non-Latin names. JSON is the default; --format table is for display. No bank synchronization is requested.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    account.epilog += '\n\n' + BULK_HELP
    actions = account.add_subparsers(dest='account_action', required=True)
    listing = actions.add_parser('list', help='List account identities and classifications',
        description='List this budget’s accounts, including name, stable ID, type, subtype, onBudget, closed lifecycle and unclassified (type=other).',
        epilog=examples + 'Reads only; --unclassified selects type=other, independently of balance verification. Imported balance estimates stay unverified.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    state = listing.add_mutually_exclusive_group()
    state.add_argument('--closed', dest='lifecycle', action='store_const', const=True, default=None, help='Only archived accounts; default lists open and closed')
    state.add_argument('--active', dest='lifecycle', action='store_const', const=False, help='Only open accounts')
    listing.add_argument('--unclassified', action='store_true', help='Only accounts whose type is other (generic/unclassified)')
    show = actions.add_parser('show', help='Read one account by unique name or stable ID',
        description='Read one saved account’s classification, budget scope and closed lifecycle without changing it. closed=true means archive, separately from financial scope. Names ignore case and outer whitespace; no fuzzy matching.',
        epilog=examples + 'A missing or ambiguous name returns candidate names and IDs. Use the stable ID to disambiguate. Imported balances remain unverified.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    edit = actions.add_parser('classify', help='Save an explicit account classification without JSON or revision arguments',
        description='Classify one existing account using the public guarded update_account operation. Reads its current revision and generates one unique request ID automatically.',
        epilog=examples + 'Types and permitted subtypes:\n' + guide + '\n\nOnly depository and credit support --on-budget. Scope omitted: preserve the existing scope, even when changing to credit. An incompatible preserved scope is rejected; choose --off-budget explicitly to move a cash/card account to a tracking type. Subtype omitted: preserve it for the same type, clear it when type changes. A certificate of deposit is --type deposit --subtype cd --off-budget. Classification preserves lifecycle state; use account close/reopen separately.\n\nClassification applies across account history without rewriting transactions, notes, categories, reviews or bank mappings. Imported balance estimates remain unverified. No bank sync is requested. Success includes the saved classification after verification. Stale revisions fail safely; no automatic retries. After an uncertain response, inspect account show and history before deciding anything else. Keep the reported original intent/revision/payload for reconciliation or an exact advanced retry; do not rerun classify to retry an uncertain write.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    edit.epilog += '\n\n' + BULK_HELP
    bulk_options(edit, formatting=False)
    edit.add_argument('--type', required=True, choices=types, help='Financial classification; other means unclassified, credit means card/line of credit')
    edit.add_argument('--subtype', help='Optional subtype from the type-specific list below; invalid combinations are refused')
    scope = edit.add_mutually_exclusive_group()
    scope.add_argument('--on-budget', dest='on_budget', action='store_const', const=True, default=None, help='Include in monthly envelopes; only depository/credit support this')
    scope.add_argument('--off-budget', dest='on_budget', action='store_const', const=False, help='Track outside monthly envelopes')
    lifecycle = []
    for action in ('close', 'reopen'):
        lifecycle.append(actions.add_parser(action, help=('Archive' if action == 'close' else 'Reopen') + ' any account type by unique name or stable ID',
            description='Save closed lifecycle state through the guarded public update_account operation, with automatic revision and request identity.',
            epilog=examples + 'Closed is independent of type/subtype and budget scope. Every account type can close or reopen, including unknown/nonzero balances. Closing preserves balances, currency, history, notes, reviews and bank mappings; it does not settle or verify funds/debts or fabricate zero. Existing financial/historical treatment follows its saved type and scope. Archived accounts are hidden from active pickers and refuse new manual entries or sync updates. Open accounts on the same bank Item keep syncing; shared connections and other budgets are unaffected. Reopening restores the saved account cursor for provider catch-up under the original mapping/history dates. Neither command starts sync. Imported balance estimates remain unverified. Each write verifies the saved result; stale or uncertain writes are never replayed automatically. Inspect account show/history and retain the reported complete intent before any retry.',
            formatter_class=argparse.RawDescriptionHelpFormatter))
    for parser in (show, edit, *lifecycle):
        parser.add_argument('account', **({'nargs': '+'} if parser is edit else {}), help='Exact stable ID or unique account name; quote spaces/emoji/non-Latin text')
    for parser in (listing, show, edit, *lifecycle):
        parser.add_argument('--format', choices=('json', 'table'), default='json', help='Output format (default: json); JSON retains complete public fields')


def resolve_account(rows, value, error):
    matches = [row for row in rows if row['id'] == value] or [row for row in rows if row['name'].strip().casefold() == value.strip().casefold()]
    if len(matches) != 1:
        candidates = [{'id': row['id'], 'name': row['name']} for row in (matches or rows)[:20]]
        raise error('Unknown or ambiguous account name. Candidates (up to 20): ' + json.dumps(candidates, ensure_ascii=True) + '. Use account list and an exact stable ID; no fuzzy match was selected.')
    return matches[0]


def classified(row):
    return {**row, 'unclassified': row['type'] == 'other', 'closed': row.get('closed', False)}


def execute_account(connection, identifier, prefix, args, exchange, error):
    def read():
        return checked(exchange(connection, 'GET', prefix + '/queries/accounts', params={'workspace': 'personal'}), identifier, error)
    observed = read()
    if args.account_action == 'list':
        return {**observed, 'budget': args.target, 'data': [classified(row) for row in observed['data'] if (not args.unclassified or row['type'] == 'other') and (args.lifecycle is None or row.get('closed', False) is args.lifecycle)]}
    row = resolve_account(observed['data'], args.account, error)
    if args.account_action == 'show':
        return {**observed, 'budget': args.target, 'data': classified(row)}
    if args.account_action in {'close', 'reopen'}:
        payload = {'accountId': row['id'], 'closed': args.account_action == 'close'}
    else:
        # Scope is explicit even when omitted, to preserve the observed choice.
        scope = row['onBudget'] if args.on_budget is None else args.on_budget
        subtype = args.subtype if args.subtype is not None else row.get('subtype') if args.type == row['type'] else None
        try:
            normalize_account({'type': args.type, 'onBudget': scope, 'subtype': subtype})
        except StoreError as failure:
            raise error(str(failure)) from failure
        payload = {'accountId': row['id'], 'type': args.type, 'onBudget': scope, 'subtype': subtype}
    expected = {**{key: row.get(key) for key in ('type', 'onBudget', 'subtype')},
                'closed': row.get('closed', False), **{key: value for key, value in payload.items() if key != 'accountId'}}
    intent = {'operation': 'update_account', 'workspace': 'personal', 'payload': payload,
              'expected_revision': observed['revision'] if getattr(args, 'revision', None) is None else args.revision, 'request_id': str(uuid4()) if getattr(args, 'request_id', None) is None else args.request_id}
    history = {'actor': args.agent, 'actorKind': 'agent'} if getattr(args, 'agent', None) is not None else {}
    if getattr(args, 'batch', None) is not None:
        history['batchId'] = args.batch
    if history:
        intent['history'] = history
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', json=intent), identifier, error)
        verified = read()
        actual = classified(resolve_account(verified['data'], row['id'], error))
        if actual['id'] != row['id'] or verified['revision'] != saved['revision'] or any(actual.get(key) != value for key, value in expected.items()):
            raise error('Account changed during verification.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        raise error('Account update was not confirmed; it may have been saved. No automatic retry. Inspect account show and history; do not rerun ' + args.account_action + ' as a retry. Retain budgetId ' + identifier + ' and the exact original intent: ' + json.dumps(intent) + '.') from failure
    return {**verified, 'budget': args.target, 'data': classified(actual),
            'requestId': intent['request_id'], 'expectedRevision': intent['expected_revision']}


def account_table(result):
    data = result['data']
    rows = data if isinstance(data, list) else [data]
    lines = [f"Budget: {terminal_text(result['budget'], 100)} ({terminal_text(result['budgetId'], 200)})"]
    cells = [['ID', 'Name', 'Type', 'Subtype', 'Scope', 'State', 'Unclassified']]
    cells += [[row['id'], row['name'], row['type'], row.get('subtype') or '—',
               'on-budget' if row['onBudget'] else 'off-budget', 'closed' if row['closed'] else 'open', 'yes' if row['unclassified'] else 'no'] for row in rows]
    cells = [[terminal_text(value, limit) for value, limit in zip(row, (40, 36, 18, 22, 10, 6, 12), strict=True)] for row in cells]
    widths = [max(len(row[index]) for row in cells) for index in range(7)]
    lines += ['  '.join(value.ljust(width) for value, width in zip(row, widths, strict=True)) for row in cells]
    if not rows:
        lines.append('No matching accounts.')
    lines.append('Classification does not verify imported balance estimates. JSON retains complete public fields.')
    return '\n'.join(lines)
