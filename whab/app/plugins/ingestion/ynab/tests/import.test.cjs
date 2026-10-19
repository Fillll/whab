const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_ynab/static/import.js'), 'utf8');
class Event {
  constructor(type) {this.type = type; this.defaultPrevented = false;}
  preventDefault() {this.defaultPrevented = true;}
}
class Target {
  constructor(properties = {}) {this.listeners = new Map(); Object.assign(this, properties);}
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
  }
  dispatchEvent(event) {for (const callback of this.listeners.get(event.type) || []) callback(event); return !event.defaultPrevented;}
  emit(type) {return Promise.all([...(this.listeners.get(type) || [])].map(callback => callback(new Event(type))));}
  replaceChildren() {this.children = [];}
  append(child) {(this.children ||= []).push(child);}
}
function panel() {
  const controls = Object.fromEntries(['form', 'file', 'usd', 'preview-button', 'commit', 'preview',
    'summary', 'month', 'warnings', 'reason', 'error', 'progress'].map(key => [`[data-import-${key}]`, new Target()]));
  const file = {name: 'fictional-export.zip', size: 1024};
  controls['[data-import-file]'].files = [file];
  controls['[data-import-usd]'].checked = true;
  controls['[data-import-preview]'].hidden = true;
  return new Target({dataset: {fileImport: 'ynab'}, controls, file, querySelector: selector => controls[selector]});
}
function harness(initialPanels = 0) {
  const requests = [], redirects = [];
  let panels = Array.from({length: initialPanels}, panel), operation = 0;
  const document = new Target({querySelectorAll: () => panels, createElement: () => new Target()});
  const location = {pathname: '/budgets/fictional/budget/2026-10/usd', origin: 'https://localhost:5173',
    assign: value => redirects.push(value)};
  vm.runInNewContext(source, {document, location, URL, crypto: {randomUUID: () => `fictional-operation-${++operation}`},
    fetch(url, options) {
      let resolve, reject;
      const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
      requests.push({url, options, reject, reply: (result, ok = true) => resolve({ok, json: async () => result})});
      return promise;
    },
  });
  return {document, requests, redirects,
    get panels() {return panels;},
    ready() {document.dispatchEvent(new Event('whab:page-ready'));},
    replace(count = 1) {panels = Array.from({length: count}, panel); document.dispatchEvent(new Event('whab:page-ready')); return panels;},
    canNavigate() {return document.dispatchEvent(new Event('whab:before-navigate'));},
  };
}
function preview(overrides = {}) {
  return {fingerprint: 'a'.repeat(64), accounts: 1, categories: 2, transactions: 3, assignments: 4,
    createsNewBudget: true, canImport: true, reason: 'Fictional preview', budgetStartMonth: '2026-10',
    warnings: ['Fictional warning'], ...overrides};
}
async function checked(state, index = 0) {
  const request = state.requests.length;
  const checking = state.panels[index].controls['[data-import-form]'].emit('submit');
  state.requests[request].reply(preview()); await checking;
}

test('fresh import panels bind once and page-ready preserves a selected archive and completed preview', async () => {
  const state = harness();
  assert.equal(state.canNavigate(), true);
  assert.equal(state.requests.length, 0);
  const [first] = state.replace(); state.ready(); state.ready();
  for (const [key, event] of [['file', 'change'], ['usd', 'change'], ['form', 'submit'], ['commit', 'click']]) {
    assert.equal(first.controls[`[data-import-${key}]`].listeners.get(event).size, 1);
  }
  await checked(state);
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, '/budgets/fictional/api/plugins/ynab/import/new/preview');
  assert.equal(state.requests[0].options.body, first.file);
  assert.equal(first.controls['[data-import-summary]'].textContent, '1 accounts · 2 categories · 3 transactions · 4 monthly assignments');
  assert.equal(first.controls['[data-import-preview]'].hidden, false);
  state.ready();
  assert.equal(first.controls['[data-import-file]'].files[0], first.file);
  assert.equal(first.controls['[data-import-commit]'].disabled, false);
  const [second] = state.replace(); state.ready();
  assert.notEqual(second, first);
  await checked(state);
  assert.equal(state.requests.length, 2);
  assert.equal(second.controls['[data-import-form]'].listeners.get('submit').size, 1);
  assert.equal(state.document.listeners.get('whab:page-ready').size, 1);
  assert.equal(state.document.listeners.get('whab:before-navigate').size, 1);
});

test('preview vetoes navigation and duplicate submission until its validation finishes', async () => {
  const state = harness(1), controls = state.panels[0].controls;
  const checking = controls['[data-import-form]'].emit('submit');
  void controls['[data-import-form]'].emit('submit');
  state.ready();
  assert.equal(state.requests.length, 1);
  assert.equal(state.canNavigate(), false);
  assert.equal(controls['[data-import-file]'].disabled, true);
  assert.equal(controls['[data-import-preview-button]'].disabled, true);
  state.requests[0].reply(preview()); await checking;
  assert.equal(state.canNavigate(), true);
  assert.equal(controls['[data-import-file]'].disabled, false);
  assert.equal(controls['[data-import-commit]'].disabled, false);
});

test('multiple active panels keep navigation blocked until the last preview settles', async () => {
  const state = harness(2);
  const checking = state.panels.map(item => item.controls['[data-import-form]'].emit('submit'));
  assert.equal(state.requests.length, 2);
  assert.equal(state.canNavigate(), false);
  state.requests[0].reply(preview()); await checking[0];
  assert.equal(state.canNavigate(), false);
  state.requests[1].reply(preview()); await checking[1];
  assert.equal(state.canNavigate(), true);
});

test('network and malformed preview errors release the guard and permit a new attempt', async () => {
  const state = harness(1), controls = state.panels[0].controls;
  const first = controls['[data-import-form]'].emit('submit');
  state.requests[0].reject(new Error('Fictional network failure.')); await first;
  assert.equal(state.canNavigate(), true);
  assert.equal(controls['[data-import-error]'].hidden, false);
  const retry = controls['[data-import-form]'].emit('submit');
  state.requests[1].reply(preview({fingerprint: 'invalid'})); await retry;
  assert.equal(state.canNavigate(), true);
  assert.equal(controls['[data-import-commit]'].disabled, true);
  assert.equal(controls['[data-import-progress]'].hidden, true);
  await checked(state);
  assert.equal(controls['[data-import-commit]'].disabled, false);
});

test('commit uses the preview identity and retains its navigation guard through a successful new-budget redirect', async () => {
  const state = harness(1), item = state.panels[0], controls = item.controls;
  await checked(state); state.ready();
  const creating = controls['[data-import-commit]'].emit('click');
  void controls['[data-import-commit]'].emit('click');
  assert.equal(state.requests.length, 2);
  assert.equal(state.canNavigate(), false);
  assert.equal(state.requests[1].url, '/budgets/fictional/api/plugins/ynab/import/new/commit');
  assert.equal(state.requests[1].options.body, item.file);
  assert.equal(state.requests[1].options.headers['X-Import-Fingerprint'], 'a'.repeat(64));
  assert.equal(state.requests[1].options.headers['X-Import-Operation'], state.requests[0].options.headers['X-Import-Operation']);
  const id = '11111111-1111-4111-8111-111111111111';
  state.requests[1].reply({id, url: `/budgets/${id}/budget/current-month/usd`}); await creating;
  assert.deepEqual(state.redirects, [`https://localhost:5173/budgets/${id}/budget/current-month/usd`]);
  assert.equal(state.canNavigate(), false);
});

test('failed commits release navigation while preserving the same archive and operation for retry', async () => {
  const state = harness(1), item = state.panels[0], controls = item.controls;
  await checked(state);
  const first = controls['[data-import-commit]'].emit('click');
  state.requests[1].reply({error: 'Import failed.'}, false); await first;
  assert.equal(state.canNavigate(), true);
  assert.equal(controls['[data-import-error]'].textContent, 'Import failed.');
  assert.equal(controls['[data-import-commit]'].disabled, false);
  state.ready();
  const retry = controls['[data-import-commit]'].emit('click');
  assert.equal(state.requests[2].options.body, item.file);
  assert.equal(state.requests[2].options.headers['X-Import-Operation'], state.requests[1].options.headers['X-Import-Operation']);
  state.requests[2].reply({id: '11111111-1111-4111-8111-111111111111', url: 'https://external.invalid/budgets/11111111-1111-4111-8111-111111111111/budget/current-month/usd'});
  await retry;
  assert.equal(state.canNavigate(), true);
  assert.equal(state.redirects.length, 0);
  assert.match(controls['[data-import-error]'].textContent, /could not be opened/);
});

test('changing a checked archive invalidates its preview without another request', async () => {
  const state = harness(1), controls = state.panels[0].controls;
  await checked(state);
  controls['[data-import-file]'].files = [{name: 'different-fictional.zip', size: 2048}];
  await controls['[data-import-file]'].emit('change');
  await controls['[data-import-commit]'].emit('click');
  assert.equal(state.requests.length, 1);
  assert.equal(controls['[data-import-preview]'].hidden, true);
  assert.equal(controls['[data-import-commit]'].disabled, true);
  assert.equal(state.canNavigate(), true);
});
