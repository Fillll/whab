const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_plaid/static/oauth.js'), 'utf8');
const flow = {formatVersion: 1, provider: 'plaid', environment: 'sandbox', legacy: false, phase: 'ready', flow_id: 'a'.repeat(32), generation: 7, kind: 'link', stage: 'ready', deadline_ms: 4000000000000};
function run({session = {}, href = 'https://localhost:5173/?oauth_state_id=fictional-private-state', brokenStorage = false} = {}) {
  const values = new Map(Object.entries({'whab-link-token': 'fictional-token', 'whab-link-budget': '/budgets/home', 'whab-link-return-url': '/budgets/home/accounts/2026-10/usd', 'whab-link-context': JSON.stringify(flow), ...session}));
  const redirects = [], paragraph = {textContent: ''};
  const url = new URL(href);
  vm.runInNewContext(source, {URL, location: {href, search: url.search, hash: url.hash, replace: destination => redirects.push(destination)},
    document: {getElementById: () => paragraph, querySelector: () => paragraph},
    sessionStorage: {getItem(key) {if (brokenStorage) throw new Error('Unavailable'); return values.get(key) || null;}, setItem: (key, value) => values.set(key, value), removeItem(key) {if (brokenStorage) throw new Error('Unavailable'); values.delete(key);}},
  });
  return {values, redirects, paragraph};
}

test('OAuth bridge returns only to the original addressed budget with unchanged flow identity', () => {
  for (const budget of ['home', '12345678-1234-1234-1234-123456789abc', 'fictional-household']) {
    const original = '/budgets/' + budget;
    const state = run({session: {'whab-link-budget': original, 'whab-link-return-url': original + '/accounts/current-month/eur'}});
    assert.deepEqual(state.redirects, [original + '/accounts/current-month/eur?oauth_state_id=fictional-private-state']);
    assert.equal(state.values.get('whab-link-redirect-uri'), 'https://localhost:5173/?oauth_state_id=fictional-private-state');
    assert.deepEqual(JSON.parse(state.values.get('whab-link-context')), flow);
    assert.equal(state.paragraph.textContent, '');
  }
});

test('new browser records can retain the explicit restart-only legacy compatibility mode', () => {
  const state = run({session: {'whab-link-context': JSON.stringify({formatVersion: 1, provider: 'plaid', environment: 'production', legacy: true, phase: 'ready'})}});
  assert.equal(state.redirects.length, 1);
});

test('OAuth refuses unscoped, submitted, expired, incompatible and malformed continuation records', () => {
  const invalid = [null, '{', {}, {...flow, formatVersion: 2}, {...flow, provider: 'other'}, {...flow, environment: 'demo'}, {...flow, legacy: 'false'}, {...flow, phase: 'submitted'}, {...flow, flow_id: 'A'.repeat(32)}, {...flow, generation: true}, {...flow, generation: 0}, {...flow, kind: 'other'}, {...flow, stage: 'exchange_uncertain'}, {...flow, deadline_ms: 1}, {...flow, deadline_ms: 1.1}];
  for (const context of invalid) {
    const state = run({session: {'whab-link-context': context === '{' ? context : JSON.stringify(context)}});
    assert.deepEqual(state.redirects, []);
    const retained = typeof context === 'object' && context && context.flow_id === flow.flow_id && Number.isSafeInteger(context.generation) && context.generation > 0 && context.formatVersion === 1 && context.provider === 'plaid' && ['sandbox', 'production'].includes(context.environment) && context.legacy === false && ['link', 'update'].includes(context.kind) && Number.isSafeInteger(context.deadline_ms) && context.deadline_ms > 0;
    assert.equal(state.values.size > 0, !!retained);
    assert.match(state.paragraph.textContent, retained ? /Do not connect again/ : /session expired/);
    assert.equal(state.paragraph.textContent.includes('fictional-private-state'), false);
  }
});

test('OAuth cannot redirect outside its local budget, to another view, or with a fragment', () => {
  for (const destination of ['https://other.example/budgets/home/accounts/2026-10/usd', '//other.example/budgets/home/accounts/2026-10/usd', '/budgets/other/accounts/2026-10/usd', '/budgets/home/transactions/2026-10/usd', '/budgets/home/accounts/2026-13/usd', '/budgets/home/accounts/2026-10/usd?redirect=outside', '/budgets/home/accounts/2026-10/usd#fragment', '/budgets/home/accounts/2026-10/usd\n']) {
    const state = run({session: {'whab-link-return-url': destination}});
    assert.equal(state.redirects.length, 0);
    assert.equal(state.values.has('whab-link-context'), true);
  }
  for (const href of ['https://localhost:5173/', 'https://localhost:5173/?oauth_state_id=fictional#fragment']) {
    assert.equal(run({href}).redirects.length, 0);
  }
  assert.equal(run({session: {'whab-link-budget': '/budgets/another'}}).redirects.length, 0);
  assert.equal(run({session: {'whab-link-token': ''}}).redirects.length, 0);
  for (const budget of ['demo', 'a'.repeat(201), 'UPPER', 'invalid--alias']) {
    assert.equal(run({session: {'whab-link-budget': '/budgets/' + budget, 'whab-link-return-url': '/budgets/' + budget + '/accounts/current-month/usd'}}).redirects.length, 0);
  }
});

test('unavailable browser storage renders a fixed expiry notice without throwing', () => {
  const state = run({brokenStorage: true});
  assert.equal(state.redirects.length, 0);
  assert.match(state.paragraph.textContent, /session expired/);
});


test('rejected OAuth never erases an already submitted or uncertain durable flow', () => {
  for (const context of [{...flow, phase: 'submitted'}, {...flow, stage: 'exchange_uncertain'}, {...flow, deadline_ms: 1}, {...flow, legacy: true, phase: 'submitted'}]) {
    const state = run({session: {'whab-link-context': JSON.stringify(context)}});
    assert.equal(state.redirects.length, 0);
    assert.deepEqual(JSON.parse(state.values.get('whab-link-context')), context);
    assert.match(state.paragraph.textContent, /Do not connect again/);
  }
});
