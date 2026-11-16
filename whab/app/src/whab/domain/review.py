"""Classification prerequisite for explicit review; legacy reads never rewrite data."""


def review_status(transaction, categories, *, transactions=None, accounts=None, incoming=None):
    """Expense/Income/Transfer/Opening Balance children or confirmed pairs qualify; hints do not."""
    category = categories.get(transaction.get('categoryId'))
    classification = category.get('kind', 'expense') if category else 'transfer' if transaction.get('transferPeerId') else None
    reason = ('Deleted transactions cannot be reviewed.' if transaction.get('deleted') else
              'Pending transactions cannot be reviewed yet.' if transaction.get('pending') else
              'Choose an Expense, Income, Transfer or Opening Balance category before marking reviewed.' if classification is None else None)
    if reason is None and (classification == 'transfer' or effective_transfer(transaction, categories)):
        from .transfer_matching import valid_pair
        verified = (transaction.get('transferPairValid') is True if transactions is None or accounts is None
                    else valid_pair(transaction, transactions, accounts, categories, incoming=incoming))
        if not verified:
            reason = 'A Transfer requires exactly two valid reciprocal legs. Match the counterpart before marking reviewed.'
    return {'classification': classification, 'eligible': reason is None, 'reason': reason}


def effective_transfer(transaction, categories):
    kind = categories.get(transaction.get('categoryId'), {}).get('kind')
    return kind != 'opening_balance' and bool(transaction.get('isTransfer') or kind == 'transfer')
