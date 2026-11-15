"""Name-addressed transaction corrections and creations through one public operation."""

import argparse
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

lazy import httpx

from whab.plugins import StoreError

from .account_commands import resolve_account
from .bulk_commands import bulk_options, resolve_categories
from .transaction_change_help import CHANGE_HELP
from .transaction_commands import checked


def add_change_parsers(commands):
    for name in ('edit', 'create'):
        create = name == 'create'
        parser = commands.add_parser(name,
            help='Create one manual, unreviewed transaction' if create else 'Correct one transaction while preserving omitted fields',
            description='Validate and preview exact transaction changes, then save through one guarded atomic public operation.',
            epilog=CHANGE_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
        if not create:
            parser.add_argument('transaction_id', help='Existing stable transaction ID from trnx list; names are not transaction identities')
        parser.add_argument('--account', required=create, help='Existing account ID or unique exact account name; quote spaces/Unicode')
        parser.add_argument('--date', required=create, help='YYYY-MM-DD; inclusive supported range 1900-01-01 through 2200-12-31')
        parser.add_argument('--amount', required=create, type=int, help='Signed integer minor units: positive outflow, negative inflow; e.g. USD -1250 = $12.50 inflow')
        if create:
            parser.add_argument('--currency', required=True, help='Explicit account currency code, e.g. USD; must match the account; no conversion')
        parser.add_argument('--payee', '--merchant', dest='merchant', required=create, help='Payee/merchant text; existing text is preserved when omitted from edit')
        category = parser.add_mutually_exclusive_group()
        category.add_argument('--category', help='Existing category ID, unique exact name, or Group / Category')
        if not create:
            category.add_argument('--clear-category', action='store_true', help='Explicitly remove the category and leave the transaction unreviewed')
        parser.add_argument('--note', '--memo', dest='note', help='Replace memo verbatim; an empty string clears it; omitted edit memo is preserved')
        bulk_options(parser)


def _items(args):
    item = {key: getattr(args, key) for key in ('account', 'date', 'merchant', 'amount', 'category', 'note', 'currency')
            if getattr(args, key, None) is not None}
    if args.filter == 'create':
        return [], [item]
    if args.clear_category:
        item['categoryId'] = None
    return [{'transactionId': args.transaction_id, **item}], []


def execute_changes(connection, identifier, prefix, args, exchange, error, *, edits=None, creates=None, preserve_balances=False):
    from whab.plugins import validate_transaction_changes
    if edits is None:
        edits, creates = _items(args)
    intent_id = str(uuid4()) if args.request_id is None else args.request_id
    combined = [*edits, *creates]
    revision, resolved = resolve_categories(connection, identifier, prefix, combined, args, exchange, error)
    accounts = checked(exchange(connection, 'GET', prefix + '/queries/accounts', params={'workspace': 'personal'}), identifier, error)
    if accounts['revision'] != revision:
        raise error('Budget changed during account/category name resolution. Read and reconsider; nothing was changed.')
    for index, item in enumerate(resolved, 1):
        if 'account' in item:
            if 'accountId' in item or type(item['account']) is not str:
                raise error(f'Item {index}: choose account (name/ID) OR accountId (stable ID).')
            item['accountId'] = resolve_account(accounts['data'], item.pop('account'), error)['id']
    edits, creates = resolved[:len(edits)], resolved[len(edits):]
    creates = [{**row, 'creationId': row.get('creationId', str(uuid5(NAMESPACE_URL, f'whab-transaction:{intent_id}:{index}')))}
               for index, row in enumerate(creates)]
    try:
        edits, creates = validate_transaction_changes(edits, creates, preserve_balances=preserve_balances)
    except StoreError as failure:
        raise error(str(failure)) from None
    history = {'actor': args.agent, 'actorKind': 'agent'} if args.agent is not None else {}
    if args.batch is not None:
        history['batchId'] = args.batch
    intent = {'operation': 'change_transactions', 'workspace': 'personal', 'payload': {'edits': edits, 'creates': creates,
              **({'preserveBalances': True} if preserve_balances else {})},
              'expected_revision': revision if args.revision is None else args.revision,
              'request_id': intent_id, **({'history': history} if history else {})}
    recovering = args.revision is not None and args.revision != revision and not args.dry_run
    preview = None if recovering else checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True, json={
        'operation': 'preview_transaction_changes', 'workspace': 'personal', 'parameters': intent['payload'],
    }), identifier, error)
    if preview is not None and preview['revision'] != revision:
        raise error('Budget changed during transaction preview. Read and reconsider; nothing was changed.')
    metadata = {'budget': args.target, 'dryRun': args.dry_run, 'operation': 'change_transactions',
                'requestId': intent_id, 'expectedRevision': intent['expected_revision'], 'intent': intent}
    if args.dry_run:
        if args.revision is not None and args.revision != revision:
            raise error('Preview revision is stale; nothing was changed.')
        return {**preview, **metadata}
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=intent), identifier, error)
        if not recovering and saved['data'] != preview['data']:
            raise error('Saved result differs from the exact validated preview.')
        expected_ids = [row['transactionId'] for row in edits] + [row['creationId'] for row in creates]
        data = saved['data']
        if (type(data) is not dict or data.get('count') != len(expected_ids)
                or [row['id'] for row in data['changes']] != expected_ids
                or data.get('createdIds') != [row['creationId'] for row in creates]):
            raise error('Invalid saved transaction-change receipt.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        refused = getattr(failure, 'status_code', None) in {400, 409}
        raise error(('Atomic transaction change refused; nothing new was saved. ' + str(failure) + '. ' if refused
                     else 'Atomic transaction result was not confirmed; it may have been saved. ')
                    + 'No automatic retry. Inspect trnx list and history. Retain budgetId ' + identifier
                    + ' and the exact original intent: ' + json.dumps(intent) + '.') from failure
    return {**saved, **metadata, 'replayed': recovering}
