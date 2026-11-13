"""Explicit transfer proposals and guarded pair confirmation over public operations."""

import argparse
from uuid import uuid4

lazy import httpx

from whab.plugins import validate_category_agent

from .account_commands import resolve_account
from .transaction_commands import checked

TRANSFER_HELP = '''Transfer proposals are suggestions, never automatic matches or reviews.
  whab "My budget" trnx transfers --since 2026-11-01 --until 2026-11-30 --format table
  whab "My budget" trnx match-transfer OUT_ID IN_ID --dry-run
  whab "My budget" trnx match-transfer OUT_ID IN_ID --agent Codex
  whab "My budget" trnx unmatch-transfer OUT_ID IN_ID --dry-run

Eligibility: two existing posted, nondeleted, uncategorized transactions in
different active accounts, equal nonzero opposite amounts, same currency AND
on/off-budget scope, within seven calendar days. A date/account filter selects an
anchor; the other leg may lie outside that selection by up to seven days. Names
and bank hints are evidence, not proof. Refunds can look like transfers: inspect
both merchants/accounts/dates/amounts. All ambiguous alternatives stay explicit.
Cross-currency, fees/splits and movements between budget scopes need manual
handling and are not paired. No bank sync or standing future rule is created.

List is read-only. JSON includes complete stable IDs, both public transactions,
account names, evidence, ambiguity, exact total and next offset. Default limit
100; use --offset NEXT with identical filters and require the same revision until
next=null. Over 1,000 candidates fails explicitly: narrow dates/accounts; nothing
is silently truncated. Amounts are integer minor units; currencies stay separate.

Match/unmatch is an explicit confirmation. --dry-run returns exact before/after,
revision and fingerprint without a commit. To bind a reviewed preview, copy its
--revision REV and --fingerprint HASH into the confirmation command. Without
these flags the CLI reads the current exact plan and generates revision/request
machinery internally. Execution is noninteractive, one atomic native commit,
durable checkpoint and reversible audit entry. --agent labels the audit actor;
--batch only groups history. Neither command sets reviewed=true: both records
remain unreviewed for ordinary transaction review. Notes, category attribution,
amounts, merchants (including whitespace), dates, accounts and identities stay
unchanged. Match sets reciprocal transferPeerId and isTransfer=true. Unmatch
removes links and restores isTransfer from the existing provider hint. Unmatch
before changing paired amounts/dates/accounts/categories or account budget scope.
Provider corrections/removals that invalidate a pair remove both links and leave
both unreviewed; provider hints remain distinct from a confirmed match.

Verify with trnx list (JSON includes transferPeerId) and history list/show. Undo
and redo use the existing fingerprint/revision-guarded history commands. Stale
state is refused. After an uncertain result, inspect the saved pair/history and
retain BOTH original IDs, action, --revision, --fingerprint and --request-id.
Exact successful retries return the original durable result after restart. Never
retry an uncertain write with fresh intent; conflicting request reuse is refused.
Unsupported backends/authorities fail before mutation. Help works offline.
'''


def add_transfer_parsers(commands):
    read = commands.add_parser('transfers', help='Propose possible transfer pairs for explicit review (read-only)',
        epilog=TRANSFER_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('since', 'until'):
        read.add_argument('--' + name, help='Inclusive anchor date YYYY-MM-DD; counterpart may be up to seven days outside')
    read.add_argument('--currency', help='One uppercase ISO currency; omit for separate same-currency pairs')
    read.add_argument('--account', action='append', help='Anchor account exact name or stable ID; repeat for several, no fuzzy match')
    read.add_argument('--offset', type=int, default=0, help='Explicit next offset from the preceding result (default 0)')
    read.add_argument('--limit', type=int, default=100, help='Proposals per page, 1–100 (default 100); inspect next/total')
    read.add_argument('--format', choices=('json', 'table'), default='json', help='Complete structured JSON (default) or readable pairs')
    for name in ('match-transfer', 'unmatch-transfer'):
        command = commands.add_parser(name, help='Explicitly ' + name.replace('-', ' ') + ' in one guarded commit; both remain unreviewed',
            epilog=TRANSFER_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
        command.add_argument('transaction_ids', nargs=2, metavar=('OUT_ID', 'IN_ID'), help='Exactly two stable IDs from the proposal or saved pair')
        command.add_argument('--dry-run', action='store_true', help='Read-only exact native before/after plan; no commit/save')
        command.add_argument('--revision', help='Observed preview revision, or original revision for an exact retry')
        command.add_argument('--fingerprint', help='Exact preview fingerprint, or original fingerprint for an exact retry')
        command.add_argument('--request-id', help='Original intent ID for recovery; otherwise generate once')
        command.add_argument('--agent', type=validate_category_agent, help='Self-declared audit actor; matching does not overwrite category attribution')
        command.add_argument('--batch', help='Optional history grouping; confirmation still commits atomically')
        command.add_argument('--format', choices=('json', 'table'), default='json', help='Saved exact JSON (default) or readable before/after')


def execute_transfer(connection, identifier, prefix, args, exchange, error):
    def query(operation, parameters):
        return checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True, json={
            'operation': operation, 'workspace': 'personal', 'parameters': parameters,
        }), identifier, error)
    if args.filter == 'transfers':
        account_ids, observed = [], None
        if args.account:
            observed = checked(exchange(connection, 'GET', prefix + '/queries/accounts', params={'workspace': 'personal'}), identifier, error)
            account_ids = list(dict.fromkeys(resolve_account(observed['data'], value, error)['id'] for value in args.account))
        result = query('transfer_candidates', {key: value for key, value in {
            'since': args.since, 'until': args.until, 'currency': args.currency,
            'offset': args.offset, 'limit': args.limit, **({'accountIds': account_ids} if account_ids else {}),
        }.items() if value is not None})
        if observed is not None and result['revision'] != observed['revision']:
            raise error('Budget changed during account resolution. Read proposals again; nothing was changed.')
        return {**result, 'budget': args.target}
    action = 'unmatch' if args.filter == 'unmatch-transfer' else 'match'
    parameters = {'transactionIds': args.transaction_ids, 'action': action}
    if args.revision is not None and args.fingerprint is not None and not args.dry_run:
        revision, fingerprint = args.revision, args.fingerprint
    else:
        preview = query('preview_transfer_match', parameters)
        revision, fingerprint = preview['revision'], preview['data']['fingerprint']
        if args.revision is not None and args.revision != revision or args.fingerprint is not None and args.fingerprint != fingerprint:
            raise error('Transfer preview changed. Inspect both current transactions; no change was submitted.')
        if args.dry_run:
            return {**preview, 'budget': args.target, 'dryRun': True, 'transactionIds': args.transaction_ids}
    intent = args.request_id or str(uuid4())
    history = {**({'actor': args.agent, 'actorKind': 'agent'} if args.agent else {}), **({'batchId': args.batch} if args.batch else {})}
    try:
        result = checked(exchange(connection, 'POST', prefix + '/commands', json={
            'operation': 'match_transfer', 'workspace': 'personal', 'payload': {**parameters, 'fingerprint': fingerprint},
            'expected_revision': revision, 'request_id': intent, **({'history': history} if history else {}),
        }), identifier, error)
    except (ValueError, OSError, RuntimeError, httpx.HTTPError) as failure:
        raise error(f'Transfer confirmation was not confirmed. Inspect both saved IDs/history; no automatic retry. Retain original IDs/action, --revision {revision} --fingerprint {fingerprint} --request-id {intent}.') from failure
    return {**result, 'budget': args.target, 'transactionIds': args.transaction_ids,
            'expectedRevision': revision, 'fingerprint': fingerprint, 'requestId': intent}


def transfer_table(result):
    data = result['data']
    if 'candidates' not in data:
        rows = [f"{data['action']}: {result.get('budget', result.get('budgetId'))} · both need review"]
        for change in data['changes']:
            before = change['before']
            rows.append(f"{before['date']} | {before['accountName']} | {before['merchant']} | {before['amount']} {before['currency']} minor units | memo: {before['note']}")
            rows.append(f"{change['id']} → peer {change['after'].get('transferPeerId', 'none')} · reviewed=false")
        return '\n'.join(rows)
    rows = [f"Possible transfers: {data['total']} total · {len(data['candidates'])} shown · next offset: {data['next']}",
            'Suggestions only. Review both sides before confirming.']
    for pair in data['candidates']:
        rows.append('Ambiguous alternatives' if pair['ambiguous'] else 'Possible pair')
        for row in pair['transactions']:
            rows.append(f"  {row['id']} | {row['date']} | {row['accountName']} | {row['merchant']} | {row['amount']} {row['currency']} minor units")
    return '\n'.join(rows)
