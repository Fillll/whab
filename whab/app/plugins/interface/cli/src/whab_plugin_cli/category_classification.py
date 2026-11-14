"""Explicit Expense/Income/Transfer category treatment over guarded public operations."""

import argparse
from copy import deepcopy

from .record_edits import RECOVERY, edit_options, intent, saved_edit
from .transaction_commands import checked

GUIDE = """Expense, Income and Transfer are financial types independent of display/group names.
Review is explicit and requires classification. Clearing classification unreviews.
Posted external inflows in eligible on-budget non-credit accounts fund Ready to
Assign once. Pending records, transfers, off-budget accounts and credit-card
inflows do not fund it. Income reversals reduce income; expense-category refunds
reduce spending. Amounts, balances, merchant whitespace, notes, original/import
information, categories' IDs/parents/currencies and reviews are preserved.

Income and Transfer categories have target zero and NO monthly assignments, including zero
rows. Conversion refuses existing targets/assignments; create a new Income child
instead to preserve an old expense envelope. A parent can mix Income and Expense.
Missing type on older categories remains Expense. No automatic migration, merchant
inference, transaction relabeling, review change or bank synchronization occurs.
Legacy uncategorized inflows keep their existing funding behavior for compatibility;
summary.classifiedIncome and summary.unclassifiedIncome separate the contributions.

Preview FIRST: changing a referenced category's type changes historical income,
spending and Ready to Assign. --dry-run shows the exact category before/after and
summary impacts for represented ledger months, without any commit or save.
Normal execution is noninteractive, one guarded commit/save with undo/redo history.
Exact retries keep the identical --type, --revision, --request-id and audit metadata.

Examples (fictional):
  whab "My budget" category create Salary --parent Finance --type income
  whab "My budget" category classify "Finance / Salary" --type income --dry-run
  whab "My budget" category classify "Finance / Salary" --type income --agent Codex
  whab "My budget" trnx categorize TX1 TX2 --category "Finance / Salary" --agent Codex
Categorize supports 1–100 explicit IDs and --dry-run; mixed edits use trnx edit-many.
Assignment never auto-reviews; clearing classification makes unreviewed.
Set review explicitly with trnx review; inspect legacy flags with trnx review-audit.
"""


def add_classify_parser(actions):
    parser = actions.add_parser('classify', help='Set Expense/Income/Transfer treatment without replacing a category',
        description='Explicit financial classification of one existing child. Preview historical totals before applying.',
        epilog=GUIDE + '\n' + RECOVERY, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('category', help='Stable child ID, unique exact name or Group / Category; no fuzzy matching')
    parser.add_argument('--type', dest='category_kind', required=True, choices=('expense', 'income', 'transfer'), help='Financial treatment; independent of parent/name/review')
    parser.add_argument('--dry-run', action='store_true', help='Read exact proposed kind and monthly totals; never commits or saves')
    edit_options(parser)


def execute_classification(connection, identifier, prefix, args, exchange, error, observed, read, selected):
    preview = checked(exchange(connection, 'POST', prefix + '/queries', ledger_errors=True,
        json={'operation': 'preview_category_classification', 'workspace': 'personal',
              'parameters': {'categoryId': selected['id'], 'kind': args.category_kind}}), identifier, error)
    if preview['revision'] != observed['revision'] or preview['data']['categoryId'] != selected['id']:
        raise error('Category changed during preview. Inspect it again; nothing was submitted.')
    if args.dry_run:
        return {**preview, 'budget': args.target, 'dryRun': True}
    expected = deepcopy(observed['data'])
    for group in expected:
        for row in group['children']:
            if row['id'] == selected['id']:
                if args.category_kind != 'expense':
                    row['kind'] = args.category_kind
                else:
                    row.pop('kind', None)
    original = intent(args, observed, 'classify_category', {'categoryId': selected['id'], 'kind': args.category_kind})

    def verify(verified):
        if verified['data'] != expected:
            raise error('Saved hierarchy differs from the explicit category classification.')
        return next(row for group in verified['data'] for row in group['children'] if row['id'] == selected['id'])

    return saved_edit(connection, identifier, prefix, args, exchange, error, original, observed, read, verify)
