const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-register.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
function harness({batchError = null} = {}) {
  const batches = [], singles = [];
  let requestId = 0;
  const context = {queueMicrotask, crypto: {randomUUID: () => `batch-${++requestId}`}};
  vm.runInNewContext(source.slice(0, source.indexOf('export function initTransactionRegister'))
    .replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), context);
  const save = context.createReviewBatcher({workspace: 'personal',
    saveBatch(body) {
      const pending = deferred(); batches.push({body, ...pending});
      if (batchError) throw batchError;
      return pending.promise;
    },
    saveOne(id, patch) {const pending = deferred(); singles.push({id, patch: plain(patch), ...pending}); return pending.promise;},
  });
  return {save, batches, singles};
}

test('synchronous pure review writes collect distinct immutable tickets of at most 100 rows', async () => {
  const state = harness();
  const saves = Array.from({length: 201}, (_, index) => state.save(`transaction-${index}`, {reviewed: true}, true));
  assert.equal(state.batches.length, 0);
  await tick();
  assert.deepEqual(state.batches.map(entry => entry.body.transactionIds.length), [100, 100, 1]);
  assert.equal(new Set(state.batches.map(entry => entry.body.requestId)).size, 3);
  for (const batch of state.batches) {
    assert.equal(batch.body.workspace, 'personal');
    assert.equal(batch.body.reviewed, true);
    assert.equal(Object.isFrozen(batch.body), true);
    assert.equal(Object.isFrozen(batch.body.transactionIds), true);
    assert.equal(new Set(batch.body.transactionIds).size, batch.body.transactionIds.length);
    batch.resolve({ok: true});
  }
  await Promise.all(saves);
  assert.equal(state.singles.length, 0);
});

test('opposite review values form separate tickets while mixed or ordinary writes keep their original path', async () => {
  const state = harness();
  const saving = [state.save('first', {reviewed: true}, true), state.save('second', {reviewed: false}, true),
    state.save('mixed', {reviewed: true, note: 'Memo'}, true), state.save('ordinary', {reviewed: true})];
  assert.deepEqual(state.singles.map(entry => entry.patch), [{reviewed: true, note: 'Memo'}, {reviewed: true}]);
  await tick();
  assert.deepEqual(state.batches.map(entry => entry.body.reviewed), [true, false]);
  for (const entry of [...state.batches, ...state.singles]) entry.resolve({ok: true});
  await Promise.all(saving);
});

test('unknown results retain the exact ticket before merged later fields or an opposite review can proceed', async () => {
  const state = harness();
  const original = Promise.allSettled(['first', 'second', 'third'].map(id => state.save(id, {reviewed: true}, true)));
  await tick();
  const body = state.batches[0].body;
  state.batches[0].reject(new Error('Response was lost.'));
  assert.equal((await original).every(result => result.status === 'rejected'), true);
  assert.equal(state.singles.length, 0);
  const retries = [state.save('first', {reviewed: false, note: 'Latest memo', categoryId: 'rent'}),
    state.save('second', {reviewed: false})];
  await tick();
  assert.equal(state.batches.length, 2);
  assert.equal(state.batches[1].body, body);
  assert.equal(state.singles.length, 0);
  state.batches[1].resolve({ok: true});
  await tick();
  assert.deepEqual(state.singles.map(({id, patch}) => ({id, patch})), [
    {id: 'first', patch: {reviewed: false, note: 'Latest memo', categoryId: 'rent'}},
    {id: 'second', patch: {reviewed: false}},
  ]);
  for (const entry of state.singles) entry.resolve({ok: true});
  await Promise.all(retries);
  await state.save('third', {reviewed: true});
  assert.equal(state.batches.length, 2);
  assert.equal(state.singles.length, 2);
});

test('confirmed tickets remain shared until each writer also saves its later fields', async () => {
  const state = harness();
  const original = Promise.allSettled(['first', 'second'].map(id => state.save(id, {reviewed: true}, true)));
  await tick(); state.batches[0].reject(new Error('Unknown result.')); await original;
  const retry = state.save('first', {reviewed: true, note: 'Keep this'});
  const rejected = assert.rejects(retry, /Memo save failed/);
  await tick(); state.batches[1].resolve({ok: true});
  await tick(); state.singles[0].reject(new Error('Memo save failed.')); await rejected;
  const final = state.save('first', {reviewed: true, note: 'Keep this'});
  await tick();
  assert.equal(state.batches.length, 2);
  assert.deepEqual(state.singles[1].patch, {note: 'Keep this'});
  state.singles[1].resolve({ok: true}); await final;
  await state.save('second', {reviewed: true});
  assert.equal(state.batches.length, 2);
});

test('malformed acknowledgements fail closed and retry the same membership and request identity', async () => {
  const state = harness();
  const original = state.save('first', {reviewed: true}, true);
  const rejected = assert.rejects(original, /could not be confirmed/);
  await tick(); state.batches[0].resolve({}); await rejected;
  const retry = state.save('first', {reviewed: true});
  await tick();
  assert.equal(state.batches[1].body, state.batches[0].body);
  state.batches[1].resolve({ok: true}); await retry;
  assert.equal(state.singles.length, 0);
});

test('a synchronous dispatch failure rejects the whole cohort once without splitting its writes', async () => {
  const state = harness({batchError: new Error('Request could not start.')});
  const failed = await Promise.allSettled(['first', 'second', 'third']
    .map(id => state.save(id, {reviewed: true}, true)));
  assert.equal(failed.every(result => result.status === 'rejected' && result.reason.message === 'Request could not start.'), true);
  assert.equal(state.batches.length, 1);
  assert.equal(state.singles.length, 0);
  const retry = await Promise.allSettled(['first', 'second', 'third']
    .map(id => state.save(id, {reviewed: true})));
  assert.equal(retry.every(result => result.status === 'rejected'), true);
  assert.equal(state.batches.length, 2);
  assert.equal(state.batches[1].body, state.batches[0].body);
});

test('late writers create another ticket without joining an already dispatched or failed cohort', async () => {
  const state = harness();
  const first = state.save('first', {reviewed: true}, true);
  const rejected = assert.rejects(first, /Atomic batch rejected/);
  await tick();
  const second = state.save('second', {reviewed: true}, true);
  await tick();
  assert.deepEqual(state.batches.map(entry => [...entry.body.transactionIds]), [['first'], ['second']]);
  assert.notEqual(state.batches[0].body.requestId, state.batches[1].body.requestId);
  state.batches[0].reject(new Error('Atomic batch rejected.')); await rejected;
  state.batches[1].resolve({ok: true}); await second;
  assert.equal(state.singles.length, 0);
});
