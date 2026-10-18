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

test('multiple accounts use repeated stable IDs and a single account keeps its readable path', () => {
  const navigate = navigation('https://localhost:5173/budgets/family-budget/transactions/2026-10/usd?accounts=MixedCase&accounts=Other%26ID&period=custom&start_date=2026-09-10&end_date=2026-10-10&review=reviewed', {
    budgetUrl: '/budgets/family-budget', selectedAccountIds: ['MixedCase', 'Other&ID'],
    accountSlugs: {MixedCase: 'daily-checking', 'Other&ID': 'savings'},
  });
  const multiple = navigate({accounts: ['Other&ID', 'MixedCase', 'Other&ID']});
  assert.equal(multiple.pathname, '/budgets/family-budget/transactions/2026-10/usd');
  assert.deepEqual(multiple.searchParams.getAll('accounts'), ['Other&ID', 'MixedCase']);
  assert.equal(multiple.searchParams.has('account'), false);
  for (const [name, value] of [['period', 'custom'], ['start_date', '2026-09-10'], ['end_date', '2026-10-10'], ['review', 'reviewed']]) {
    assert.equal(multiple.searchParams.get(name), value);
  }
  const single = navigate({accounts: ['MixedCase']});
  assert.equal(single.pathname, '/budgets/family-budget/daily-checking/transactions/2026-10/usd');
  assert.equal(single.searchParams.has('accounts'), false);
  assert.equal(single.searchParams.has('account'), false);
  const all = navigate({accounts: []});
  assert.equal(all.pathname, '/budgets/family-budget/transactions/2026-10/usd');
  assert.equal(all.searchParams.has('accounts'), false);
  assert.equal(all.searchParams.has('account'), false);
});

test('relative date presets replace legacy all dates and custom bounds while retaining account and review scope', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=first&all_dates=true&period=custom&start_date=2026-09-10&end_date=2026-10-10&review=needs-review');
  for (const period of ['current-month', 'previous-month']) {
    const preset = navigate({period, month: 'current-month'});
    assert.equal(preset.pathname, '/budgets/home/transactions/current-month/usd');
    assert.equal(preset.searchParams.get('period'), period);
    assert.equal(preset.searchParams.get('account'), 'first');
    assert.equal(preset.searchParams.get('review'), 'needs-review');
    for (const key of ['all_dates', 'start_date', 'end_date']) assert.equal(preset.searchParams.has(key), false);
  }
});

test('custom dates allow either open boundary and changing month clears the custom period', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=first&all_dates=true&start_date=2026-01-01&end_date=2026-12-31&review=reviewed');
  for (const [startDate, endDate] of [['2026-09-10', ''], ['', '2026-10-10'], ['', '']]) {
    const custom = navigate({period: 'custom', startDate, endDate});
    assert.equal(custom.searchParams.get('period'), 'custom');
    assert.equal(custom.searchParams.get('start_date'), startDate || null);
    assert.equal(custom.searchParams.get('end_date'), endDate || null);
    assert.equal(custom.searchParams.get('review'), 'reviewed');
    assert.equal(custom.searchParams.has('all_dates'), false);
  }
  const month = navigate({month: '2026-09'});
  for (const key of ['period', 'start_date', 'end_date', 'all_dates']) assert.equal(month.searchParams.has(key), false);
  assert.equal(month.searchParams.get('account'), 'first');
  assert.equal(month.searchParams.get('review'), 'reviewed');
});

test('review status is shareable and unrelated pages clear transaction-only filters', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?accounts=first&accounts=second&period=custom&start_date=2026-09-10&review=needs-review');
  assert.equal(navigate({review: 'reviewed'}).searchParams.get('review'), 'reviewed');
  assert.equal(navigate({review: 'all'}).searchParams.has('review'), false);
  for (const page of ['budget', 'accounts', 'insights']) {
    const destination = navigate({page});
    for (const key of ['account', 'accounts', 'period', 'start_date', 'end_date', 'review']) {
      assert.equal(destination.searchParams.has(key), false);
    }
  }
});

test('a relative preset drops stale custom date drafts merged from an earlier pending choice', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=first&period=custom&start_date=2026-09-10&end_date=2026-10-10');
  const preset = navigate({period: 'previous-month', month: 'current-month', startDate: '2026-09-10', endDate: '2026-10-10'});
  assert.equal(preset.searchParams.get('period'), 'previous-month');
  assert.equal(preset.searchParams.has('start_date'), false);
  assert.equal(preset.searchParams.has('end_date'), false);
});

test('register filters reset pagination while retaining the complete-history search', () => {
  const navigate = navigation('https://localhost:5173/budgets/home/transactions/2026-10/usd?accounts=first&accounts=second&tx_page=12&q=earlier+memo');
  for (const changes of [{account: 'first'}, {accounts: []}, {month: '2026-09'}, {currency: 'EUR'},
    {period: 'previous-month'}, {review: 'needs-review'}, {q: 'new search'}]) {
    const target = navigate(changes);
    assert.equal(target.searchParams.has('tx_page'), false);
    assert.equal(target.searchParams.get('q'), changes.q || 'earlier memo');
  }
  assert.equal(navigate({q: ''}).searchParams.has('q'), false);
  for (const page of ['accounts', 'budget', 'insights']) {
    const target = navigate({page});
    assert.equal(target.searchParams.has('q'), false);
    assert.equal(target.searchParams.has('tx_page'), false);
  }
});
