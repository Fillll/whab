"""Explicit reversible transaction tombstones through the guarded public ledger."""

import argparse
import json
from uuid import uuid4

lazy import httpx

from whab.plugins import ACCOUNT_TYPES, is_liability, validate_category_agent

from .bulk_transfers import valid_record
from .transaction_commands import checked

VOID_HELP = '''Reversible transaction corrections (1–100 explicit stable IDs):
  whab "My budget" trnx void TX1 TX2 --dry-run --format table
  whab "My budget" trnx void TX1 TX2 --revision REV --fingerprint HASH --agent Codex
  whab "My budget" trnx unvoid TX1 TX2 --dry-run
  whab "My budget" trnx unvoid TX1 TX2 --agent Codex --batch correction

Use void only for user-confirmed erroneous records. Discover live IDs with trnx
list; transaction names/payees are not identities. Preserve the exact IDs from
the receipt/history for unvoid. Void is reversible exclusion, not physical
deletion, a zero-amount edit, or a fabricated balancing transaction. Unvoid
restores explicitly voided records; it does not revive provider-only deletions.

Every selected record is validated before ONE native atomic commit, revision
advancement, synchronous durable save and permanent exact before/after audit
entry. Empty, oversized, duplicate, missing or disallowed selections reject ALL
changes. Batches are never split. Include both reciprocal transfer legs
explicitly; no counterpart, amount or link is invented. Imported, manual and
zero-value records use the same explicit workflow.

--dry-run returns the exact core before/after records and balanceChanges, plus
the observed revision and fingerprint, without mutation/history/save. Inspect
both records and balances before deciding. Bind that preview with --revision
and --fingerprint; otherwise the CLI previews current state internally. Normal
execution is noninteractive. Stale revisions and changed fingerprints refuse
the complete operation. JSON is complete; tables show every proposed/saved
record and account balance change in integer currency minor units.

Imported corrections preserve provider/imported account balance snapshots;
manual-entry corrections reverse/reapply their account balance effect. Inspect
the explicit balanceChanges rather than assuming voiding changes a bank balance.
Each balance change names its accountType: assets reverse an outflow by adding
its amount; liabilities reverse the amount owed. Unvoid applies the opposite.
Unknown/null snapshots stay unknown when no balance adjustment is needed; a
nonzero manual adjustment requires a known balance and otherwise rejects all.
Monthly assignments remain exact, even if excluding false income reveals a
funding deficit; this command does not silently reallocate envelopes.
No bank sync, guessed reconciliation or future rule is created. Import identity,
source evidence, notes, merchant whitespace and financial fields are retained.
Explicit tombstones prevent refresh/reimport from silently resurrecting a void.
Both void and unvoid leave affected transactions unreviewed. Existing reciprocal
pair links and categories remain intact; both legs must be restored together.
Unvoid sets voided=false as an explicit restoration marker, so a later posted
refresh cannot inherit an old pending tombstone. It refuses a later provider-
deletion marker rather than overriding evidence.

Verify the saved receipt and history list/show. Voided records are excluded
from ordinary trnx list; use unvoid IDS --dry-run to inspect the retained rows.
Unvoid and guarded history undo/redo retain permanent history and must satisfy
current financial/transfer rules. --agent labels the audit actor; --batch groups
history entries and never combines separate commands into one commit.

After an uncertain response, inspect receipt/history and retain the COMPLETE
original intent: ordered IDs, action, --revision, --fingerprint, --request-id,
--agent and --batch. Exact successful retries return the original durable receipt
after restart without duplicate changes/history/saves. Never retry automatically
or replace an uncertain intent with a fresh revision/request ID. Conflicting
request-ID reuse is refused. Unsupported backends fail before mutation.
Requires CLI 2.24 / WHAB 2.82 and native SQLite 1.30 or JSON 1.22. Help is offline.
'''


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
        refused = getattr(failure, 'status_code', None) in {400, 409}
        message = ('Atomic transaction ' + args.filter + ' refused; nothing new was saved. ' + str(failure) + '. '
                   if refused else 'Atomic transaction ' + args.filter + ' result was not confirmed; it may have been saved. ')
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
