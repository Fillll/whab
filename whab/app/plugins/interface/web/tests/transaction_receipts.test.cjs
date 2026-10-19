/* Receipt controls run against the actual register module and its shared DOM fixture. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const helpers = fs.readFileSync(path.join(__dirname, 'transaction_register.test.cjs'), 'utf8').split('\ntest(')[0];
const runtime = {require, __dirname, URL, setImmediate, setTimeout, clearTimeout, queueMicrotask};
vm.runInNewContext(helpers, runtime);
const {harness, deferred} = runtime;
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));
const receipt = row => row.controls['[data-transaction-receipt]'];
const uuid = index => `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;

function respond(request, {ok = true, blob = {fictional: 'raster'}, error} = {}) {
  request.resolve({ok, blob: async () => blob, json: async () => error ? {error} : {ok: true}});
}

async function ready({exists = false, ...options} = {}) {
  const index = deferred();
  const state = harness(1, {receiptIndexScoped: true, receiptIndex: index, ...options});
  index.resolve({supported: true, transactionIds: exists ? [state.rows[0].dataset.transactionId] : []});
  await tick();
  return state;
}

test('a 100-row register requests only its current receipt IDs from a 10000-receipt workspace', async () => {
  const allIds = Array.from({length: 10000}, (_, index) => uuid(index));
  const index = deferred();
  const state = harness(100, {receiptIndexScoped: true, receiptIndex: index, transactionIds: allIds.slice(5000, 5100)});
  assert.equal(state.receiptRequests.length, 1);
  assert.equal(state.imageRequests.length, 0);
  const request = state.receiptRequests[0];
  assert.equal(request.path, '/receipts/index');
  assert.equal(request.method, 'POST');
  assert.deepEqual(plain(request.body), {workspace: 'personal', transactionIds: allIds.slice(5000, 5100)});
  assert.equal(state.rows.every(row => receipt(row).disabled), true);
  const existing = new Set(allIds);
  const response = {supported: true, transactionIds: request.body.transactionIds.filter(id => existing.has(id))};
  assert.equal(Buffer.byteLength(JSON.stringify(response)), 3937);
  assert.equal(Buffer.byteLength(JSON.stringify({supported: true, transactionIds: allIds})), 390037);
  index.resolve(response); await tick();
  assert.equal(state.rows.every(row => !receipt(row).disabled && receipt(row).classList.contains('has-receipt')), true);
  assert.equal(state.receiptRequests.length, 1);
  assert.equal(state.imageRequests.length, 0);
  state.register.dispose();
});

test('hosts without an exact scoped-index flag keep the existing workspace GET', async () => {
  for (const flag of [undefined, false, 'true']) {
    const index = deferred();
    const state = harness(2, {receiptIndexScoped: flag, receiptIndex: index, workspace: 'demo'});
    assert.equal(state.receiptRequests.length, 1);
    assert.equal(state.receiptRequests[0].path, '/receipts?workspace=demo');
    assert.equal(state.receiptRequests[0].body, undefined);
    assert.equal(state.receiptRequests[0].method, undefined);
    index.resolve({supported: true, transactionIds: ['transaction-1', 'older-off-page-receipt']}); await tick();
    assert.equal(receipt(state.rows[0]).title, 'Add receipt');
    assert.equal(receipt(state.rows[1]).title, 'View receipt');
    assert.equal(state.rows.every(row => !receipt(row).disabled), true);
    state.register.dispose();
  }
});

test('empty registers skip both scoped and legacy receipt-index requests', () => {
  for (const flag of [true, undefined]) {
    const state = harness(0, {receiptIndexScoped: flag});
    assert.equal(state.receiptRequests.length, 0);
    assert.equal(state.imageRequests.length, 0);
    state.register.dispose();
  }
});

test('unsupported receipt storage keeps controls disabled after the one scoped activation request', async () => {
  const index = deferred();
  const state = harness(2, {receiptIndexScoped: true, receiptIndex: index});
  assert.equal(state.rows.every(row => receipt(row).disabled), true);
  index.resolve({supported: false, transactionIds: []}); await tick();
  assert.equal(state.rows.every(row => receipt(row).disabled && receipt(row).title === 'Receipts are unavailable with this storage plugin.'), true);
  assert.equal(state.receiptRequests.length, 1);
  state.register.dispose();
});

test('a scoped-index error stays disabled and never retries through the full workspace GET', async () => {
  const index = deferred();
  const state = harness(2, {receiptIndexScoped: true, receiptIndex: index});
  index.reject(new Error('Scoped route unavailable.')); await tick();
  assert.equal(state.rows.every(row => receipt(row).disabled && receipt(row).title === 'Receipts could not be loaded.'), true);
  assert.equal(await state.register.flush(), true);
  assert.equal(state.receiptRequests.length, 1);
  assert.equal(state.imageRequests.length, 0);
  state.register.dispose();
});

test('late scoped-index success or failure cannot activate a disposed register', async () => {
  for (const failed of [false, true]) {
    const index = deferred();
    const state = harness(2, {receiptIndexScoped: true, receiptIndex: index});
    state.register.dispose();
    if (failed) index.reject(new Error('Late failure.'));
    else index.resolve({supported: true, transactionIds: ['transaction-0']});
    await tick();
    assert.equal(state.rows.every(row => receipt(row).disabled && receipt(row).title === 'Receipt' && !receipt(row).classList.contains('has-receipt')), true);
    assert.equal(state.imageRequests.length, 0);
  }
});

test('activated receipt markers open only their scoped private image and release its preview on close', async () => {
  const state = await ready({exists: true, workspace: 'demo', transactionIds: ['fictional/id ?']});
  receipt(state.rows[0]).emit('click');
  assert.equal(state.receiptDialog.open, true);
  assert.equal(state.imageRequests.length, 1);
  assert.equal(state.imageRequests[0].path, '/transactions/fictional%2Fid%20%3F/receipt?workspace=demo');
  assert.deepEqual(plain(state.imageRequests[0].details), {cache: 'no-store'});
  assert.equal(state.receiptControls['[data-receipt-remove]'].hidden, false);
  assert.equal(state.receiptControls['[data-receipt-status]'].textContent, 'Loading receipt…');
  const blob = {fictional: 'private raster'};
  respond(state.imageRequests[0], {blob}); await tick();
  assert.equal(state.receiptControls['[data-receipt-preview]'].src, 'blob:fixture-1');
  assert.equal(state.receiptControls['[data-receipt-preview]'].hidden, false);
  assert.equal(state.objectUrls[0].blob, blob);
  state.receiptControls['[data-receipt-close]'].emit('click');
  assert.equal(state.receiptDialog.open, false);
  assert.equal(state.receiptControls['[data-receipt-preview]'].hidden, true);
  assert.deepEqual([...state.revokedUrls], ['blob:fixture-1']);
  assert.equal(state.requests.length, 0);
  assert.equal(state.receiptRequests.length, 1);
  state.register.dispose();
});

test('upload keeps controls busy and the save barrier active until its private preview reload completes', async () => {
  let mutations = 0;
  const state = await ready({onMutation: () => {mutations += 1;}});
  receipt(state.rows[0]).emit('click');
  assert.equal(state.imageRequests.length, 0);
  assert.equal(state.receiptControls['[data-receipt-empty]'].hidden, false);
  const file = {name: 'fictional.png', type: 'image/png', size: 100};
  const input = state.receiptControls['[data-receipt-file]'];
  input.files = [file]; input.value = 'fictional.png';
  state.receiptControls['[data-receipt-form]'].emit('submit');
  const saving = state.register.flush();
  assert.equal(state.imageRequests.length, 1);
  assert.equal(state.imageRequests[0].details.method, 'PUT');
  assert.equal(state.imageRequests[0].details.body, file);
  assert.deepEqual(plain(state.imageRequests[0].details.headers), {'Content-Type': 'image/png'});
  assert.equal(state.receiptDialog.querySelectorAll('button,input').every(control => control.disabled), true);
  assert.equal(state.receiptDialog.emit('cancel').event.prevented, true);
  assert.equal(state.window.emit('beforeunload').event.prevented, true);
  assert.equal(state.register.dirty, true);
  respond(state.imageRequests[0]); await tick();
  assert.equal(state.imageRequests.length, 2);
  assert.deepEqual(plain(state.imageRequests[1].details), {cache: 'no-store'});
  assert.equal(receipt(state.rows[0]).classList.contains('has-receipt'), true);
  assert.equal(input.value, '');
  assert.equal(state.register.dirty, true);
  respond(state.imageRequests[1]);
  assert.equal(await saving, true);
  assert.equal(state.register.dirty, false);
  assert.equal(state.receiptDialog.querySelectorAll('button,input').every(control => !control.disabled), true);
  assert.equal(state.receiptControls['[data-receipt-status]'].textContent, 'Receipt saved.');
  assert.equal(receipt(state.rows[0]).title, 'View receipt');
  assert.equal(mutations, 1);
  assert.equal(state.requests.length, 0);
  assert.equal(state.receiptRequests.length, 1);
  state.register.dispose();
});

test('removal clears its marker and preview after DELETE without requesting another image or index', async () => {
  let mutations = 0;
  const state = await ready({exists: true, onMutation: () => {mutations += 1;}});
  receipt(state.rows[0]).emit('click'); respond(state.imageRequests[0]); await tick();
  state.receiptControls['[data-receipt-remove]'].emit('click');
  const removing = state.register.flush();
  assert.equal(state.imageRequests.length, 2);
  assert.deepEqual(plain(state.imageRequests[1].details), {method: 'DELETE'});
  assert.equal(receipt(state.rows[0]).classList.contains('has-receipt'), true);
  respond(state.imageRequests[1]);
  assert.equal(await removing, true);
  assert.equal(receipt(state.rows[0]).classList.contains('has-receipt'), false);
  assert.equal(receipt(state.rows[0]).title, 'Add receipt');
  assert.equal(state.receiptControls['[data-receipt-preview]'].hidden, true);
  assert.equal(state.receiptControls['[data-receipt-remove]'].hidden, true);
  assert.equal(state.receiptControls['[data-receipt-empty]'].hidden, false);
  assert.equal(state.receiptControls['[data-receipt-status]'].textContent, 'Receipt removed.');
  assert.deepEqual([...state.revokedUrls], ['blob:fixture-1']);
  assert.equal(state.imageRequests.length, 2);
  assert.equal(state.receiptRequests.length, 1);
  assert.equal(mutations, 1);
  state.register.dispose();
});

test('failed image loads retain the marker and late previews cannot reopen after close', async () => {
  const state = await ready({exists: true});
  receipt(state.rows[0]).emit('click'); respond(state.imageRequests[0], {ok: false}); await tick();
  assert.equal(state.receiptControls['[data-receipt-error]'].textContent, 'The receipt could not be loaded.');
  assert.equal(state.receiptControls['[data-receipt-error]'].hidden, false);
  assert.equal(receipt(state.rows[0]).classList.contains('has-receipt'), true);
  state.receiptDialog.close();
  receipt(state.rows[0]).emit('click'); state.receiptDialog.close();
  respond(state.imageRequests[1]); await tick();
  assert.equal(state.objectUrls.length, 0);
  assert.equal(state.receiptControls['[data-receipt-preview]'].hidden, true);
  assert.equal(state.receiptDialog.open, false);
  state.register.dispose();
});

test('invalid file choices stay local and upload/delete failures preserve the previous receipt marker', async () => {
  const invalid = await ready();
  receipt(invalid.rows[0]).emit('click');
  for (const file of [undefined, {type: 'image/svg+xml', size: 1}, {type: 'image/png', size: 5 * 1024 * 1024 + 1}]) {
    invalid.receiptControls['[data-receipt-file]'].files = file ? [file] : [];
    invalid.receiptControls['[data-receipt-form]'].emit('submit');
  }
  assert.equal(invalid.imageRequests.length, 0);
  assert.equal(invalid.register.dirty, false);
  invalid.register.dispose();
  for (const exists of [false, true]) {
    const state = await ready({exists});
    receipt(state.rows[0]).emit('click');
    if (exists) {respond(state.imageRequests[0]); await tick(); state.receiptControls['[data-receipt-remove]'].emit('click');}
    else {
      state.receiptControls['[data-receipt-file]'].files = [{type: 'image/png', size: 100}];
      state.receiptControls['[data-receipt-form]'].emit('submit');
    }
    const saving = state.register.flush();
    respond(state.imageRequests.at(-1), {ok: false, error: 'Private image request failed.'});
    assert.equal(await saving, true);
    assert.equal(state.receiptControls['[data-receipt-error]'].textContent, 'Private image request failed.');
    assert.equal(state.receiptControls['[data-receipt-error]'].hidden, false);
    assert.equal(receipt(state.rows[0]).classList.contains('has-receipt'), exists);
    assert.equal(state.receiptDialog.querySelectorAll('button,input').every(control => !control.disabled), true);
    assert.equal(state.receiptRequests.length, 1);
    state.register.dispose();
  }
});
