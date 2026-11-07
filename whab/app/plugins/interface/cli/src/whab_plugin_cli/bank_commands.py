"""Shared bank-management intent parsing for both local console entry points."""

import json
from urllib.parse import quote

from .identifiers import host_id


def add_bank_parser(commands):
    bank = commands.add_parser('bank', help='Review accounts, investments and bank updates',
        description='Manage bank connections and read investment positions. Investment reports include value, reported cost basis and unrealized returns; missing dates or realized returns remain unavailable. Read bank.investments with describe for the full report contract.')
    bank.add_argument('action', choices=('connections', 'preview', 'configure', 'sync', 'schedule', 'background', 'investments'))
    bank.add_argument('plugin', help='Enabled bank provider, for example plaid')
    bank.add_argument('--item', help='Connection ID from bank connections')
    bank.add_argument('--bank-account', help='Provider account ID from bank preview or investments; omit for all investment accounts')
    bank.add_argument('--since', help='Inclusive investment activity start date YYYY-MM-DD; defaults to two years before --until')
    bank.add_argument('--until', help='Inclusive investment activity end date YYYY-MM-DD; defaults to today')
    bank.add_argument('--accounts', help='JSON array of {bankAccountId, accountId: null or WHAB ID, since: null or YYYY-MM-DD}; null destination creates a new account')
    cadence = bank.add_mutually_exclusive_group()
    cadence.add_argument('--every-hours', type=int, choices=(1, 3, 6))
    cadence.add_argument('--manual', action='store_true', help='Disable automatic and webhook-triggered imports')
    background = bank.add_mutually_exclusive_group()
    background.add_argument('--enable', action='store_true', help='Register macOS hourly wakeups; the saved cadence chooses which checks are due')
    background.add_argument('--disable', action='store_true')
    return bank


def bank_request(args, budget):
    prefix = '/budgets/' + quote(budget, safe='') + '/api/banks/' + quote(host_id(args.plugin), safe='')
    action = args.action
    if action == 'connections':
        return 'GET', prefix + '/items', {}
    if action in {'preview', 'configure', 'investments'} and not args.item:
        raise ValueError('Choose a connection ID with --item.')
    if action == 'investments':
        return 'POST', prefix + '/investments', {'json': {'itemId': args.item, 'bankAccountId': args.bank_account, 'startDate': args.since, 'endDate': args.until}}
    if action == 'preview':
        return 'POST', prefix + '/sync-preview', {'json': {'itemId': args.item}}
    if action == 'configure':
        if args.accounts is None or len(args.accounts.encode()) > 128 * 1024:
            raise ValueError('Supply a bounded JSON account selection with --accounts.')
        accounts = json.loads(args.accounts)
        if type(accounts) is not list:
            raise ValueError('Supply a JSON array of account selections.')
        return 'PUT', prefix + '/sync-settings', {'json': {'itemId': args.item, 'accounts': accounts}}
    if action == 'sync':
        return 'POST', prefix + '/sync', {'json': {'itemId': args.item}}
    if action == 'schedule':
        if args.manual or args.every_hours is not None:
            return 'PUT', prefix + '/schedule', {'json': {'interval_seconds': None if args.manual else args.every_hours * 3600}}
        return 'GET', prefix + '/schedule', {}
    if not (args.enable or args.disable):
        raise ValueError('Choose --enable or --disable for background checks.')
    return 'POST', prefix + '/schedule/background', {'json': {'enabled': args.enable}}


def bank_error(raw):
    """Only authored, bounded host validation text reaches the console."""
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    message = result.get('error') if type(result) is dict else None
    return message if type(message) is str and 0 < len(message) <= 1000 else None
