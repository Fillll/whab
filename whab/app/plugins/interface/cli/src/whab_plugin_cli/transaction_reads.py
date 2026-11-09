"""Console projections of public transactions; no storage or mutation access."""

import argparse
from collections import Counter

from whab.plugins import format_money


def positive_limit(value):
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError('Limit must be a positive integer; omit it for all matches.')
    return count


def add_uncategorized_parser(reads):
    command = reads.add_parser('uncategorized', aliases=['uncategorised'],
        help='List transactions with no assigned category, independently of review status',
        description='Read uncategorized transactions from this budget. Includes pending rows, transfers, inflows and off-budget accounts unless explicitly filtered. Deleted rows are excluded.',
        epilog='Examples:\n  whab "My budget" trnx uncategorized\n  whab "My budget" trnx uncategorized --limit 10 --format table\n  whab "My budget" trnx uncategorized --posted --exclude-transfers\n\nDefault: all history, newest dates first, JSON with total/returned counts and account names. --month restricts history. --limit changes returned rows, not full-match counts. Order within a date uses the transaction ID, not an inferred time. Amounts are integer currency minor units in JSON; table output formats each currency separately. This never categorizes, reviews or syncs transactions.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    command.add_argument('--month', help='Limit to YYYY-MM; default: all history')
    command.add_argument('--limit', type=positive_limit, help='Return at most this many matches; default: all')
    command.add_argument('--posted', action='store_true', help='Exclude pending transactions')
    command.add_argument('--exclude-transfers', action='store_true', help='Exclude explicit and inferred transfers')
    command.add_argument('--format', choices=('json', 'table'), default='json', help='Output format (default: json); JSON retains complete fields')


def is_transfer(row):
    return bool(row.get('isTransfer') or row.get('inferredTransfer'))


def uncategorized_report(result, accounts, args):
    names = {row['id']: row['name'] for row in accounts['data']}
    rows = [row for row in result['data'] if row.get('categoryId') is None and not row.get('deleted')
            and not (args.posted and row.get('pending'))
            and not (args.exclude_transfers and is_transfer(row))]
    rows.sort(key=lambda row: (row['date'], row['id']), reverse=True)
    sample = rows[:args.limit]
    return {'budgetId': result['budgetId'], 'revision': result['revision'],
            'total': len(rows), 'returned': len(sample),
            'pending': sum(bool(row.get('pending')) for row in rows),
            'transferFlagged': sum(is_transfer(row) for row in rows),
            'postedNonTransfer': sum(not row.get('pending') and not is_transfer(row) for row in rows),
            'bySource': [{'source': source, 'count': count} for source, count in sorted(Counter(row.get('source', 'unknown') for row in rows).items())],
            'byCurrency': [{'currency': currency, 'count': count} for currency, count in sorted(Counter(row['currency'] for row in rows).items())],
            'data': [{**row, 'accountName': names.get(row['accountId'], row['accountId'])} for row in sample]}


def terminal_text(value, limit):
    text = ''.join(char if char.isprintable() else f'\\u{ord(char):04x}' for char in str(value))
    return text if limit is None or len(text) <= limit else text[:limit - 1] + '…'


def transaction_table(result):
    lines = [f"Uncategorized: {result['total']} matched, {result['returned']} shown",
             f"Pending: {result['pending']} | Transfer-flagged: {result['transferFlagged']} | Posted non-transfer: {result['postedNonTransfer']}"]
    if not result['data']:
        return '\n'.join([*lines, 'No matching transactions.'])
    rows = [['Date', 'Merchant', 'Account', 'Amount', 'Status', 'Source']]
    for row in result['data']:
        rows.append([row['date'], row['merchant'], row['accountName'], format_money(row['amount'], row['currency']),
                     'Pending' if row.get('pending') else 'Posted', row.get('source', 'unknown')])
    rows = [[terminal_text(value, limit) for value, limit in zip(row, (10, 36, 28, 20, 7, 14), strict=True)] for row in rows]
    widths = [max(len(row[column]) for row in rows) for column in range(6)]
    lines += ['Positive amounts are outflows; negative amounts are inflows. Long text is shortened here; JSON retains full values.']
    lines += ['  '.join(value.rjust(width) if column == 3 else value.ljust(width)
                       for column, (value, width) in enumerate(zip(row, widths, strict=True))) for row in rows]
    return '\n'.join(lines)
