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
  const navigations = [];
  const contexts = [], history = [];
  let pickerChoose, pickerDisposed = false, periodChoose, periodDisposed = false;
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
    '[data-receipt-form]', '[data-receipt-payee]', '[data-receipt-empty]'].map(selector => [selector, element()]));
  const receiptDialog = element({querySelector: selector => receiptControls[selector]});
  const controls = Object.fromEntries(['#transaction-search', '#account-filter', '#review-filter',
    '#transaction-count', '[data-select-all]', '[data-transaction-save-status]', '#all-dates-filter',
    '[data-navigation="month"]', '[data-transaction-retry]'].map(selector => [selector, element()]));
  controls['#transaction-search'].value = options.search || '';
  const searchForm = element(), pageLink = element({href: 'https://localhost:5173/budgets/home/transactions/2026-10/usd?tx_page=2'});
  controls['#transaction-count'].dataset.period = 'All dates';
  const reviewLabel = element();
  controls['#review-filter'].dataset.reviewState = options.review || 'all';
  controls['#review-filter'].querySelector = () => reviewLabel;
  const root = element({dataset: {workspace: 'personal', ...(options.paged ? {transactionPaged: 'true', transactionPage: '1', transactionRequestedPage: '1'} : {})},
    querySelectorAll: selector => selector === '.transaction-row' ? rowList : selector === '[data-transaction-page]' ? [pageLink] : [],
    querySelector: selector => ({...controls, '[data-transaction-selection-bar]': selectionBar,
      '[data-transaction-feedback]': feedback, '[data-transaction-receipt-dialog]': receiptDialog,
      '[data-transaction-search-form]': searchForm})[selector]});
  const window = element();
  window.history = {state: {}, replaceState: (...args) => history.push(args)};
  const context = {document: {...element(), querySelector: () => root}, window, setTimeout, clearTimeout, URL,
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
      if (path.startsWith('/receipts?')) return Promise.resolve({supported: false, transactionIds: []});
      const pending = deferred();
      requests.push({path, body: JSON.parse(JSON.stringify(body)), method, ...pending});
      return pending.promise;
    },
  };
  vm.runInNewContext(source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), context);
  const register = context.initTransactionRegister({navigate: async (url, details) => {
    navigations.push({url: JSON.parse(JSON.stringify(url)), details: JSON.parse(JSON.stringify(details))});
    return true;
  }, ...options});
  return {register, requests, rows: rowList, bulk, window, feedback, feedbackText, controls, navigations,
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

test('review changes under All refresh server counts without reload and wait for memo focus to leave', async () => {
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
  assert.deepEqual(state.navigations[0].details, {focus: false, refresh: true, history: 'replace'});
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
