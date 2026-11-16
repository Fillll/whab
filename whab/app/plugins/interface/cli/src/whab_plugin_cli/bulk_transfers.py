"""Explicit atomic transfer batches over the public ledger transport."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

lazy import httpx

from whab.plugins import validate_category_agent

from .transaction_commands import checked
from .transfer_help import BULK_TRANSFER_HELP


def add_bulk_transfer_parser(commands):
    parser = commands.add_parser('match-transfers',
        help='Atomically match 1–100 explicit pairs, optionally reviewing both legs',
        description='Match explicit disjoint transfer pairs in one guarded commit and durable save.',
        epilog=BULK_TRANSFER_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--file', required=True, help='UTF-8 JSON array of {outId,inId}; - reads stdin; 1–100 pairs, max 2 MiB')
    parser.add_argument('--reviewed', action='store_true', help='Explicitly review both legs of every valid pair in this same commit; default leaves unreviewed')
    parser.add_argument('--dry-run', action='store_true', help='Read-only exact core preview and fingerprint; no commit/save')
    parser.add_argument('--revision', help='Observed preview revision; retain original for exact recovery')
    parser.add_argument('--fingerprint', help='Exact preview fingerprint; retain original for exact recovery')
    parser.add_argument('--request-id', help='Original intent ID for an exact retry; otherwise a new UUID is generated once')
    parser.add_argument('--agent', type=validate_category_agent, help='Self-declared audit actor; existing category attribution is preserved')
    parser.add_argument('--batch', help='History grouping only; the whole operation already commits once')
    parser.add_argument('--format', choices=('json', 'table'), default='json', help='Complete JSON (default) or all exact before/after records')


def read_pairs(filename, error):
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
        pairs = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        if type(pairs) is not list or not 1 <= len(pairs) <= 100:
            raise ValueError('Expected a JSON array of 1–100 {"outId": "...", "inId": "..."} pairs.')
        seen = set()
        for index, pair in enumerate(pairs, 1):
            if type(pair) is not dict or pair.keys() != {'outId', 'inId'}:
                raise ValueError(f'Pair {index}: supply outId and inId only.')
            for name in ('outId', 'inId'):
                value = pair[name]
                if type(value) is not str or not 1 <= len(value) <= 200 or '\x00' in value:
                    raise ValueError(f'Pair {index}: {name} must be a nonempty stable ID of at most 200 characters.')
                value.encode('utf-8')
                if value in seen:
                    raise ValueError(f'Pair {index}: repeated transaction ID {value!r}; pairs must be disjoint.')
                seen.add(value)
        return pairs
    except (ValueError, OSError) as failure:
        raise error('Cannot read transfer pairs: ' + str(failure)) from None


def valid_record(row):
    required = {'id': str, 'accountId': str, 'date': str, 'merchant': str, 'amount': int,
                'currency': str, 'pending': bool, 'reviewed': bool, 'note': str,
                'isTransfer': bool, 'inferredTransfer': bool, 'source': str}
    return (type(row) is dict and all(type(row.get(key)) is kind for key, kind in required.items())
            and 'categoryId' in row and (row['categoryId'] is None or type(row['categoryId']) is str)
            and 'merchantLogo' in row and (row['merchantLogo'] is None or type(row['merchantLogo']) is str))


def _receipt(data, pairs, reviewed, fingerprint):
    ids = [pair[name] for pair in pairs for name in ('outId', 'inId')]
    peers = {pair[key]: pair[other] for pair in pairs for key, other in (('outId', 'inId'), ('inId', 'outId'))}
    allowed = {'transferPeerId', 'transferPairValid', 'isTransfer', 'reviewed'}
    return (type(data) is dict and data.get('action') == 'match'
            and type(data.get('pairCount')) is int and data['pairCount'] == len(pairs)
            and type(data.get('count')) is int and data['count'] == len(ids)
            and type(data.get('changed')) is int and data['changed'] == len(ids)
            and data.get('reviewed') is reviewed
            and data.get('fingerprint') == fingerprint
            and [row['id'] for row in data['changes']] == ids
            and all(valid_record(row['before']) and valid_record(row['after'])
                    and row['before']['id'] == row['id'] == row['after']['id']
                    and row['after'].get('transferPeerId') == peers[row['id']]
                    and row['after'].get('isTransfer') is True
                    and row['after'].get('reviewed') is reviewed
                    and {key: value for key, value in row['before'].items() if key not in allowed}
                    == {key: value for key, value in row['after'].items() if key not in allowed}
                    for row in data['changes']))


def execute_bulk_transfers(connection, identifier, prefix, args, exchange, error):
    pairs = read_pairs(args.file, error)
    parameters = {'pairs': pairs, 'reviewed': args.reviewed}
    preview = None
    if args.revision is not None and args.fingerprint is not None and not args.dry_run:
        revision, fingerprint = args.revision, args.fingerprint
    else:
        preview = checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True, json={
            'operation': 'preview_transfer_matches', 'workspace': 'personal', 'parameters': parameters,
        }), identifier, error)
        revision, fingerprint = preview['revision'], preview['data']['fingerprint']
        if args.revision is not None and args.revision != revision or args.fingerprint is not None and args.fingerprint != fingerprint:
            raise error('Transfer batch preview changed. Inspect the current pairs; no change was submitted.')
        if args.dry_run:
            return {**preview, 'budget': args.target, 'dryRun': True, **parameters}
    history = {**({'actor': args.agent, 'actorKind': 'agent'} if args.agent else {}), **({'batchId': args.batch} if args.batch else {})}
    intent = {'operation': 'match_transfers', 'workspace': 'personal', 'payload': {**parameters, 'fingerprint': fingerprint},
              'expected_revision': revision, 'request_id': args.request_id or str(uuid4()), **({'history': history} if history else {})}
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=intent), identifier, error)
        if not _receipt(saved['data'], pairs, args.reviewed, fingerprint) or preview is not None and saved['data'] != preview['data']:
            raise error('Saved transfer batch receipt does not match the exact validated intent.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        refused = getattr(failure, 'status_code', None) in {400, 409}
        message = ('Atomic transfer matching refused; no new pairs were saved. ' + str(failure) + '. '
                   if refused else 'Atomic transfer result was not confirmed; it may have been saved. ')
        raise error(message + 'No automatic retry. Inspect saved pairs/history. Retain budgetId ' + identifier
                    + ' and the COMPLETE original intent: ' + json.dumps(intent) + '.') from failure
    return {**saved, 'budget': args.target, 'dryRun': False, **parameters,
            'requestId': intent['request_id'], 'expectedRevision': revision, 'fingerprint': fingerprint, 'intent': intent}


def bulk_transfer_table(result):
    data = result['data']
    lines = ['Budget: ' + json.dumps(result['budget'], ensure_ascii=True) + ' (' + json.dumps(result['budgetId']) + ')',
             'Read-only preview — no save' if result['dryRun'] else 'Saved atomic transfer receipt (exact retries return the original result)',
             f"{len(result['pairs'])} pairs; {data['count']} transaction legs; reviewed={str(result['reviewed']).lower()}"]
    for row in data['changes']:
        lines.extend([json.dumps(row['id'], ensure_ascii=True), '  before: ' + json.dumps(row['before'], ensure_ascii=True, sort_keys=True),
                      '  after:  ' + json.dumps(row['after'], ensure_ascii=True, sort_keys=True)])
    lines.extend(['Revision: ' + result['revision'], 'Fingerprint: ' + data['fingerprint']])
    if not result['dryRun']:
        lines.extend(['Request ID: ' + json.dumps(result['requestId']), 'Original expected revision: ' + json.dumps(result['expectedRevision'])])
    return '\n'.join(lines)
