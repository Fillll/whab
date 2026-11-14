"""Classification-aware review and complete read-only legacy inspection."""

import argparse

from whab.plugins import review_status

from .bulk_commands import bulk_options, execute_bulk
from .transaction_commands import checked
from .transaction_reads import terminal_text
from .transaction_search import calendar_date, execute_search, validate_search

GUIDE = """A posted transaction needs an Expense, Income or explicitly chosen Transfer
category (or an already confirmed transfer pair) before review. A bank transfer
hint alone does not qualify. Classification does not automatically review it;
clearing classification also sets reviewed=false. A combined classification and
review is allowed in trnx edit-many when the final state is valid.

review changes 1–100 explicit IDs atomically: one revision, request ID, native
commit and durable save. One invalid/missing/duplicate target rejects everything.
No automatic splits/retries. --batch groups history only. Preserve the complete
reported intent, revision, request ID and actor/batch metadata after uncertainty;
exact retries never add changes/history/saves. Never retry with a new intent.

Examples (fictional):
  whab "My budget" trnx review TX1 TX2 --reviewed --dry-run --agent Codex
  whab "My budget" trnx review TX1 TX2 --reviewed --agent Codex
  whab "My budget" trnx review-audit --format table
  whab "My budget" trnx review TX3 TX4 --needs-review --dry-run --agent Codex
  whab "My budget" trnx review TX3 TX4 --needs-review --agent Codex

review-audit is a read-only legacy audit. It lists saved nondeleted reviewed flags that fail the current
classification/pending rule; upgrades/reads do not automatically change them.
Inspect each result before explicitly clearing review. Original import notes,
IDs, financial fields, category attribution and history remain intact. Audit
results are complete for the chosen inclusive --since/--until window, or fail
explicitly at the existing 16 MiB read limit; narrow dates rather than accepting
truncation. Names/emoji are preserved in JSON. Help works offline.
"""


def add_review_parsers(actions):
    parser = actions.add_parser('review', help='Explicitly review/unreview 1–100 classified transaction IDs atomically',
        epilog=GUIDE, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('transaction_id', nargs='+', help='Stable transaction IDs from trnx list; never merchant inference')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--reviewed', dest='reviewed', action='store_true', help='Mark reviewed only when classification/pending guards pass for every target')
    mode.add_argument('--needs-review', dest='reviewed', action='store_false', help='Explicitly clear review; also works for legacy unclassified records')
    bulk_options(parser)
    audit = actions.add_parser('review-audit', help='Read saved reviewed records that fail current eligibility; never cleans automatically',
        epilog=GUIDE, formatter_class=argparse.RawDescriptionHelpFormatter)
    audit.add_argument('--since', type=calendar_date, help='Inclusive lower date YYYY-MM-DD; default all history')
    audit.add_argument('--until', type=calendar_date, help='Inclusive upper date YYYY-MM-DD; default all history')
    audit.add_argument('--format', choices=('json','table'), default='json', help='Complete JSON (default) or readable IDs/reasons')


def execute_review(connection, identifier, prefix, args, exchange, error):
    if args.filter == 'review':
        policy = checked(exchange(connection, 'GET', prefix + '/queries/review_policy', params={'workspace':'personal'}), identifier, error)
        if type(policy['data']) is not dict or policy['data'].get('version') != 1 or policy['data'].get('classificationRequired') is not True:
            raise error('Update the authoritative WHAB host to enforce classification before review. Nothing was submitted.')
    observed = checked(exchange(connection, 'GET', prefix + '/queries/categories', params={'workspace':'personal'}), identifier, error)
    if args.filter == 'review':
        edits = [{'transactionId': value, 'reviewed': args.reviewed} for value in args.transaction_id]
        return execute_bulk(connection, identifier, prefix, args, exchange, error, 'edit_transactions', edits, observed['revision'])
    args.month, args.merchant = None, []
    validate_search(args, error)
    found = execute_search(connection, identifier, prefix, args, exchange, error)
    if found['revision'] != observed['revision']:
        raise error('WHAB changed during review audit. Read again; no partial result or write was returned.')
    categories = {row['id']: row for row in observed['data']}
    rows = [{**row, 'reviewIssue': review_status(row, categories)['reason']} for row in found['data']
            if row['reviewed'] and not review_status(row, categories)['eligible']]
    return {**found, 'budget':args.target, 'data':rows, 'total':len(rows), 'returned':len(rows), 'complete':True, 'readOnly':True}


def review_audit_table(result):
    return '\n'.join([f"Budget: {terminal_text(result['budget'], 100)}", f"Legacy review issues: {result['total']} (complete, read-only)",
                      *[terminal_text(row['id'], None) + '  ' + terminal_text(row['reviewIssue'], None) for row in result['data']],
                      'No records were changed. Preview an explicit trnx review --needs-review for confirmed IDs.'])
