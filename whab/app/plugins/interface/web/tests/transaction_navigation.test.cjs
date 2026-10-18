/* Run with node --test plugins/interface/web/tests/transaction_navigation.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/client.js'), 'utf8');
function navigation(href, extra = {}) {
  const context = {
    URL, location: {href},
    document: {getElementById(id) {
      return id === 'whab-context' ? {textContent: JSON.stringify({
        budgetUrl: '/budgets/home', page: 'transactions', monthToken: '2026-10', currency: 'USD',
        ...extra,
      })} : {};
    }},
  };
  vm.runInNewContext(source.replaceAll('export function ', 'function ')
    .replaceAll('export async function ', 'async function '), context);
  return overrides => context.pageUrl(overrides);
}

test('account register survives month navigation while switching to the selected month', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=MixedCase&all_dates=true');
  const month = navigate({month: '2026-09'});
  assert.equal(month.pathname, '/budgets/home/transactions/2026-09/usd');
  assert.equal(month.searchParams.get('account'), 'MixedCase');
  assert.equal(month.searchParams.has('all_dates'), false);
  const toggle = navigate({allDates: false});
  assert.equal(toggle.searchParams.get('account'), 'MixedCase');
  assert.equal(toggle.searchParams.has('all_dates'), false);
});

test('readable budget and account paths preserve stable account IDs while changing periods', () => {
  const navigate = navigation('https://localhost:5173/budgets/family-budget/daily-checking/transactions/2026-10/usd?all_dates=true', {
    budgetUrl: '/budgets/family-budget', selectedAccountId: 'MixedCase',
    accountSlugs: {MixedCase: 'daily-checking', 'Other&ID': 'savings'},
  });
  const month = navigate({month: '2026-09'});
  assert.equal(month.pathname, '/budgets/family-budget/daily-checking/transactions/2026-09/usd');
  assert.equal(month.searchParams.has('account'), false);
  assert.equal(month.searchParams.has('all_dates'), false);
  assert.equal(navigate({allDates: false}).pathname, '/budgets/family-budget/daily-checking/transactions/2026-10/usd');
  const account = navigate({account: 'Other&ID'});
  assert.equal(account.pathname, '/budgets/family-budget/savings/transactions/2026-10/usd');
  assert.equal(account.searchParams.get('all_dates'), 'true');
  assert.equal(navigate({account: 'all'}).pathname, '/budgets/family-budget/transactions/2026-10/usd');
  assert.equal(navigate({currency: 'JPY'}).pathname, '/budgets/family-budget/transactions/2026-10/jpy');
  assert.equal(navigate({page: 'budget'}).pathname, '/budgets/family-budget/budget/2026-10/usd');
});

test('legacy account queries become readable paths when the account slug is known', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=MixedCase&all_dates=true', {
    budgetUrl: '/budgets/family-budget', selectedAccountId: 'MixedCase',
    accountSlugs: {MixedCase: 'daily-checking'},
  });
  const url = navigate({allDates: true});
  assert.equal(url.pathname, '/budgets/family-budget/daily-checking/transactions/2026-10/usd');
  assert.equal(url.searchParams.has('account'), false);
  assert.equal(url.searchParams.get('all_dates'), 'true');
});

test('account filter changes and clears without losing all dates', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=first&all_dates=true#settings');
  const selected = navigate({account: 'Other&ID'});
  assert.equal(selected.searchParams.get('account'), 'Other&ID');
  assert.equal(selected.searchParams.get('all_dates'), 'true');
  assert.equal(selected.hash, '');
  assert.equal(navigate({account: 'all'}).searchParams.has('account'), false);
  assert.equal(navigate({allDates: true}).searchParams.get('all_dates'), 'true');
});

test('switching currency or leaving transactions clears the account scope', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=first&all_dates=true');
  const currency = navigate({currency: 'EUR'});
  assert.equal(currency.pathname, '/budgets/home/transactions/2026-10/eur');
  assert.equal(currency.searchParams.has('account'), false);
  assert.equal(currency.searchParams.get('all_dates'), 'true');
  for (const page of ['budget', 'accounts', 'insights']) {
    const target = navigate({page});
    assert.equal(target.searchParams.has('account'), false);
    assert.equal(target.searchParams.has('all_dates'), false);
  }
});
