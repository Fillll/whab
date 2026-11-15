const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const directory = path.join(__dirname, '../src/whab_plugin_web/static');
const client = fs.readFileSync(path.join(directory, 'client.js'), 'utf8');
const register = fs.readFileSync(path.join(directory, 'transaction-register.js'), 'utf8');
const runtime = {URL};
vm.runInNewContext(client.slice(client.indexOf('export function parsePageRoute'), client.indexOf('export function apiUrl')).replaceAll('export function ', 'function '), runtime);
vm.runInNewContext(register.slice(register.indexOf('export function transactionControlUrl'), register.indexOf('export function initTransactionRegister')).replaceAll('export function ', 'function '), runtime);

test('short and legacy USD routes resolve identically while other currencies remain explicit', () => {
  for (const account of ['', 'cash-wallet/']) {
    const base = `https://localhost:5173/budgets/fictional/${account}transactions/current-month`;
    assert.deepEqual(JSON.parse(JSON.stringify(runtime.parsePageRoute(new URL(base)))),
      JSON.parse(JSON.stringify(runtime.parsePageRoute(new URL(base + '/usd')))));
    assert.equal(runtime.parsePageRoute(new URL(base)).currency, 'usd');
    assert.equal(runtime.parsePageRoute(new URL(base + '/eur')).currency, 'eur');
  }
  for (const path of ['/budgets/fictional/budget/current-month', '/budgets/fictional/transactions/current-month/USD', '/budgets/fictional/cash-wallet/accounts/current-month']) {
    assert.equal(runtime.parsePageRoute(new URL(path, 'https://localhost:5173')), null);
  }
});

test('month controls retain account, filters and currency for short and old bookmarks', () => {
  for (const suffix of ['', '/usd', '/eur']) {
    const base = `https://localhost:5173/budgets/fictional/cash-wallet/transactions/2026-11${suffix}?accounts=one&accounts=two&sort=amount:desc&q=Train&tx_page=3`;
    const link = {hasAttribute: () => false, href: `https://localhost:5173/budgets/fictional/transactions/2026-10${suffix}`};
    const target = runtime.transactionControlUrl(base, link);
    assert.equal(target.pathname, `/budgets/fictional/cash-wallet/transactions/2026-10${suffix === '/eur' ? '/eur' : ''}`);
    assert.deepEqual(target.searchParams.getAll('accounts'), ['one', 'two']);
    assert.deepEqual(target.searchParams.getAll('sort'), ['amount:desc']);
    assert.equal(target.searchParams.get('q'), 'Train');
    assert.equal(target.searchParams.has('tx_page'), false);
  }
});

test('Accounts addresses have no period or currency and legacy bookmarks parse to the same view', () => {
  const base = new URL('https://localhost:5173/budgets/fictional/accounts');
  const expected = {budget: 'fictional', accountSlug: null, page: 'accounts', month: null, currency: null};
  for (const suffix of ['', '/current-month/usd', '/2026-10/eur']) {
    assert.deepEqual(JSON.parse(JSON.stringify(runtime.parsePageRoute(new URL(base.href + suffix)))), expected);
  }
  assert.equal(runtime.pagePath('/budgets/fictional', 'accounts', '2026-10', 'EUR', 'wallet'), '/budgets/fictional/accounts');
  for (const suffix of ['/current-month', '/usd', '/2026-10/eur/extra']) {
    assert.equal(runtime.parsePageRoute(new URL(base.href + suffix)), null);
  }
});
