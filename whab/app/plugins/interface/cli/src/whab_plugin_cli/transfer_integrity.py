"""Read-only transfer audit and explicit guarded legacy repair."""

import argparse
import json
from uuid import uuid4

lazy import httpx

from whab.plugins import validate_category_agent

from .bulk_transfers import valid_record
from .transaction_commands import checked
from .transaction_search import calendar_date
from .transfer_help import TRANSFER_AUDIT_HELP


def add_integrity_parsers(commands):
    audit = commands.add_parser('transfer-audit', help='Read legacy transfer issues, including unpaired and zero-value hints',
        epilog=TRANSFER_AUDIT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    audit.add_argument('--since', type=calendar_date, help='Inclusive lower date YYYY-MM-DD; default all history')
    audit.add_argument('--until', type=calendar_date, help='Inclusive upper date YYYY-MM-DD; default all history')
    audit.add_argument('--offset', type=int, default=0, help='Exact next offset from preceding result; retain same revision')
    audit.add_argument('--limit', type=int, default=100, help='Page size 1–100 (default 100); inspect total/next')
    audit.add_argument('--format', choices=('json', 'table'), default='json', help='Complete JSON page (default) or readable issue records')
    repair = commands.add_parser('repair-transfers', help='Atomically clear explicitly inspected invalid legacy transfer state',
        epilog=TRANSFER_AUDIT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    repair.add_argument('transaction_ids', nargs='+', help='1–200 explicit audited IDs; include all invalid counterpart legs')
    repair.add_argument('--dry-run', action='store_true', help='Exact read-only core before/after preview; inspect before deciding')
    repair.add_argument('--revision', help='Observed preview revision or original revision for an exact retry')
    repair.add_argument('--fingerprint', help='Exact preview fingerprint or original fingerprint for an exact retry')
    repair.add_argument('--request-id', help='Original intent ID for exact recovery; otherwise generated once')
    repair.add_argument('--agent', type=validate_category_agent, help='Self-declared audit actor; no category inference')
    repair.add_argument('--batch', help='History grouping only; complete repair already commits once')
    repair.add_argument('--format', choices=('json', 'table'), default='json', help='Complete JSON (default) or every before/after record')


def execute_integrity(connection, identifier, prefix, args, exchange, error):
    def query(operation, parameters):
        return checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True, json={
            'operation': operation, 'workspace': 'personal', 'parameters': parameters,
        }), identifier, error)
    if args.filter == 'transfer-audit':
        result = query('transfer_audit', {key: value for key, value in {
            'since': args.since, 'until': args.until, 'offset': args.offset, 'limit': args.limit,
        }.items() if value is not None})
        return {**result, 'budget': args.target, 'readOnly': True}
    parameters = {'transactionIds': args.transaction_ids}
    preview = None
    if args.revision is not None and args.fingerprint is not None and not args.dry_run:
        revision, fingerprint = args.revision, args.fingerprint
    else:
        preview = query('preview_transfer_repair', parameters)
        revision, fingerprint = preview['revision'], preview['data']['fingerprint']
        if args.revision is not None and args.revision != revision or args.fingerprint is not None and args.fingerprint != fingerprint:
            raise error('Transfer repair preview changed. Inspect current issues; no change was submitted.')
        if args.dry_run:
            return {**preview, 'budget': args.target, 'dryRun': True, **parameters}
    history = {**({'actor': args.agent, 'actorKind': 'agent'} if args.agent else {}), **({'batchId': args.batch} if args.batch else {})}
    intent = {'operation': 'repair_transfers', 'workspace': 'personal', 'payload': {**parameters, 'fingerprint': fingerprint},
              'expected_revision': revision, 'request_id': args.request_id or str(uuid4()), **({'history': history} if history else {})}
    try:
        result = checked(exchange(connection, 'POST', prefix + '/commands', ledger_errors=True, json=intent), identifier, error)
        data = result['data']
        if (type(data) is not dict or data.get('action') != 'repair' or data.get('fingerprint') != fingerprint
                or type(data.get('count')) is not int or data['count'] != len(args.transaction_ids)
                or type(data.get('changed')) is not int or not 0 <= data['changed'] <= data['count']
                or [row['id'] for row in data['changes']] != args.transaction_ids
                or any(not valid_repair_change(row) for row in data['changes'])
                or preview is not None and data != preview['data']):
            raise error('Saved repair receipt does not match the exact validated intent.')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, httpx.HTTPError) as failure:
        refused = getattr(failure, 'status_code', None) in {400, 409}
        message = ('Transfer repair refused; no new changes were saved. ' + str(failure) + '. '
                   if refused else 'Transfer repair result was not confirmed; it may have been saved. ')
        raise error(message + 'No automatic retry. Inspect records/history. Retain budgetId ' + identifier
                    + ' and the COMPLETE original intent: ' + json.dumps(intent) + '.') from failure
    return {**result, 'budget': args.target, 'dryRun': False, **parameters, 'requestId': intent['request_id'],
            'expectedRevision': revision, 'fingerprint': fingerprint, 'intent': intent}


def valid_repair_change(row):
    before, after = row['before'], row['after']
    if not valid_record(before) or not valid_record(after):
        return False
    cleared = before['categoryId'] is not None and after['categoryId'] is None
    allowed = {'transferPeerId', 'transferPairValid', 'isTransfer', 'reviewed', 'categoryId', 'categoryAgent', 'categoryEdited'}
    return (before['id'] == row['id'] == after['id'] and after['reviewed'] is False
            and after.get('transferPeerId') is None
            and after['isTransfer'] is (after['categoryId'] is None and before['inferredTransfer'])
            and after['categoryId'] in (None, before['categoryId'])
            and (after.get('categoryAgent') is None if cleared else after.get('categoryAgent') == before.get('categoryAgent'))
            and (after.get('categoryEdited') is True if cleared else after.get('categoryEdited') == before.get('categoryEdited'))
            and {key: value for key, value in before.items() if key not in allowed}
            == {key: value for key, value in after.items() if key not in allowed})


def integrity_table(result):
    data = result['data']
    lines = ['Budget: ' + json.dumps(result['budget'], ensure_ascii=True) + ' (' + json.dumps(result['budgetId']) + ')']
    if result.get('readOnly'):
        lines.append(f"Transfer issues: {data['total']} total; next offset: {data['next']} (read-only)")
        lines.extend(json.dumps(row, ensure_ascii=True, sort_keys=True) for row in data['issues'])
        lines.append('No records changed. Inspect exact repair-transfers --dry-run before deciding.')
    else:
        lines.extend(['Read-only repair preview — no save' if result['dryRun'] else 'Saved atomic repair receipt (exact retries return the original result)',
                      f"{data['count']} targets; {data['changed']} changed"])
        for row in data['changes']:
            lines.extend([json.dumps(row['id'], ensure_ascii=True), '  before: ' + json.dumps(row['before'], ensure_ascii=True, sort_keys=True),
                          '  after:  ' + json.dumps(row['after'], ensure_ascii=True, sort_keys=True)])
        lines.append('Fingerprint: ' + data['fingerprint'])
        if not result['dryRun']:
            lines.extend(['Request ID: ' + json.dumps(result['requestId']), 'Original expected revision: ' + json.dumps(result['expectedRevision'])])
    lines.append('Revision: ' + result['revision'])
    return '\n'.join(lines)
