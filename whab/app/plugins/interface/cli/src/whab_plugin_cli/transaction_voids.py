"""Explicit reversible transaction tombstones through the guarded public ledger."""

import argparse
import json
from uuid import uuid4

lazy import httpx

from whab.plugins import validate_category_agent
lazy from whab.plugins import ACCOUNT_TYPES, is_liability

from .transaction_void_help import VOID_HELP
lazy from .bulk_transfers import valid_record
lazy from .transaction_commands import checked


def add_void_parsers(commands):
    for action in ('void', 'unvoid'):
        parser = commands.add_parser(action,
            help='Atomically exclude erroneous transactions while retaining reversible tombstones' if action == 'void'
                 else 'Atomically restore explicitly voided transactions',
            description='Preview exact records and account balances, then make one guarded reversible correction.',
            epilog=VOID_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument('transaction_ids', nargs='+', help='1–100 distinct stable IDs; include both reciprocal transfer legs explicitly')
        parser.add_argument('--dry-run', action='store_true', help='Exact read-only before/after and balanceChanges preview; no commit/save')
        parser.add_argument('--revision', help='Observed preview revision; retain the original revision for exact recovery')
        parser.add_argument('--fingerprint', help='Exact preview fingerprint; retain the original fingerprint for exact recovery')
        parser.add_argument('--request-id', help='Original intent ID for exact retry; otherwise a new UUID is generated once')
        parser.add_argument('--agent', type=validate_category_agent, help='Self-declared audit actor; existing category-agent attribution is preserved')
        parser.add_argument('--batch', help='History grouping only; this complete operation already commits once')
        parser.add_argument('--format', choices=('json', 'table'), default='json', help='Complete JSON (default) or every before/after and balance change')


def _ids(values, error):
    if not 1 <= len(values) <= 100:
        raise error('Choose 1–100 explicit transaction IDs; batches are never split.')
    seen = set()
    for index, value in enumerate(values, 1):
        if type(value) is not str or not 1 <= len(value) <= 200 or '\x00' in value:
            raise error(f'Target {index}: use a nonempty stable transaction ID of at most 200 characters.')
        try:
            value.encode('utf-8')
        except UnicodeError:
            raise error(f'Target {index}: transaction ID must be valid UTF-8 text.') from None
        if value in seen:
            raise error(f'Target {index}: duplicate transaction ID {value!r}; nothing was submitted.')
        seen.add(value)
    return list(values)


def _receipt(data, ids, action, fingerprint):
    allowed = {'deleted', 'voided', 'reviewed'}
    if (type(data) is not dict or data.get('action') != action
            or type(data.get('count')) is not int or data['count'] != len(ids)
            or type(data.get('changed')) is not int or data['changed'] != len(ids)
            or data.get('fingerprint') != fingerprint
            or type(data.get('changes')) is not list or [row['id'] for row in data['changes']] != ids
            or type(data.get('balanceChanges')) is not list):
        return False
    accounts = {}
    for row in data['changes']:
        before, after = row['before'], row['after']
        if (not valid_record(before) or not valid_record(after)
                or before['id'] != row['id'] or after['id'] != row['id']
                or after['reviewed'] is not False
                or after.get('deleted') is not (action == 'void')
                or (action == 'void' and (before.get('deleted', False) is not False or before.get('voided', False) is not False
                                         or after.get('voided') is not True))
                or (action == 'unvoid' and (before.get('voided') is not True or before.get('deleted') is not True
                                          or before.get('providerDeleted', False) is not False or after.get('voided') is not False))
                or {key: value for key, value in before.items() if key not in allowed}
                != {key: value for key, value in after.items() if key not in allowed}):
            return False
        accounts.setdefault(before['accountId'], {'currency': before['currency'], 'manual': 0})
        if before['currency'] != accounts[before['accountId']]['currency']:
            return False
        if before['source'] in {'manual', 'demo'}:
            accounts[before['accountId']]['manual'] += before['amount']
    balances = data['balanceChanges']
    return (len(balances) == len(accounts) and len({row['accountId'] for row in balances}) == len(accounts)
            and all(type(row) is dict and row.get('accountId') in accounts
                    and row.get('currency') == accounts[row['accountId']]['currency']
                    and row.get('accountType') in ACCOUNT_TYPES
                    and _balance(row, accounts[row['accountId']]['manual'], action)
                    for row in balances))


def _balance(row, manual, action):
    if row.get('before') is None or row.get('after') is None:
        return 'before' in row and 'after' in row and row['before'] is None and row['after'] is None and manual == 0
    return (type(row['before']) is int and type(row['after']) is int
            and row['after'] - row['before'] == manual
            * (-1 if is_liability({'type': row['accountType']}) else 1) * (1 if action == 'void' else -1))


def execute_void(connection, identifier, prefix, args, exchange, error):
    ids = _ids(args.transaction_ids, error)
    parameters = {'transactionIds': ids}
    operation = args.filter + '_transactions'
    preview = None
    if args.revision is not None and args.fingerprint is not None and not args.dry_run:
        revision, fingerprint = args.revision, args.fingerprint
    else:
        preview = checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True, json={
            'operation': 'preview_' + operation, 'workspace': 'personal', 'parameters': parameters,
        }), identifier, error)
        revision, fingerprint = preview['revision'], preview['data']['fingerprint']
        if not _receipt(preview['data'], ids, args.filter, fingerprint):
            raise error('Invalid transaction void/unvoid preview; no change was submitted.')
        if args.revision is not None and args.revision != revision or args.fingerprint is not None and args.fingerprint != fingerprint:
            raise error('Transaction ' + args.filter + ' preview changed. Inspect current records and balances; nothing was submitted.')
        if args.dry_run:
            return {**preview, 'budget': args.target, 'dryRun': True, **parameters}
    history = {**({'actor': args.agent, 'actorKind': 'agent'} if args.agent else {}), **({'batchId': args.batch} if args.batch else {})}
    intent = {'operation': operation, 'workspace': 'personal', 'payload': {**parameters, 'fingerprint': fingerprint},
              'expected_revision': revision, 'request_id': args.request_id or str(uuid4()), **({'history': history} if history else {})}
    try:
        saved = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=intent), identifier, error)
        if not _receipt(saved['data'], ids, args.filter, fingerprint) or preview is not None and saved['data'] != preview['data']:
            raise error('Saved transaction void/unvoid receipt does not match the exact validated intent.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        message = 'Atomic transaction ' + args.filter + ' result was not confirmed; it may have been saved. '
        if getattr(failure, 'status_code', None) in {400, 409}:
            message += 'Server reported: ' + str(failure) + '. '
        raise error(message + 'No automatic retry. Inspect saved receipt/history. Retain budgetId ' + identifier
                    + ' and the COMPLETE original intent: ' + json.dumps(intent) + '.') from failure
    return {**saved, 'budget': args.target, 'dryRun': False, **parameters,
            'requestId': intent['request_id'], 'expectedRevision': revision, 'fingerprint': fingerprint, 'intent': intent}


def void_table(result):
    data = result['data']
    lines = ['Budget: ' + json.dumps(result['budget'], ensure_ascii=True) + ' (' + json.dumps(result['budgetId']) + ')',
             'Read-only preview — no save' if result['dryRun'] else 'Saved atomic transaction receipt (exact retry returns original result)',
             f"Action: {data['action']}; {data['count']} transactions; {data['changed']} changed"]
    for row in data['changes']:
        lines.extend([json.dumps(row['id'], ensure_ascii=True), '  before: ' + json.dumps(row['before'], ensure_ascii=True, sort_keys=True),
                      '  after:  ' + json.dumps(row['after'], ensure_ascii=True, sort_keys=True)])
    lines.append('Account balanceChanges (integer currency minor units):')
    lines.extend(json.dumps(row, ensure_ascii=True, sort_keys=True) for row in data['balanceChanges'])
    lines.extend(['Revision: ' + result['revision'], 'Fingerprint: ' + data['fingerprint']])
    if not result['dryRun']:
        lines.extend(['Request ID: ' + json.dumps(result['requestId']), 'Original expected revision: ' + json.dumps(result['expectedRevision'])])
    return '\n'.join(lines)
