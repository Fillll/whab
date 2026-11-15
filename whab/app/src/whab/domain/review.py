"""Classification prerequisite for explicit review; legacy reads never rewrite data."""


def review_status(transaction, categories):
    """Expense/Income/Transfer/Opening Balance children or confirmed pairs qualify; hints do not."""
    category = categories.get(transaction.get('categoryId'))
    classification = category.get('kind', 'expense') if category else 'transfer' if transaction.get('transferPeerId') else None
    reason = ('Deleted transactions cannot be reviewed.' if transaction.get('deleted') else
              'Pending transactions cannot be reviewed yet.' if transaction.get('pending') else
              'Choose an Expense, Income, Transfer or Opening Balance category before marking reviewed.' if classification is None else None)
    return {'classification': classification, 'eligible': reason is None, 'reason': reason}


def effective_transfer(transaction, categories):
    kind = categories.get(transaction.get('categoryId'), {}).get('kind')
    return kind != 'opening_balance' and bool(transaction.get('isTransfer') or kind == 'transfer')
