"""Create an empty manual register through the guarded public ledger contract."""

import argparse
import json

lazy import httpx

from whab.plugins import StoreError, normalize_manual_account

from .account_commands import classified
from .account_help import CREATE_HELP
from .record_edits import edit_options, intent
from .transaction_commands import checked


def add_create_parser(actions, types, guide):
    parser = actions.add_parser('create', help='Create an empty manual account with an unknown opening balance',
        description='Create one manual register by name; no opening transaction, verified balance or bank connection is invented.',
        epilog=CREATE_HELP + '\nTypes and permitted subtypes:\n' + guide,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', help='New account name (1–100 characters); unique ignoring case and outer whitespace')
    parser.add_argument('--type', required=True, choices=types, help='Required financial type; savings uses depository, not deposit')
    parser.add_argument('--subtype', help='Optional valid subtype for this type; depository/savings, deposit/cd, credit/credit_card')
    parser.add_argument('--currency', required=True, help='Explicit supported uppercase ISO currency code, for example USD; no currency conversion')
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--on-budget', dest='on_budget', action='store_const', const=True, help='Include eligible cash/card activity in monthly budgeting; only depository/credit')
    scope.add_argument('--off-budget', dest='on_budget', action='store_const', const=False, help='Track this account outside monthly envelopes')
    parser.add_argument('--dry-run', action='store_true', help='Read-only shared validation and exact account/intent preview; does not reserve name/ID')
    edit_options(parser)


def execute_create(connection, identifier, prefix, args, exchange, error):
    if (args.revision is None) != (args.request_id is None):
        raise error('Exact recovery requires both --revision and --request-id with the original values; otherwise omit both.')

    def read():
        return checked(exchange(connection, 'GET', prefix + '/queries/accounts', params={'workspace': 'personal'}), identifier, error)

    observed = read()
    if args.dry_run and args.revision is not None and args.revision != observed['revision']:
        raise error('The preview revision is stale. Read current accounts for a new preview; retain the original intent for exact recovery.')
    payload = {'name': args.name, 'type': args.type, 'balance': 0, 'balanceUnknown': True,
               'uniqueName': True, 'currency': args.currency, 'onBudget': args.on_budget,
               **({'subtype': args.subtype} if args.subtype is not None else {})}
    try:
        # An exact recovery must reach the durable receipt before duplicate-name checks.
        normalized = normalize_manual_account(payload, observed['data'] if args.request_id is None or args.dry_run else ())
    except StoreError as failure:
        raise error(str(failure)) from failure
    original = intent(args, observed, 'add_account', normalized)
    proposal = classified({key: value for key, value in normalized.items() if key != 'uniqueName'}
                          | {'id': None, 'source': 'manual', 'institution': 'Manual account', 'stale': False, 'recordedActivity': 0})
    metadata = {'budget': args.target, 'requestId': original['request_id'],
                'expectedRevision': original['expected_revision'], 'intent': original, 'dryRun': args.dry_run}
    if args.dry_run:
        return {**observed, **metadata, 'data': proposal}
    replay = args.revision is not None and args.revision != observed['revision']
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=original), identifier, error)
        verified = read()
        actual = next((row for row in verified['data'] if row['id'] == saved['data']), None)
        expected_revision = observed['revision'] if replay else saved['revision']
        if type(saved['data']) is not str or not saved['data'] or actual is None or verified['revision'] != expected_revision:
            raise error('The created account ID and saved revision could not be verified.')
        actual = classified(actual)
        if not replay and any(actual.get(key) != value for key, value in proposal.items() if key != 'id'):
            raise error('The saved account differs from the requested empty manual register.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        refused = getattr(failure, 'status_code', None) in {400, 409}
        raise error('Account creation was not confirmed; it may have been saved. '
                    + (str(failure) + '. ' if refused else '')
                    + 'No automatic retry. Inspect account list/show and history. Retain budgetId ' + identifier
                    + ' and the exact original intent: ' + json.dumps(original) + '.') from failure
    return {**verified, **metadata, 'data': classified(actual), 'savedRevision': saved['revision'], 'replayed': replay}
