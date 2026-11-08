"""Convenient category edits over the existing guarded public ledger command."""

import argparse
from uuid import uuid4

import httpx

from whab.plugins import validate_category_agent


def add_category_parsers(commands):
    commands.add_parser('categories', help='List category IDs, names and groups for this budget')
    edit = commands.add_parser('categorize', aliases=['categorise'],
        help='Assign a category without JSON or advanced command syntax',
        description='Assign a category to one transaction in this budget. Review status is preserved unless --needs-review is supplied. Agent attribution is opt-in.',
        epilog='Examples:\n  whab "My budget" trnx categories\n  whab "My budget" trnx categorize TRANSACTION_ID --category "Food / Groceries" --agent Codex --needs-review\n  whab "My budget" trnx categorize TRANSACTION_ID --clear\n\nCategory accepts an exact ID, unique name or "Group / Name" (case-insensitive names). Ambiguous names fail. A fresh ledger revision and unique request ID are obtained automatically; supply --revision and --request-id for an explicitly observed intent. Never automatically retries. After an uncertain result, inspect the transaction; retain the reported intent IDs and original arguments.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    edit.add_argument('transaction_id', help='Stable transaction ID returned by a read')
    choice = edit.add_mutually_exclusive_group(required=True)
    choice.add_argument('--category', help='Category ID, unique name or Group / Name')
    choice.add_argument('--clear', action='store_true', help='Clear the assigned category')
    edit.add_argument('--agent', type=validate_category_agent, help='Explicit self-declared agent label; omit for manual attribution')
    edit.add_argument('--needs-review', action='store_true', help='Set reviewed=false with this category edit')
    edit.add_argument('--revision', help='Use this observed revision instead of the fresh category-read revision')
    edit.add_argument('--request-id', help='Explicit unique intent ID; otherwise generated once and returned')


def checked(result, identifier, error):
    if not isinstance(result, dict) or result.get('budgetId') != identifier or not result.get('revision') or 'data' not in result:
        raise error('WHAB returned another budget or an invalid response.')
    return result


def category_id(rows, value, error):
    exact = [row for row in rows if row['id'] == value]
    names = [row for row in rows if row['name'].strip().casefold() == value.strip().casefold()
             or f"{row['group'].strip()} / {row['name'].strip()}".casefold() == value.strip().casefold()]
    matches = exact or names
    if len(matches) != 1:
        raise error('Unknown or ambiguous category. Use trnx categories and choose an exact ID or Group / Name.')
    return matches[0]['id']


def execute_category(connection, identifier, prefix, args, exchange, error):
    categories = checked(exchange(connection, 'GET', prefix + '/queries/categories', params={'workspace': 'personal'}), identifier, error)
    if args.filter == 'categories':
        return categories
    selected = None if args.clear else category_id(categories['data'], args.category, error)
    payload = {'transactionId': args.transaction_id, 'categoryId': selected}
    if args.agent is not None:
        payload['categoryAgent'] = args.agent
    if args.needs_review:
        payload['reviewed'] = False
    revision = args.revision or categories['revision']
    intent = args.request_id or str(uuid4())
    try:
        result = checked(exchange(connection, 'POST', prefix + '/commands',
            json={'operation': 'edit_transaction', 'workspace': 'personal', 'payload': payload, 'expected_revision': revision, 'request_id': intent}), identifier, error)
    except (ValueError, OSError, RuntimeError, httpx.HTTPError) as failure:
        raise error(f'Categorization was not confirmed; inspect the transaction before retrying. No automatic retry. Retain original arguments, --revision {revision} and --request-id {intent}.') from failure
    return {**result, 'transactionId': args.transaction_id, 'categoryId': selected,
            'expectedRevision': revision, 'requestId': intent}
