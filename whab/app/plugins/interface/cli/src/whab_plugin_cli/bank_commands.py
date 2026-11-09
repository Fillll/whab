"""Shared bank-management intent parsing for both local console entry points."""

import argparse
import json
from urllib.parse import quote

from .identifiers import host_id


def add_bank_parser(commands):
    bank = commands.add_parser('bank', help='Review accounts, investments and bank updates',
        description='Manage existing bank connections in a local personal budget. Requires an enabled bank plugin. Investments are read-only positions/activity; they do not import investment activity into the transaction ledger.',
        epilog='Actions and required options:\n  connections PROVIDER                 read connection IDs and saved mappings\n  preview PROVIDER --item ID            read provider account IDs before linking\n  configure PROVIDER --item ID --accounts JSON\n  sync PROVIDER [--item ID]             import now using saved mappings\n  schedule PROVIDER                    read policy and due status\n  schedule PROVIDER --every-hours 1     save 1/3/6-hour cadence\n  schedule PROVIDER --manual            disable automatic/webhook imports\n  background PROVIDER --enable         register device/environment hourly wakeups\n  background PROVIDER --disable        remove device/environment wakeups\n  investments PROVIDER --item ID [--bank-account PROVIDER_ACCOUNT_ID]\n\nExample (replace fictional IDs, review preview first):\n  whab "My budget" bank connections plaid\n  whab "My budget" bank preview plaid --item CONNECTION_ID\n  whab "My budget" bank configure plaid --item CONNECTION_ID --accounts \'[{"bankAccountId":"PROVIDER_ACCOUNT_ID","accountId":"WHAB_ACCOUNT_ID","since":"2026-11-07"}]\'\n  whab "My budget" bank sync plaid --item CONNECTION_ID\n\nconfigure saves mappings; sync is a separate write. accountId:null creates a new\naccount; since:null selects all available history. Dates are inclusive and must be\nchosen explicitly; omitted provider accounts are skipped. Existing destinations\nrequire compatible currency/type; configuration does not deduplicate YNAB imports.\nSchedule is per budget; background wakeups are per runtime home/environment and\nonly run due checks. Manual mode retains explicit sync. No new Link flow is opened.\nInvestment --since/--until filter activity, not current positions. Missing cost basis,\nacquisition dates or realized returns remain unavailable; unrealized gain versus\nreported cost basis is not total investment return. Use whab api describe --plugin plaid\n--operation bank.investments for full schemas and provider limits.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    bank.add_argument('action', choices=('connections', 'shared', 'attach', 'detach', 'disconnect', 'preview', 'configure', 'sync', 'schedule', 'background', 'investments'), help='shared lists device connections and all budget attachments; attach/detach affect this budget; disconnect revokes the bank for all attached budgets')
    bank.add_argument('plugin', help='Enabled bank provider, for example plaid')
    bank.add_argument('--item', help='Connection ID from bank connections')
    bank.add_argument('--bank-account', help='Provider account ID from bank preview or investments; omit for all investment accounts')
    bank.add_argument('--since', help='Inclusive investment activity start date YYYY-MM-DD; defaults to two years before --until')
    bank.add_argument('--until', help='Inclusive investment activity end date YYYY-MM-DD; defaults to today')
    bank.add_argument('--accounts', help='JSON array of {bankAccountId, accountId: null or WHAB ID, since: null or YYYY-MM-DD}; null destination creates a new account')
    bank.add_argument('--affected-budgets', help='disconnect only: exact sorted JSON array of every affected budgetId from bank shared; revokes the underlying bank connection')
    cadence = bank.add_mutually_exclusive_group()
    cadence.add_argument('--every-hours', type=int, choices=(1, 3, 6), help='schedule only: save periodic cadence; omit cadence flags to read policy')
    cadence.add_argument('--manual', action='store_true', help='Disable automatic and webhook-triggered imports')
    background = bank.add_mutually_exclusive_group()
    background.add_argument('--enable', action='store_true', help='Register macOS hourly wakeups; the saved cadence chooses which checks are due')
    background.add_argument('--disable', action='store_true', help='background only: remove device/environment hourly wakeups')
    return bank


def bank_request(args, budget):
    prefix = '/budgets/' + quote(budget, safe='') + '/api/banks/' + quote(host_id(args.plugin), safe='')
    action = args.action
    if action == 'connections':
        return 'GET', prefix + '/items', {}
    if action in {'preview', 'configure', 'investments', 'attach', 'detach', 'disconnect'} and not args.item:
        raise ValueError('Choose a connection ID with --item.')
    if action == 'shared':
        return 'GET', prefix + '/connections', {}
    if action == 'attach':
        return 'POST', prefix + '/attachments', {'json': {'itemId': args.item}}
    if action == 'detach':
        return 'DELETE', prefix + '/attachments/' + quote(args.item, safe=''), {'json': {}}
    if action == 'disconnect':
        if args.affected_budgets is None or len(args.affected_budgets.encode()) > 128 * 1024:
            raise ValueError('Review bank shared and supply every affected budget ID with --affected-budgets JSON.')
        budgets = json.loads(args.affected_budgets)
        if type(budgets) is not list or any(type(value) is not str for value in budgets) or budgets != sorted(set(budgets)):
            raise ValueError('Supply a sorted JSON array of unique affected budget IDs.')
        return 'DELETE', prefix + '/items/' + quote(args.item, safe=''), {'json': {'budgetIds': budgets}}
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
