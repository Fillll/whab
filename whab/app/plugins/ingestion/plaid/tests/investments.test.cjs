const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_plaid/static/connect.js'), 'utf8');
class Element {
  constructor(tag = 'div') {this.tag = tag; this.children = []; this.dataset = {}; this.value = ''; this.textContent = ''; this.hidden = false; this.isConnected = true; this.listeners = new Map();}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children = nodes; if (this.tag === 'select') this.value = nodes[0]?.value || '';}
  get options() {return this.children;}
  addEventListener(type, callback) {if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback);}
  async emit(type) {await Promise.all((this.listeners.get(type) || []).map(callback => callback({type, target: this})));}
  focus() {this.focused = true;}
  set innerHTML(_) {throw new Error('Provider text must never become HTML');}
}
function report() {
  return {asOf: '2026-11-07T12:00:00Z', providerUpdatedAt: null, startDate: '2024-11-07', endDate: '2026-11-07',
    accounts: [{id: 'account', name: 'Fictional IRA', mask: '1234'}],
    totals: [{currency: 'USD', value: 125000, costBasis: 100000, coveredPositions: 1, unrealizedGain: 25000, unrealizedReturnPercent: '25.00'}],
    positions: [{accountId: 'account', name: '<img src=x onerror=alert(1)>', ticker: 'FUND', quantity: '10', price: '125', priceAsOf: '2026-11-06', status: 'held', value: 125000, costBasis: 100000, currency: 'USD', unrealizedGain: 25000, unrealizedReturnPercent: '25.00', firstRecordedAddition: null}],
    activity: [{name: 'Fictional dividend', type: 'cash', subtype: 'dividend', date: '2026-11-04', amount: -82, fees: 0, currency: 'USD'}]};
}
function harness() {
  const selectors = ['heading', 'snapshot', 'start', 'end', 'load', 'error', 'totals', 'positions', 'activity', 'status', 'more', 'more-activity', 'back'];
  const fields = Object.fromEntries(selectors.map(name => [`[data-investment-${name}]`, new Element()]));
  const select = fields['[data-investment-account]'] = new Element('select');
  const view = new Element(); view.hidden = true; view.querySelector = selector => fields[selector];
  const connections = new Element();
  const provider = new Element(); provider.querySelector = selector => selector === '[data-plaid-investment-view]' ? view : connections;
  view.closest = () => provider;
  const open = new Element('button'); open.dataset.plaidInvestments = 'fictional-item'; open.closest = () => provider;
  for (const element of Object.values(fields)) element.closest = () => view;
  const buttons = Object.fromEntries(Object.entries(fields).map(([selector, element]) => [selector, [element]])); buttons['[data-plaid-investments]'] = [open];
  const requests = [], listeners = {};
  const document = {querySelectorAll: selector => buttons[selector] || [], querySelector: () => null, getElementById: () => null,
    addEventListener: (name, callback) => {listeners[name] = callback;}, dispatchEvent: () => false, createElement: tag => new Element(tag)};
  const context = {document, window: {alert() {}}, location: {href: 'https://localhost:5173/budgets/fictional/accounts/2026-11/usd', pathname: '/budgets/fictional/accounts/2026-11/usd'}, URL, Intl,
    sessionStorage: {getItem: () => null, removeItem() {}},
    fetch(url, options) {return new Promise(resolve => requests.push({url, options, reply: (value, ok = true) => resolve({ok, json: async () => value})}));}};
  vm.runInNewContext(source, context);
  return {fields, view, provider, connections, select, open, requests, listeners};
}
const text = element => [element.textContent, ...element.children.map(text)].join(' ');

test('investment action loads the addressed connection and renders text-only values and unknown dates', async () => {
  const h = harness(); const loading = h.open.emit('click');
  assert.equal(h.requests[0].url, '/budgets/fictional/api/plaid/investments');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {itemId: 'fictional-item', bankAccountId: null, startDate: null, endDate: null});
  h.requests[0].reply(report()); await loading;
  assert.equal(h.view.hidden, false); assert.equal(h.connections.hidden, true);
  assert.match(text(h.fields['[data-investment-positions]']), /<img src=x onerror=alert\(1\)>/);
  assert.match(text(h.fields['[data-investment-positions]']), /25.00%/);
  assert.match(text(h.fields['[data-investment-positions]']), /125 USD \/ unit · 2026-11-06/);
  assert.match(text(h.fields['[data-investment-positions]']), /Unavailable/);
  assert.match(text(h.fields['[data-investment-activity]']), /0.82/);
  assert.equal(h.fields['[data-investment-start]'].value, '2024-11-07');
});

test('account changes retain date filters and bind once when the page becomes ready again', async () => {
  const h = harness(); let loading = h.open.emit('click'); h.requests[0].reply(report()); await loading;
  h.listeners['whab:page-ready'](); h.listeners['whab:page-ready']();
  h.select.value = 'account'; h.fields['[data-investment-start]'].value = '2025-01-01';
  loading = h.select.emit('change'); assert.equal(h.requests.length, 2);
  assert.equal(JSON.parse(h.requests[1].options.body).bankAccountId, 'account');
  assert.equal(JSON.parse(h.requests[1].options.body).startDate, '2025-01-01');
  h.requests[1].reply(report()); await loading;
});

test('Back invalidates an in-flight report instead of restoring stale results', async () => {
  const h = harness(); const loading = h.open.emit('click');
  assert.equal(h.select.disabled, true);
  assert.equal(h.fields['[data-investment-start]'].disabled, true);
  await h.fields['[data-investment-back]'].emit('click'); h.requests[0].reply(report()); await loading;
  assert.equal(h.view.hidden, true); assert.equal(h.connections.hidden, false);
  assert.equal(h.view.investmentReport, null);
  assert.equal(h.select.disabled, false);
});

test('unmounted views ignore late responses', async () => {
  const h = harness(); const loading = h.open.emit('click'); h.view.isConnected = false;
  h.requests[0].reply(report()); await loading; assert.equal(h.view.investmentReport, null);
});

test('provider errors remain visible and loading state recovers without stale position rows', async () => {
  const h = harness(); const loading = h.open.emit('click');
  h.requests[0].reply({error: 'Investments is not active.'}, false); await loading;
  assert.equal(h.fields['[data-investment-error]'].hidden, false);
  assert.match(h.fields['[data-investment-error]'].textContent, /not active/);
  assert.equal(h.fields['[data-investment-load]'].disabled, false);
  assert.equal(h.fields['[data-investment-positions]'].children.length, 0);
});

test('large reports expose every position through progressive rows and handle unknown cost basis', async () => {
  const h = harness(); const value = report(); value.positions = Array.from({length: 501}, (_, index) => ({...value.positions[0], securityId: `fund-${index}`, costBasis: null, unrealizedGain: null, unrealizedReturnPercent: null}));
  const loading = h.open.emit('click'); h.requests[0].reply(value); await loading;
  assert.equal(h.fields['[data-investment-positions]'].children.length, 500);
  assert.equal(h.fields['[data-investment-more]'].hidden, false);
  await h.fields['[data-investment-more]'].emit('click');
  assert.equal(h.fields['[data-investment-positions]'].children.length, 501);
  assert.equal(h.fields['[data-investment-more]'].hidden, true);
});
