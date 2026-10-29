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
    removeAttribute(name) {delete attributes[name]; if (name === 'src') this.src = '';},
    showModal() {this.open = true;},
    close() {this.open = false; this.emit('close');},
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

function harness(count = 5, options = {}) {
  const requests = [];
  const receiptRequests = [], imageRequests = [], objectUrls = [], revokedUrls = [];
  const workspace = options.workspace || 'personal';
  const navigations = [];
  const contexts = [], history = [];
  let pickerChoose, pickerDisposed = false, periodChoose, periodDisposed = false;
  const makeRow = index => {
    const controls = Object.fromEntries(['[data-select-transaction]', '[data-transaction-field="note"]',
      '[data-transaction-field="categoryId"]', '[data-transaction-reviewed]', '[data-transaction-receipt]']
      .map(selector => [selector, element()]));
    controls['[data-select-transaction]'].checked = true;
    controls['[data-transaction-reviewed]'].setAttribute('aria-pressed', 'false');
    controls['[data-transaction-receipt]'].disabled = true;
    controls['[data-transaction-receipt]'].title = 'Receipt';
    return element({dataset: {transactionId: options.transactionIds?.[index] || `transaction-${index}`, merchant: `Payee ${index}`,
      payee: `payee ${index}`, reviewed: 'false', pending: 'false', transfer: 'false'},
    querySelector: selector => controls[selector], controls});
  };
  const rowList = Array.from({length: count}, (_, index) => makeRow(index));
  const body = {querySelector: () => rowList[0], append(row) {rowList.push(row);}, insertBefore(row, anchor) {rowList.splice(rowList.indexOf(anchor), 0, row);}};
  let streamCallbacks;
  const bulk = element({dataset: {bulkReviewed: 'true'}}), clear = element(), selectionCount = element();
  const selectionBar = element({querySelector: selector => selector === '[data-clear-selection]' ? clear : selectionCount,
    querySelectorAll: () => [bulk]});
  const feedbackText = element(), feedback = element({hidden: true, querySelector: () => feedbackText});
  const receiptControls = Object.fromEntries(['[data-receipt-preview]', '[data-receipt-pdf]', '[data-receipt-error]',
    '[data-receipt-status]', '[data-receipt-file]', '[data-receipt-remove]', '[data-receipt-close]',
    '[data-receipt-form]', '[data-receipt-payee]', '[data-receipt-empty]', '[data-receipt-save]'].map(selector => [selector, element()]));
  receiptControls['[data-receipt-file]'].files = [];
  receiptControls['[data-receipt-error]'].hidden = true;
  receiptControls['[data-receipt-preview]'].hidden = true;
  receiptControls['[data-receipt-remove]'].hidden = true;
  const receiptDialog = element({querySelector: selector => receiptControls[selector],
    querySelectorAll: selector => selector === 'button,input' ? ['[data-receipt-close]', '[data-receipt-remove]',
      '[data-receipt-save]', '[data-receipt-file]'].map(key => receiptControls[key]) : []});
  const controls = Object.fromEntries(['#transaction-search', '#account-filter', '#review-filter',
    '#transaction-count', '[data-select-all]', '[data-transaction-save-status]', '#all-dates-filter',
    '[data-navigation="month"]', '[data-transaction-retry]'].map(selector => [selector, element()]));
  controls['#transaction-search'].value = options.search || '';
  const searchForm = element(), pageLink = element({href: 'https://localhost:5173/budgets/home/transactions/2026-10/usd?tx_page=2'});
  controls['#transaction-count'].dataset.period = 'All dates';
  const reviewLabel = element();
  controls['#review-filter'].dataset.reviewState = options.review || 'all';
  controls['#review-filter'].querySelector = () => reviewLabel;
  const root = element({dataset: {workspace, ...(options.paged ? {transactionPaged: 'true', transactionPage: '1', transactionRequestedPage: '1'} : {})},
    querySelectorAll: selector => selector === '.transaction-row' ? rowList : selector === '[data-transaction-page]' ? [pageLink] : [],
    querySelector: selector => ({...controls, '[data-transaction-selection-bar]': selectionBar,
      '[data-transaction-feedback]': feedback, '[data-transaction-receipt-dialog]': receiptDialog,
      '[data-transaction-search-form]': searchForm, '.transactions-table tbody': body})[selector]});
  const window = element();
  window.history = {state: {}, replaceState: (...args) => history.push(args)};
  let requestId = 0;
  class ReceiptURL extends URL {
    static createObjectURL(blob) {const url = `blob:fixture-${objectUrls.length + 1}`; objectUrls.push({url, blob}); return url;}
    static revokeObjectURL(url) {revokedUrls.push(url);}
  }
  const context = {document: {...element(), querySelector: () => root}, window, setTimeout, clearTimeout, queueMicrotask, URL: ReceiptURL,
    crypto: {randomUUID: () => `review-request-${++requestId}`},
    getContext: () => ({bulkReview: options.bulkReview === true, receiptIndexScoped: options.receiptIndexScoped}),
    encodeURIComponent, location: {href: 'https://localhost:5173/budgets/home/transactions/2026-10/usd', assign() {throw new Error('Unexpected full navigation.');}},
    notify() {}, apiUrl: value => value, pageUrl: value => value,
    updateContext: value => contexts.push(value),
    initAccountPicker(_, onChoose) {
      pickerChoose = onChoose;
      return {dispose() {pickerDisposed = true;}};
    },
    initPeriodPicker(_, onChoose) {
      periodChoose = onChoose;
      return {dispose() {periodDisposed = true;}};
    },
    api(path, body, method) {
      if (path === '/receipts/index' || path === `/receipts?workspace=${workspace}`) {
        if (path === '/receipts/index') {
          assert.equal(options.receiptIndexScoped, true);
          assert.equal(method, 'POST');
          assert.equal(body.workspace, workspace);
        } else {
          assert.notEqual(options.receiptIndexScoped, true);
          assert.equal(body, undefined);
          assert.equal(method, undefined);
        }
        receiptRequests.push({path, body: body === undefined ? undefined : JSON.parse(JSON.stringify(body)), method});
        return options.receiptIndex?.promise || Promise.resolve({supported: false, transactionIds: []});
      }
      assert.equal(path.startsWith('/receipts'), false, 'Unexpected receipt index route.');
      const pending = deferred();
      requests.push({path, body: JSON.parse(JSON.stringify(body)), method, ...pending});
      return pending.promise;
    },
    fetch(path, details) {
      assert.ok(rowList.some(row => path === `/transactions/${encodeURIComponent(row.dataset.transactionId)}/receipt?workspace=${workspace}`));
      const pending = deferred(); imageRequests.push({path, details, ...pending}); return pending.promise;
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-grid.js'), 'utf8').replaceAll('export function ', 'function '), context);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-stream.js'), 'utf8').replaceAll('export function ', 'function '), context);
  vm.runInNewContext(source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), context);
  if (options.stream) {
    context.bindLogos = () => {};
    context.initTransactionStream = callbacks => {streamCallbacks = callbacks; return {cancel() {}, mutation() {}, check() {}, dispose() {}};};
  }
  const register = context.initTransactionRegister({navigate: async (url, details) => {
    navigations.push({url: JSON.parse(JSON.stringify(url)), details: JSON.parse(JSON.stringify(details))});
    return true;
  }, ...options});
  return {register, requests, receiptRequests, makeRow, stream: streamCallbacks, imageRequests, receiptControls, objectUrls, revokedUrls,
    rows: rowList, bulk, window, feedback, feedbackText, controls, navigations,
    chooseAccount: value => pickerChoose(value), isPickerDisposed: () => pickerDisposed,
    choosePeriod: value => periodChoose(value), isPeriodDisposed: () => periodDisposed, reviewLabel, contexts, history, searchForm, pageLink, document: context.document, receiptDialog};
}

test('paged search debounces full-history navigation and cancels stale work immediately', async () => {
  let canceled = 0;
  const state = harness(2, {paged: true, cancelNavigation: () => {canceled += 1;}});
  state.controls['#transaction-count'].textContent = '1000 transactions';
  const search = state.controls['#transaction-search'];
  search.value = 'older'; search.emit('input');
  search.value = 'older memo'; search.emit('input');
  assert.equal(canceled, 2);
  assert.equal(state.navigations.length, 0);
  await new Promise(resolve => setTimeout(resolve, 330));
  assert.deepEqual(state.navigations, [{url: {q: 'older memo', review: 'all'}, details: {focus: '#transaction-search'}}]);
  assert.equal(state.controls['#transaction-count'].textContent, '1000 transactions');
  assert.equal(state.requests.length, 0);
  search.value = 'discarded'; search.emit('input');
  state.register.dispose();
  await new Promise(resolve => setTimeout(resolve, 330));
  assert.equal(state.navigations.length, 1);
});

test('paged search submit and page links wait for edited memos before navigation', async () => {
  const state = harness(1, {paged: true});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'Keep this edit'; memo.emit('input');
  state.controls['#transaction-search'].value = 'complete history';
  assert.equal(state.searchForm.emit('submit').event.prevented, true);
  assert.equal(state.requests.length, 1);
  assert.equal(state.navigations.length, 0);
  state.requests[0].resolve({ok: true});
  await tick();
  assert.deepEqual(state.requests[0].body, {workspace: 'personal', note: 'Keep this edit'});
  assert.deepEqual(state.navigations[0], {url: {q: 'complete history', review: 'all'}, details: {focus: '#transaction-search'}});
  state.register.dispose();
  const paging = harness(1, {paged: true});
  const event = paging.pageLink.emit('click', {button: 0});
  assert.equal(event.event.prevented, true);
  await tick();
  assert.deepEqual(paging.navigations, [{url: paging.pageLink.href, details: {focus: false}}]);
  paging.register.dispose();
});

test('paged review filters search the complete history and retain a pending account choice', async () => {
  const pending = new URL('https://localhost:5173/budgets/home/transactions/2026-10/usd?account=second&tx_page=7&q=old');
  const state = harness(1, {paged: true, search: 'old', pendingDestination: () => pending});
  state.controls['#review-filter'].emit('click');
  await tick();
  assert.equal(state.rows[0].hidden, false);
  const target = new URL(state.navigations[0].url);
  assert.equal(target.searchParams.get('account'), 'second');
  assert.equal(target.searchParams.get('review'), 'needs-review');
  assert.equal(target.searchParams.get('q'), 'old');
  assert.equal(target.searchParams.has('tx_page'), false);
  state.register.dispose();
});

test('a new financial draft cancels a mutation refresh and survives its stale response', async () => {
  const refresh = deferred(), navigations = [];
  let canceled = 0;
  const state = harness(1, {paged: true, review: 'needs-review', cancelNavigation: () => {canceled += 1;},
    navigate: (url, details) => {navigations.push({url, details}); return refresh.promise;}});
  state.rows[0].controls['[data-transaction-reviewed]'].emit('click');
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(navigations.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(navigations[0].details)), {focus: false, refresh: true, history: 'replace'});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'Second draft must survive'; memo.emit('input');
  assert.equal(canceled, 2);
  refresh.resolve(true);
  await tick();
  const saving = state.register.flush();
  assert.deepEqual(state.requests[1].body, {workspace: 'personal', note: 'Second draft must survive'});
  state.requests[1].resolve({ok: true});
  assert.equal(await saving, true);
  state.register.dispose();
});

test('memo search results remain in place during autosave and refresh only after focus leaves', async () => {
  const state = harness(1, {paged: true, search: 'old'});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  state.document.activeElement = memo;
  memo.value = 'New memo'; memo.emit('input'); memo.emit('change');
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  state.document.activeElement = null;
  memo.emit('blur');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.deepEqual(state.navigations[0].details, {focus: false, refresh: true, history: 'replace'});
  state.register.dispose();
});

test('review changes under All request only server counts and wait for memo focus to leave', async () => {
  const state = harness(1, {paged: true});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.matches = () => true;
  state.rows[0].controls['[data-transaction-reviewed]'].emit('click');
  state.document.activeElement = memo;
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  state.document.activeElement = null;
  memo.emit('blur');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.deepEqual(state.navigations[0].details, {focus: false, refresh: true, history: 'replace', summary: true});
  state.document.emit('click');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 1);
  state.register.dispose();
});

test('bulk review coalesces all successful writes into one summary request', async () => {
  const state = harness(6, {paged: true});
  const saving = state.bulk.emit('click').completed;
  assert.equal(state.requests.length, 4);
  for (let index = 0; index < 6; index += 1) {
    state.requests[index].resolve({ok: true});
    await tick();
  }
  await saving;
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.requests.length, 6);
  assert.deepEqual(state.navigations.map(entry => entry.details), [{focus: false, refresh: true, history: 'replace', summary: true}]);
  state.document.emit('click');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 1);
  state.register.dispose();
});

test('native bulk review sends one bounded request, excludes pending rows and keeps the current editors', async () => {
  const state = harness(100, {paged: true, bulkReview: true});
  state.rows[0].dataset.pending = 'true';
  const memo = state.rows[1].controls['[data-transaction-field="note"]'];
  memo.matches = () => true;
  state.document.activeElement = memo;
  const saving = state.bulk.emit('click').completed;
  state.bulk.emit('click');
  assert.equal(state.requests.length, 0);
  assert.equal(state.register.dirty, true);
  assert.equal(state.window.emit('beforeunload').event.prevented, true);
  await tick();
  assert.equal(state.requests.length, 1);
  const request = state.requests[0];
  assert.equal(request.path, '/transactions/review');
  assert.equal(request.method, 'PUT');
  assert.equal(request.body.workspace, 'personal');
  assert.equal(request.body.reviewed, true);
  assert.equal(request.body.transactionIds.length, 99);
  assert.equal(request.body.transactionIds.includes('transaction-0'), false);
  assert.equal(new Set(request.body.transactionIds).size, 99);
  request.resolve({ok: true}); await saving;
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.rows[0].dataset.reviewed, 'false');
  assert.equal(state.rows.slice(1).every(row => row.dataset.reviewed === 'true'), true);
  assert.equal(state.register.dirty, false);
  assert.equal(state.document.activeElement, memo);
  assert.equal(state.navigations.length, 0);
  state.document.activeElement = null; memo.emit('blur');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.deepEqual(state.navigations.map(entry => entry.details), [{focus: false, refresh: true, history: 'replace', summary: true}]);
  assert.equal(await state.register.flush(), true);
  assert.equal(state.requests.length, 1);
  state.register.dispose();
});

test('native lost-response retry confirms the same cohort before newer memo and opposite review fields', async () => {
  const state = harness(3, {paged: true, bulkReview: true});
  const saving = state.bulk.emit('click').completed;
  await tick();
  const original = state.requests[0].body;
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.matches = () => true;
  state.document.activeElement = memo;
  memo.value = 'Latest memo must survive'; memo.emit('input');
  state.rows[0].controls['[data-transaction-reviewed]'].emit('click');
  state.requests[0].reject(new Error('Response was lost after commit.'));
  await saving;
  assert.equal(state.requests.length, 1);
  assert.equal(state.rows.every(row => row.classList.contains('has-error')), true);
  assert.equal(state.register.dirty, true);
  assert.equal(state.navigations.length, 0);
  const retry = state.controls['[data-transaction-retry]'].emit('click').completed;
  await tick();
  assert.equal(state.requests.length, 2);
  assert.deepEqual(state.requests[1].body, original);
  state.requests[1].resolve({ok: true});
  await tick();
  assert.equal(state.requests.length, 3);
  assert.equal(state.requests[2].path, '/transactions/transaction-0');
  assert.deepEqual(state.requests[2].body, {workspace: 'personal', reviewed: false, note: 'Latest memo must survive'});
  state.requests[2].resolve({ok: true}); await retry;
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.deepEqual(state.rows.map(row => row.dataset.reviewed), ['false', 'true', 'true']);
  assert.equal(state.rows.some(row => row.classList.contains('has-error')), false);
  assert.equal(state.feedback.hidden, true);
  assert.equal(state.register.dirty, false);
  assert.equal(state.document.activeElement, memo);
  assert.equal(memo.value, 'Latest memo must survive');
  assert.equal(state.navigations.length, 0);
  state.document.activeElement = null; memo.emit('blur');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 1);
  assert.equal(state.requests.length, 3);
  state.register.dispose();
});

test('writers already saving memos form a later native cohort after their original write finishes', async () => {
  const state = harness(3, {paged: true, bulkReview: true});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'Already saving'; memo.emit('input'); memo.emit('change');
  const saving = state.bulk.emit('click').completed;
  await tick();
  assert.equal(state.requests.length, 2);
  assert.equal(state.requests[0].path, '/transactions/transaction-0');
  assert.deepEqual(state.requests[1].body.transactionIds, ['transaction-1', 'transaction-2']);
  state.requests[1].resolve({ok: true}); await tick();
  assert.equal(state.register.dirty, true);
  state.requests[0].resolve({ok: true}); await tick();
  assert.equal(state.requests.length, 3);
  assert.equal(state.requests[2].path, '/transactions/review');
  assert.deepEqual(state.requests[2].body.transactionIds, ['transaction-0']);
  assert.notEqual(state.requests[2].body.requestId, state.requests[1].body.requestId);
  state.requests[2].resolve({ok: true}); await saving;
  assert.equal(state.rows.every(row => row.dataset.reviewed === 'true'), true);
  assert.equal(memo.value, 'Already saving');
  state.register.dispose();
});

test('combined queued category and review patches retain the individual ordered path', async () => {
  const state = harness(2, {paged: true, bulkReview: true});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'Original memo'; memo.emit('input'); memo.emit('change');
  const saving = state.bulk.emit('click').completed;
  const category = state.rows[0].controls['[data-transaction-field="categoryId"]'];
  category.value = 'rent'; category.emit('change');
  await tick();
  state.requests[1].resolve({ok: true});
  state.requests[0].resolve({ok: true}); await tick();
  assert.equal(state.requests.length, 3);
  assert.equal(state.requests[2].path, '/transactions/transaction-0');
  assert.deepEqual(state.requests[2].body, {workspace: 'personal', reviewed: true, categoryId: 'rent'});
  state.requests[2].resolve({ok: true}); await saving;
  assert.equal(category.value, 'rent');
  assert.equal(state.register.dirty, false);
  state.register.dispose();
});

test('atomic errors retain every native row failure and never fall back to individual requests', async () => {
  const state = harness(2, {paged: true, bulkReview: true});
  const saving = state.bulk.emit('click').completed;
  await tick(); state.requests[0].reject(new Error('Transaction not found.')); await saving;
  assert.equal(state.rows.every(row => row.dataset.reviewed === 'false' && row.classList.contains('has-error')), true);
  const navigating = state.register.flush();
  await tick();
  assert.equal(state.requests.length, 2);
  assert.deepEqual(state.requests[1].body, state.requests[0].body);
  assert.equal(state.requests.every(request => request.path === '/transactions/review'), true);
  state.requests[1].reject(new Error('Transaction not found.'));
  assert.equal(await navigating, false);
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  assert.equal(state.register.dirty, true);
  assert.equal(state.feedback.hidden, false);
  state.register.dispose();
});

test('search navigation waits for native batch completion and preserves its filter destination', async () => {
  const state = harness(2, {paged: true, bulkReview: true, review: 'needs-review'});
  const saving = state.bulk.emit('click').completed;
  state.controls['#transaction-search'].value = 'another payee';
  state.searchForm.emit('submit');
  await tick();
  assert.equal(state.navigations.length, 0);
  assert.equal(state.requests.length, 1);
  state.requests[0].resolve({ok: true}); await saving;
  await tick();
  assert.deepEqual(state.navigations, [{url: {q: 'another payee', review: 'needs-review'}, details: {focus: '#transaction-search'}}]);
  state.register.dispose();
});

test('disposal settles queued native review saves without triggering a detached register refresh', async () => {
  const state = harness(3, {paged: true, bulkReview: true});
  const saving = state.bulk.emit('click').completed;
  state.register.dispose();
  await tick();
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].path, '/transactions/review');
  state.requests[0].resolve({ok: true}); await saving;
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'Detached'; memo.emit('input'); memo.emit('change');
  assert.equal(state.requests.length, 1);
});

for (const failed of [false, true]) test(`disposal settles an active native ${failed ? 'failure' : 'save'} without refresh or another request`, async () => {
  const state = harness(3, {paged: true, bulkReview: true});
  const saving = state.bulk.emit('click').completed;
  await tick();
  assert.equal(state.requests.length, 1);
  state.register.dispose();
  if (failed) state.requests[0].reject(new Error('Unknown result after disposal.'));
  else state.requests[0].resolve({ok: true});
  await saving;
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.requests.length, 1);
  assert.equal(state.navigations.length, 0);
  assert.equal(state.rows.every(row => row.classList.contains('has-error') === failed), true);
});

test('a canceled summary keeps refresh pending until a new draft is saved', async () => {
  const summary = deferred(), navigations = [];
  const state = harness(1, {paged: true, navigate: (url, details) => {
    navigations.push(details);
    return navigations.length === 1 ? summary.promise : Promise.resolve(true);
  }});
  state.rows[0].controls['[data-transaction-reviewed]'].emit('click');
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'new draft'; memo.emit('input');
  summary.resolve(true);
  await tick();
  assert.equal(navigations.length, 1);
  const saving = state.register.flush();
  state.requests[1].resolve({ok: true}); await saving;
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(navigations.length, 2);
  assert.equal(navigations[1].summary, true);
  assert.equal(memo.value, 'new draft');
  state.register.dispose();
});

test('a summary canceled outside the register is retried once the displayed register is available', async () => {
  const first = deferred(), navigations = [];
  const state = harness(1, {paged: true, navigate: (url, details) => {
    navigations.push(details);
    return navigations.length === 1 ? first.promise : Promise.resolve(true);
  }});
  state.rows[0].controls['[data-transaction-reviewed]'].emit('click');
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  first.resolve(false);
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(navigations.length, 2);
  assert.equal(navigations.every(details => details.summary), true);
  state.document.emit('click');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(navigations.length, 2);
  state.register.dispose();
});

test('unavailable summaries stop after one automatic retry and resume only on a later interaction', async () => {
  const navigations = [];
  const state = harness(1, {paged: true, navigate: async (url, details) => {
    navigations.push(details);
    return false;
  }});
  state.rows[0].controls['[data-transaction-reviewed]'].emit('click');
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(navigations.length, 2);
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(navigations.length, 2);
  state.document.emit('click');
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(navigations.length, 4);
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(navigations.length, 4);
  assert.equal(navigations.every(details => details.summary), true);
  state.register.dispose();
});

test('ordinary memo-only saves under All do not refresh the register or disturb the editor', async () => {
  const state = harness(1, {paged: true});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  state.document.activeElement = memo;
  memo.value = 'Keep the cursor here'; memo.emit('input'); memo.emit('change');
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  assert.equal(state.document.activeElement, memo);
  state.document.activeElement = null;
  memo.emit('blur');
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  state.register.dispose();
});

test('a financial draft restores displayed search and review after canceling their navigation', async () => {
  const pending = deferred();
  const state = harness(1, {paged: true, navigate: () => pending.promise});
  state.controls['#review-filter'].emit('click');
  await tick();
  const search = state.controls['#transaction-search'];
  search.value = 'Unapplied query'; search.emit('input');
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'Keep editing'; memo.emit('input');
  assert.equal(state.controls['#review-filter'].dataset.reviewState, 'all');
  assert.equal(state.reviewLabel.textContent, 'All');
  assert.equal(search.value, '');
  pending.resolve(true);
  state.register.dispose();
});

test('a late memo save cannot replace an open receipt dialog or its selected file', async () => {
  const state = harness(1, {paged: true, search: 'old'});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'changed'; memo.emit('input'); memo.emit('change');
  state.rows[0].controls['[data-transaction-receipt]'].emit('click');
  state.receiptDialog.querySelector('[data-receipt-file]').value = 'Chosen image';
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  assert.equal(state.receiptDialog.open, true);
  assert.equal(state.receiptDialog.querySelector('[data-receipt-file]').value, 'Chosen image');
  state.receiptDialog.close();
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 1);
  state.register.dispose();
});

test('a late save cannot supersede a newer debounced full-history search', async () => {
  const state = harness(1, {paged: true, search: 'old'});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'changed'; memo.emit('input'); memo.emit('change');
  const search = state.controls['#transaction-search'];
  search.value = 'new query'; search.emit('input');
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  assert.equal(search.value, 'new query');
  await new Promise(resolve => setTimeout(resolve, 315));
  assert.deepEqual(state.navigations[0], {url: {q: 'new query', review: 'all'}, details: {focus: '#transaction-search'}});
  state.register.dispose();
});

test('IME search drafts survive earlier saves until composition ends', async () => {
  const state = harness(1, {paged: true, search: 'old'});
  const memo = state.rows[0].controls['[data-transaction-field="note"]'];
  memo.value = 'changed'; memo.emit('input'); memo.emit('change');
  const search = state.controls['#transaction-search'];
  search.value = '新'; search.emit('input', {isComposing: true});
  state.requests[0].resolve({ok: true});
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.equal(state.navigations.length, 0);
  assert.equal(search.value, '新');
  search.emit('compositionend');
  await tick();
  assert.deepEqual(state.navigations[0], {url: {q: '新', review: 'all'}, details: {focus: '#transaction-search'}});
  state.register.dispose();
});

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

test('the artwork account picker uses partial navigation and releases its listeners with the register', async () => {
  const state = harness(1);
  await state.chooseAccount(['savings', 'checking']);
  await tick();
  assert.deepEqual(state.navigations, [{url: {accounts: ['savings', 'checking'], review: 'all'}, details: {focus: '#account-filter-toggle'}}]);
  assert.equal(state.requests.length, 0);
  state.register.dispose();
  assert.equal(state.isPickerDisposed(), true);
  assert.equal(state.isPeriodDisposed(), true);
});

test('period presets and date boundaries navigate within the register without writing transactions', async () => {
  const state = harness(1);
  const selection = {period: 'custom', startDate: '2026-09-10', endDate: '2026-10-10'};
  await state.choosePeriod(selection);
  await tick();
  assert.deepEqual(state.navigations, [{url: {...selection, review: 'all'}, details: {focus: '#transaction-period-toggle'}}]);
  assert.equal(state.requests.length, 0);
  state.register.dispose();
});

test('review filtering cycles all, needs review and reviewed while preserving search and selection counts', () => {
  const state = harness(3);
  state.rows[0].dataset.reviewed = 'true';
  const review = state.controls['#review-filter'];
  review.emit('click');
  assert.deepEqual(state.rows.map(row => row.hidden), [true, false, false]);
  assert.equal(state.reviewLabel.textContent, 'Needs review');
  assert.equal(state.rows[0].controls['[data-select-transaction]'].checked, false);
  assert.match(state.controls['#transaction-count'].textContent, /^2 transactions/);
  review.emit('click');
  assert.deepEqual(state.rows.map(row => row.hidden), [false, true, true]);
  assert.equal(state.reviewLabel.textContent, 'Reviewed');
  assert.match(state.controls['#transaction-count'].textContent, /^1 transaction/);
  state.controls['#transaction-search'].value = 'payee 1';
  state.controls['#transaction-search'].emit('input');
  assert.deepEqual(state.rows.map(row => row.hidden), [true, true, true]);
  review.emit('click');
  assert.deepEqual(state.rows.map(row => row.hidden), [true, false, true]);
  assert.equal(state.reviewLabel.textContent, 'All');
  assert.match(state.controls['#transaction-count'].textContent, /^1 transaction/);
  assert.equal(state.requests.length, 0);
  assert.equal(state.history.length, 3);
  assert.deepEqual(state.contexts.map(value => value.reviewFilter), ['needs-review', 'reviewed', 'all']);
  state.register.dispose();
});

test('marking a transaction reviewed immediately removes it from the Needs review view after save', async () => {
  const state = harness(2);
  state.controls['#review-filter'].emit('click');
  state.rows[0].controls['[data-transaction-reviewed]'].emit('click');
  assert.deepEqual(state.requests[0].body, {workspace: 'personal', reviewed: true});
  state.requests[0].resolve({ok: true});
  await tick();
  assert.equal(state.rows[0].hidden, true);
  assert.equal(state.rows[1].hidden, false);
  assert.match(state.controls['#transaction-count'].textContent, /^1 transaction/);
  state.register.dispose();
});

test('rapid account and period choices retain both pending filter selections', async () => {
  const state = harness(1);
  state.chooseAccount(['checking', 'savings']);
  state.choosePeriod({period: 'custom', startDate: '2026-10-01', endDate: ''});
  await tick();
  assert.equal(state.navigations.length, 1);
  assert.deepEqual(state.navigations.at(-1), {
    url: {accounts: ['checking', 'savings'], period: 'custom', startDate: '2026-10-01', endDate: '', review: 'all'},
    details: {focus: '#transaction-period-toggle'},
  });
  state.register.dispose();
});

test('review changes while a filter request is pending reissue the latest combined filters', async () => {
  const pending = [], changes = [];
  const state = harness(1, {navigate: (url, details) => {
    const request = deferred();
    pending.push(request);
    changes.push({url: JSON.parse(JSON.stringify(url)), details: JSON.parse(JSON.stringify(details))});
    return request.promise;
  }});
  state.chooseAccount(['checking', 'savings']);
  state.choosePeriod({period: 'custom', startDate: '2026-10-01', endDate: ''});
  await tick();
  assert.equal(pending.length, 1);
  state.controls['#review-filter'].emit('click');
  assert.equal(state.reviewLabel.textContent, 'Needs review');
  await tick();
  assert.equal(pending.length, 2);
  assert.deepEqual(changes.at(-1), {
    url: {accounts: ['checking', 'savings'], period: 'custom', startDate: '2026-10-01', endDate: '', review: 'needs-review'},
    details: {focus: '#review-filter'},
  });
  assert.equal(state.history.length, 0);
  pending[1].resolve(true);
  pending[0].resolve(true);
  await tick();
  assert.equal(state.requests.length, 0);
  state.register.dispose();
});

test('a review change before the filter save barrier finishes suppresses the earlier navigation', async () => {
  const state = harness(1);
  state.choosePeriod({period: 'previous-month', month: 'current-month'});
  state.controls['#review-filter'].emit('click');
  await tick();
  assert.deepEqual(state.navigations, [{
    url: {period: 'previous-month', month: 'current-month', review: 'needs-review'},
    details: {focus: '#review-filter'},
  }]);
  state.register.dispose();
});

test('review changes during sidebar navigation preserve the pending account and date range', async () => {
  const destination = new URL('https://localhost:5173/budgets/home/savings/transactions/current-month/usd?period=custom&start_date=2026-09-10&end_date=2026-10-10');
  const pending = [], navigations = [];
  const state = harness(1, {pendingDestination: () => destination, navigate: (url, details) => {
    const request = deferred();
    pending.push(request);
    navigations.push({url: String(url), details: JSON.parse(JSON.stringify(details))});
    return request.promise;
  }});
  state.controls['#review-filter'].emit('click');
  await tick();
  const first = new URL(navigations[0].url);
  assert.equal(first.pathname, destination.pathname);
  assert.equal(first.searchParams.get('start_date'), '2026-09-10');
  assert.equal(first.searchParams.get('end_date'), '2026-10-10');
  assert.equal(first.searchParams.get('review'), 'needs-review');
  state.controls['#review-filter'].emit('click');
  await tick();
  const latest = new URL(navigations.at(-1).url);
  assert.equal(latest.pathname, destination.pathname);
  assert.equal(latest.searchParams.get('period'), 'custom');
  assert.equal(latest.searchParams.get('start_date'), '2026-09-10');
  assert.equal(latest.searchParams.get('end_date'), '2026-10-10');
  assert.equal(latest.searchParams.get('review'), 'reviewed');
  assert.equal(destination.searchParams.has('review'), false);
  assert.equal(state.history.length, 0);
  for (const request of pending) request.resolve(true);
  await tick();
  state.register.dispose();
});

test('appended rows retain editing, receipt markers and ID de-duplication', async () => {
  const state = harness(1, {paged: true, stream: true, receiptIndexScoped: true});
  const added = state.makeRow(100);
  state.stream.append([added], 'next');
  state.stream.append([added], 'next');
  assert.equal(state.rows.length, 2);
  assert.equal(state.receiptRequests.length, 2);
  assert.deepEqual(state.receiptRequests[1].body.transactionIds, ['transaction-100']);
  added.controls['[data-transaction-field="note"]'].value = 'Memo beyond the first chunk';
  added.controls['[data-transaction-field="note"]'].emit('input');
  assert.equal(state.stream.ready(), false);
  const flushed = state.register.flush();
  assert.equal(state.requests[0].path, '/transactions/transaction-100');
  assert.equal(state.requests[0].body.note, 'Memo beyond the first chunk');
  state.requests[0].resolve({ok: true});
  assert.equal(await flushed, true);
  assert.equal(state.stream.ready(), true);
  state.register.dispose();
});
