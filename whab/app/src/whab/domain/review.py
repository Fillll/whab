"""Classification prerequisite for explicit review; legacy reads never rewrite data."""


def review_status(transaction, categories):
    """Expense/Income/Transfer children or confirmed pairs qualify; hints do not."""
    category = categories.get(transaction.get('categoryId'))
    classification = category.get('kind', 'expense') if category else 'transfer' if transaction.get('transferPeerId') else None
    reason = ('Deleted transactions cannot be reviewed.' if transaction.get('deleted') else
              'Pending transactions cannot be reviewed yet.' if transaction.get('pending') else
              'Choose an Expense, Income or Transfer category before marking reviewed.' if classification is None else None)
    return {'classification': classification, 'eligible': reason is None, 'reason': reason}


def effective_transfer(transaction, categories):
    return bool(transaction.get('isTransfer') or categories.get(transaction.get('categoryId'), {}).get('kind') == 'transfer')
