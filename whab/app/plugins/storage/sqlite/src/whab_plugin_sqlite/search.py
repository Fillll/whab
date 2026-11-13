"""Complete public CLI selections and counts from one SQLite snapshot."""

import json
from collections import Counter

from whab.domain.errors import StoreError
from whab.domain.transactions import public_transaction


def search_transactions(store, workspace, filters):
    with store._snapshot():
        store._require_workspace(workspace)
        accounts = {row['id']: row for row in store.accounts(workspace)}
        categories = {row['id']: row for row in store.categories(workspace)}
        conditions = ['workspace=?', 'deleted=0']
        values = [workspace]
        for field, operator, value in [('date', '>=', filters.get('since')), ('date', '<=', filters.get('until'))]:
            if value is not None:
                conditions.append(f'{field}{operator}?')
                values.append(value)
        if filters.get('month'):
            conditions.extend(['date>=?', 'date<?'])
            values.extend([filters['month'] + '-01', filters['month'] + '-32'])
        merchants = filters.get('merchant', [])
        candidates = None
        if merchants:
            from .merchant_index import selection
            conditions.append('id IN (SELECT value FROM json_each(?))')
            candidates = selection(store, workspace, merchants)
            values.append(json.dumps(candidates, separators=(',', ':')))
        uncategorized = filters.get('uncategorized', False)
        if uncategorized:
            conditions.append('category_id IS NULL')
        if filters.get('posted'):
            conditions.append('pending=0')
        if filters.get('excludeTransfers'):
            conditions.extend(['is_transfer=0', 'inferred_transfer=0'])
        scope = ' WHERE ' + ' AND '.join(conditions)
        table = 'transactions'
        if candidates is not None and len(candidates) <= 512:
            # The date-order index can otherwise walk the entire register before
            # finding an old merchant. Bound lookup work by the candidate set.
            table += ' INDEXED BY sqlite_autoindex_transactions_1'
        elif uncategorized and not merchants and store.db.execute("SELECT 1 FROM sqlite_schema WHERE type='index' AND name='tx_uncategorized_recent'").fetchone():
            table += ' INDEXED BY tx_uncategorized_recent'
        counts = Counter()
        by_source = Counter()
        by_currency = Counter()
        if uncategorized:
            count_table = table if candidates is not None else 'transactions'
            for row in store.db.execute('SELECT account_id,source,pending,is_transfer,inferred_transfer,COUNT(*) AS count FROM ' + count_table + scope + ' GROUP BY account_id,source,pending,is_transfer,inferred_transfer', values):
                count = row['count']
                transfer = bool(row['is_transfer'] or row['inferred_transfer'])
                counts['total'] += count
                counts['pending'] += count * bool(row['pending'])
                counts['transferFlagged'] += count * transfer
                counts['postedNonTransfer'] += count * (not row['pending'] and not transfer)
                by_source[row['source']] += count
                by_currency[accounts[row['account_id']]['currency']] += count
        limit = min(filters['limit'], 2**63 - 1) if filters.get('limit') is not None else None
        tail = ' ORDER BY date DESC,id DESC' + (' LIMIT ?' if limit is not None else '')
        rows = []
        response_bytes = len(json.dumps(filters, ensure_ascii=False).encode()) + 512
        for row in store.db.execute('SELECT * FROM ' + table + scope + tail, [*values, *([limit] if limit is not None else [])]):
            account = accounts[row['account_id']]
            category = categories.get(row['category_id'], {})
            transaction = public_transaction({
                'id': row['id'], 'accountId': row['account_id'], 'date': row['date'],
                'merchant': row['merchant'], 'merchantLogo': row['merchant_logo'], 'amount': row['amount'],
                'categoryId': row['category_id'], 'pending': bool(row['pending']), 'reviewed': bool(row['reviewed']),
                'source': row['source'], 'note': row['note'], 'isTransfer': bool(row['is_transfer']),
                'inferredTransfer': bool(row['inferred_transfer']), 'currency': account['currency'],
                **({'categoryAgent': row['category_agent']} if row['category_agent'] is not None else {}),
                **({'transferPeerId': row['transfer_peer_id']} if row['transfer_peer_id'] is not None else {}),
            })
            transaction['accountName'] = account['name']
            if not uncategorized:
                transaction.update(categoryName=category.get('name'), categoryGroup=category.get('group'))
            if filters.get('maxBytes') is not None:
                response_bytes += len(json.dumps(transaction, ensure_ascii=False, separators=(',', ':')).encode()) + 1
                if response_bytes > filters['maxBytes']:
                    raise StoreError('Transaction results exceed 16 MiB. Restrict dates or merchant selection.')
            rows.append(transaction)
        if uncategorized:
            return {**{name: counts[name] for name in ('total', 'pending', 'transferFlagged', 'postedNonTransfer')},
                    'returned': len(rows), 'bySource': [{'source': key, 'count': value} for key, value in sorted(by_source.items())],
                    'byCurrency': [{'currency': key, 'count': value} for key, value in sorted(by_currency.items())], 'data': rows}
        return {'complete': True, 'total': len(rows), 'returned': len(rows),
                'filters': {name: filters.get(name) for name in ('since', 'until', 'month')}
                | {'merchant': merchants, 'merchantMatch': 'case-insensitive-literal-substring-any'}, 'data': rows}
