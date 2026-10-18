/* Run with node --test plugins/interface/web/tests/transaction_register.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-register.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
function element(properties = {}) {
  const listeners = {}, attributes = {}, classes = new Set();
  return Object.assign({
    dataset: {}, value: '', hidden: false, checked: false,
    classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name);},
      remove(name) {classes.delete(name);}, contains(name) {return classes.has(name);}},
    setAttribute(name, value) {attributes[name] = value;},
    getAttribute(name) {return attributes[name];},
    removeAttribute(name) {delete attributes[name];},
    addEventListener(type, callback) {(listeners[type] ||= []).push(callback);},
    removeEventListener(type, callback) {listeners[type] = (listeners[type] || []).filter(value => value !== callback);},
    emit(type, detail = {}) {
      const event = {prevented: false, preventDefault() {this.prevented = true;}, ...detail};
      const completed = Promise.all((listeners[type] || []).map(callback => callback(event)));
      return {event, completed};
    },
    querySelector: () => null, querySelectorAll: () => [],
  }, properties);
}

function harness(count = 5) {
  const requests = [];
  const rowList = Array.from({length: count}, (_, index) => {
    const controls = Object.fromEntries(['[data-select-transaction]', '[data-transaction-field="note"]',
      '[data-transaction-field="categoryId"]', '[data-transaction-reviewed]', '[data-transaction-receipt]']
      .map(selector => [selector, element()]));
    controls['[data-select-transaction]'].checked = true;
    controls['[data-transaction-reviewed]'].setAttribute('aria-pressed', 'false');
    return element({dataset: {transactionId: `transaction-${index}`, merchant: `Payee ${index}`,
      payee: `payee ${index}`, reviewed: 'false', pending: 'false', transfer: 'false'},
    querySelector: selector => controls[selector], controls});
  });
  const bulk = element({dataset: {bulkReviewed: 'true'}}), clear = element(), selectionCount = element();
  const selectionBar = element({querySelector: selector => selector === '[data-clear-selection]' ? clear : selectionCount,
    querySelectorAll: () => [bulk]});
  const feedbackText = element(), feedback = element({hidden: true, querySelector: () => feedbackText});
  const receiptControls = Object.fromEntries(['[data-receipt-preview]', '[data-receipt-error]',
    '[data-receipt-status]', '[data-receipt-file]', '[data-receipt-remove]', '[data-receipt-close]',
    '[data-receipt-form]'].map(selector => [selector, element()]));
  const receiptDialog = element({querySelector: selector => receiptControls[selector]});
  const controls = Object.fromEntries(['#transaction-search', '#account-filter', '#review-filter',
    '#transaction-count', '[data-select-all]', '[data-transaction-save-status]', '#all-dates-filter',
    '[data-navigation="month"]', '[data-transaction-retry]'].map(selector => [selector, element()]));
  controls['#transaction-count'].dataset.period = 'All dates';
  const root = element({dataset: {workspace: 'personal'},
    querySelectorAll: () => rowList,
    querySelector: selector => ({...controls, '[data-transaction-selection-bar]': selectionBar,
      '[data-transaction-feedback]': feedback, '[data-transaction-receipt-dialog]': receiptDialog})[selector]});
  const window = element();
  const context = {document: {querySelector: () => root}, window, setTimeout, clearTimeout, URL,
    encodeURIComponent, location: {assign() {throw new Error('Unexpected full navigation.');}},
    notify() {}, apiUrl: value => value, pageUrl: value => value,
    api(path, body, method) {
      if (path.startsWith('/receipts?')) return Promise.resolve({supported: false, transactionIds: []});
      const pending = deferred();
      requests.push({path, body: JSON.parse(JSON.stringify(body)), method, ...pending});
      return pending.promise;
    },
  };
  vm.runInNewContext(source.replace(/^import .*$/m, '').replaceAll('export function ', 'function '), context);
  const register = context.initTransactionRegister();
  return {register, requests, rows: rowList, bulk, window, feedback, feedbackText, controls};
}

test('navigation waits for every wave of a bulk review and retains a later failure for retry', async () => {
  const state = harness();
  const bulk = state.bulk.emit('click').completed;
  assert.equal(state.requests.length, 4);
  assert.equal(state.register.dirty, true);
  assert.equal(state.window.emit('beforeunload').event.prevented, true);
  let finished = false;
  const navigating = state.register.flush().then(result => {finished = true; return result;});
  for (const request of state.requests.slice(0, 4)) request.resolve({ok: true});
  await tick();
  assert.equal(state.requests.length, 5);
  assert.equal(finished, false);
  assert.equal(state.requests[4].path, '/transactions/transaction-4');
  state.requests[4].reject(new Error('Connection interrupted.'));
  await bulk;
  await tick();
  // Navigation gets a chance to retry the retained change, and must remain blocked if it fails.
  assert.equal(state.requests.length, 6);
  assert.equal(finished, false);
  state.requests[5].reject(new Error('Still offline.'));
  assert.equal(await navigating, false);
  assert.equal(state.register.dirty, true);
  assert.equal(state.feedback.hidden, false);
  assert.equal(state.feedbackText.textContent, 'Still offline.');
  const retry = state.controls['[data-transaction-retry]'].emit('click').completed;
  assert.equal(state.requests.length, 7);
  assert.deepEqual(state.requests[6].body, {workspace: 'personal', reviewed: true});
  state.requests[6].resolve({ok: true});
  await retry;
  assert.equal(state.register.dirty, false);
  assert.equal(state.feedback.hidden, true);
  assert.equal(state.rows.every(row => row.dataset.reviewed === 'true'), true);
  state.register.dispose();
});

test('category edits update the uncategorized indicator immediately while transfers stay neutral', async () => {
  const state = harness(2);
  const category = state.rows[0].controls['[data-transaction-field="categoryId"]'];
  category.classList.toggle('uncategorized', true);
  category.value = 'groceries';
  category.emit('change');
  assert.equal(category.classList.contains('uncategorized'), false);
  assert.deepEqual(state.requests[0].body, {workspace: 'personal', categoryId: 'groceries'});
  state.requests[0].resolve({ok: true});
  await tick();
  category.value = '';
  category.emit('change');
  assert.equal(category.classList.contains('uncategorized'), true);
  assert.deepEqual(state.requests[1].body, {workspace: 'personal', categoryId: null});
  state.requests[1].resolve({ok: true});
  const transfer = state.rows[1];
  transfer.dataset.transfer = 'true';
  const transferCategory = transfer.controls['[data-transaction-field="categoryId"]'];
  transferCategory.value = '';
  transferCategory.emit('change');
  assert.equal(transferCategory.classList.contains('uncategorized'), false);
  state.requests[2].resolve({ok: true});
  assert.equal(await state.register.flush(), true);
  state.register.dispose();
});
