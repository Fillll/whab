"""Explicit financial category treatment over guarded public operations."""

import argparse
from copy import deepcopy

from .record_edits import RECOVERY, edit_options, intent, saved_edit
from .transaction_commands import checked

GUIDE = """Expense, Income, Transfer and Opening Balance are financial types independent of
display/group names. Use --type opening_balance for Opening Balance.
Review is explicit and requires classification. Clearing classification unreviews.
Transfer classification/review requires exactly two valid reciprocal legs; a bank
hint or isolated Transfer category is insufficient. Use trnx match-transfers for
explicit pairs, and transfer-audit/repair-transfers for guarded legacy cleanup.
Posted external inflows in eligible on-budget non-credit accounts fund Ready to
Assign once. Pending records, transfers, off-budget accounts and credit-card
inflows do not fund income. Valid transfers have separate signed transferFunding
for eligible budget movement, including differing scopes/posting months. Income reversals reduce income; expense-category refunds
reduce spending. Amounts, balances, merchant whitespace, notes, original/import
information, categories' IDs/parents/currencies and reviews are preserved.

Income, Transfer and Opening Balance categories have target zero and
NO monthly assignments, including zero rows. Conversion refuses existing targets/assignments;
create a new technical child
instead to preserve an old expense envelope. A parent can mix financial types.
Missing type on older categories remains Expense. No automatic migration, merchant
inference, transaction relabeling, review change or bank synchronization occurs.
Legacy uncategorized inflows keep their existing funding behavior for compatibility;
summary.classifiedIncome and summary.unclassifiedIncome separate the contributions.

Opening Balance records existing cash or debt, not ordinary income or spending.
Posted opening cash in eligible on-budget non-credit accounts contributes signed
summary.openingFunding in its transaction month; later months carry it forward
without adding it again. Prior-to-budget entries are already carried in. Credit
debt and off-budget balances never become opening funding. Stale/unknown account
balances remain excluded; classification does not verify imported estimates. Existing zero-amount
opening entries can be classified and explicitly reviewed; ordinary create/edit
amounts must still be nonzero. No category is created or inferred on upgrade.

Preview FIRST: changing a referenced category's type changes historical income,
opening funding, spending and Ready to Assign. --dry-run shows the exact category before/after and
summary impacts for represented ledger months, without any commit or save.
Normal execution is noninteractive, one guarded commit/save with undo/redo history.
Exact retries keep the identical --type, --revision, --request-id and audit metadata.

Examples (fictional):
  whab "My budget" category create Salary --parent Finance --type income
  whab "My budget" category classify "Finance / Salary" --type income --dry-run
  whab "My budget" category classify "Finance / Salary" --type income --agent Codex
  whab "My budget" trnx categorize TX1 TX2 --category "Finance / Salary" --agent Codex
  whab "My budget" category create "Opening Balance" --parent Finance --type opening_balance --agent Codex
  whab "My budget" category classify "Finance / Opening Balance" --type opening_balance --dry-run
  whab "My budget" trnx categorize OPENING_TX --category "Finance / Opening Balance" --agent Codex
Categorize supports 1–100 explicit IDs and --dry-run; mixed edits use trnx edit-many.
Assignment never auto-reviews; removing the last valid classification makes
unreviewed. A valid pair keeps review eligibility when its optional Transfer
category label is cleared.
Set review explicitly with trnx review; inspect legacy flags with trnx review-audit.
"""


def add_classify_parser(actions):
    parser = actions.add_parser('classify', help='Set Expense/Income/Transfer/Opening Balance treatment without replacing a category',
        description='Explicit financial classification of one existing child. Preview historical totals before applying.',
        epilog=GUIDE + '\n' + RECOVERY, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('category', help='Stable child ID, unique exact name or Group / Category; no fuzzy matching')
    parser.add_argument('--type', dest='category_kind', required=True, choices=('expense', 'income', 'transfer', 'opening_balance'), help='Financial treatment; independent of parent/name/review')
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
