"""Name-addressed permanent history and previewed undo/redo/restore."""

import argparse
from uuid import uuid4

import httpx

from whab.plugins import validate_category_agent

from .transaction_commands import checked


def add_history_parser(commands):
    history = commands.add_parser('history', help='Inspect permanent financial history and preview undo/redo')
    actions = history.add_subparsers(dest='history_action', required=True)
    listing = actions.add_parser('list', help='List recent changes, actors, batches and snapshots')
    listing.add_argument('--limit', type=int, default=50)
    listing.add_argument('--before', help='Entry ID from the next-page cursor')
    for action in ('show', 'export'):
        parser = actions.add_parser(action, help='Inspect an entry/batch' if action == 'show' else 'Print the financial snapshot at an entry, or baseline')
        parser.add_argument('history_target')
    for action in ('undo', 'redo', 'restore'):
        parser = actions.add_parser(action, help='Preview changes; commit only with the reviewed fingerprint',
            epilog=f'whab "My budget" history {action} TARGET\nReview the changes, then repeat with --apply FINGERPRINT. Later conflicts refuse the complete action; no automatic retry.', formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument('history_target', help='Entry/batch ID, or baseline for restore')
        parser.add_argument('--apply', metavar='FINGERPRINT', help='Exact fingerprint from the reviewed preview')
        parser.add_argument('--agent', type=validate_category_agent, help='Explicit self-declared agent label')
        parser.add_argument('--request-id', help='Optional original intent ID for an exact retry')
        parser.add_argument('--revision', help='Original preview revision for an exact retry')
    snapshot = actions.add_parser('snapshot', help='Create a durable financial checkpoint')
    snapshot.add_argument('--apply', metavar='FINGERPRINT', help='Original fingerprint for an exact retry')
    snapshot.add_argument('--agent', type=validate_category_agent)
    snapshot.add_argument('--request-id')
    snapshot.add_argument('--revision')


def execute_history(connection, identifier, prefix, args, exchange, error):
    def query(operation, parameters):
        return checked(exchange(connection, 'POST', prefix + '/queries', json={
            'operation': operation, 'workspace': 'personal', 'parameters': parameters}), identifier, error)
    action = args.history_action
    if action == 'list':
        return query('history', {'limit': args.limit, **({'before': args.before} if args.before else {})})
    if action in {'show', 'export'}:
        return query('history', {'target': args.history_target, 'snapshot': action == 'export'})
    target = None if action == 'snapshot' else args.history_target
    # Exact retries must retain the original reviewed fingerprint and revision.
    if args.apply and args.revision:
        fingerprint, revision = args.apply, args.revision
    else:
        preview = query('history_preview', {'action': action, 'target': target})
        if action != 'snapshot' and args.apply is None:
            return preview
        fingerprint, revision = preview['data']['fingerprint'], preview['revision']
        if action != 'snapshot' and args.apply != fingerprint:
            raise error('Budget changed since the reviewed preview. Preview again before applying.')
    intent = args.request_id or str(uuid4())
    body = {'operation': 'history_apply', 'workspace': 'personal',
            'payload': {'action': action, 'target': target, 'fingerprint': fingerprint},
            'expected_revision': revision, 'request_id': intent}
    if args.agent:
        body['history'] = {'actor': args.agent, 'actorKind': 'agent'}
    try:
        result = checked(exchange(connection, 'POST', prefix + '/commands', json=body), identifier, error)
    except (ValueError, OSError, RuntimeError, httpx.HTTPError) as failure:
        raise error(f'History action was not confirmed. Inspect history before retrying; retain --revision {revision} and --request-id {intent} with the original --apply fingerprint. No automatic replay.') from failure
    return {**result, 'requestId': intent, 'expectedRevision': revision, 'fingerprint': fingerprint}
