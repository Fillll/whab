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
        if merchants:
            store.db.create_function('whab_casefold', 1, str.casefold, deterministic=True)
            conditions.append('(' + ' OR '.join('instr(whab_casefold(merchant),?)>0' for _ in merchants) + ')')
            values.extend(text.casefold() for text in merchants)
        uncategorized = filters.get('uncategorized', False)
        if uncategorized:
            conditions.append('category_id IS NULL')
        if filters.get('posted'):
            conditions.append('pending=0')
        if filters.get('excludeTransfers'):
            conditions.extend(['is_transfer=0', 'inferred_transfer=0'])
        scope = ' FROM transactions WHERE ' + ' AND '.join(conditions)
        counts = Counter()
        by_source = Counter()
        by_currency = Counter()
        if uncategorized:
            for row in store.db.execute('SELECT account_id,source,pending,is_transfer,inferred_transfer,COUNT(*) AS count' + scope + ' GROUP BY account_id,source,pending,is_transfer,inferred_transfer', values):
                count = row['count']
                transfer = bool(row['is_transfer'] or row['inferred_transfer'])
                counts['total'] += count
                counts['pending'] += count * bool(row['pending'])
                counts['transferFlagged'] += count * transfer
                counts['postedNonTransfer'] += count * (not row['pending'] and not transfer)
                by_source[row['source']] += count
                by_currency[accounts[row['account_id']]['currency']] += count
        limit = filters.get('limit')
        tail = ' ORDER BY date DESC,id DESC' + (' LIMIT ?' if limit is not None else '')
        rows = []
        response_bytes = len(json.dumps(filters, ensure_ascii=False).encode()) + 512
        for row in store.db.execute('SELECT *' + scope + tail, [*values, *([limit] if limit is not None else [])]):
            account = accounts[row['account_id']]
            category = categories.get(row['category_id'], {})
            transaction = public_transaction({
                'id': row['id'], 'accountId': row['account_id'], 'date': row['date'],
                'merchant': row['merchant'], 'merchantLogo': row['merchant_logo'], 'amount': row['amount'],
                'categoryId': row['category_id'], 'pending': bool(row['pending']), 'reviewed': bool(row['reviewed']),
                'source': row['source'], 'note': row['note'], 'isTransfer': bool(row['is_transfer']),
                'inferredTransfer': bool(row['inferred_transfer']), 'currency': account['currency'],
                **({'categoryAgent': row['category_agent']} if row['category_agent'] is not None else {}),
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
