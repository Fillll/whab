/* Run with node --test plugins/interface/web/tests/transaction_writer.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-register.js'), 'utf8');
const context = {};
vm.runInNewContext(source.slice(0, source.indexOf('export function initTransactionRegister'))
  .replace(/^import .*$/m, '').replaceAll('export function ', 'function '), context);
const {createTransactionWriter} = context;

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = object => JSON.parse(JSON.stringify(object));

function harness() {
  const saves = [], committed = [], states = [];
  let active = 0, maximumActive = 0;
  const writer = createTransactionWriter(patch => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    const pending = deferred();
    saves.push({patch: plain(patch), resolve: () => {active -= 1; pending.resolve();},
      reject: message => {active -= 1; pending.reject(new Error(message));}});
    return pending.promise;
  }, {saved: patch => committed.push(plain(patch)),
    state: (state, error) => states.push({state, message: error?.message})});
  return {writer, saves, committed, states, maximumActive: () => maximumActive};
}

test('a clean register flush completes without any network request', async () => {
  const state = harness();
  assert.equal(await state.writer.flush(), true);
  assert.equal(state.saves.length, 0);
  assert.equal(state.writer.dirty, false);
});

test('in-flight edits serialize and coalesce the latest memo and category fields', async () => {
  const state = harness();
  const pending = state.writer.push({note: 'First draft'});
  state.writer.push({note: 'Second draft'});
  state.writer.push({categoryId: 'utilities'});
  state.writer.push({note: 'Final draft\nWith receipt details'});
  assert.equal(state.saves.length, 1);
  assert.equal(state.writer.dirty, true);
  state.saves[0].resolve();
  await tick();
  assert.deepEqual(state.saves[1].patch, {note: 'Final draft\nWith receipt details', categoryId: 'utilities'});
  state.saves[1].resolve();
  assert.equal(await pending, true);
  assert.equal(state.maximumActive(), 1);
  assert.deepEqual(state.committed, [{note: 'First draft'}, {note: 'Final draft\nWith receipt details', categoryId: 'utilities'}]);
  assert.equal(state.writer.dirty, false);
});

test('failed saves retain newer drafts and retry only the latest merged transaction values', async () => {
  const state = harness();
  const pending = state.writer.push({note: 'Original', reviewed: false});
  state.writer.push({note: 'Latest', categoryId: 'rent'});
  state.saves[0].reject('Connection interrupted');
  assert.equal(await pending, false);
  assert.equal(state.writer.dirty, true);
  assert.equal(state.committed.length, 0);
  assert.deepEqual(state.states.at(-1), {state: 'error', message: 'Connection interrupted'});
  const retried = state.writer.flush();
  assert.deepEqual(state.saves[1].patch, {note: 'Latest', reviewed: false, categoryId: 'rent'});
  state.saves[1].resolve();
  assert.equal(await retried, true);
  assert.equal(state.writer.dirty, false);
  assert.equal(state.states.at(-1).state, 'saved');
});

test('navigation flush waits for all queued field writes to finish', async () => {
  const state = harness();
  state.writer.push({note: 'Updated memo'});
  let finished = false;
  const navigating = state.writer.flush().then(result => {finished = true; return result;});
  state.writer.push({reviewed: true});
  state.saves[0].resolve();
  await tick();
  assert.equal(finished, false);
  assert.deepEqual(state.saves[1].patch, {reviewed: true});
  state.saves[1].resolve();
  assert.equal(await navigating, true);
  assert.equal(finished, true);
});

test('a later edit can retry a failure without losing retained fields', async () => {
  const state = harness();
  const first = state.writer.push({categoryId: 'food'});
  state.saves[0].reject('Retry later');
  assert.equal(await first, false);
  const retry = state.writer.push({note: 'Lunch'});
  assert.deepEqual(state.saves[1].patch, {categoryId: 'food', note: 'Lunch'});
  state.saves[1].resolve();
  assert.equal(await retry, true);
  assert.equal(state.writer.dirty, false);
});
