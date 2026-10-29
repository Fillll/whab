const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const context = {AbortController};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-stream.js'), 'utf8').replaceAll('export function ', 'function '), context);
const {createTransactionLoader} = context;
function deferred() {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};}
function harness() {
  const requests = [], appended = [], states = [], refreshes = [];
  let ready = true;
  const loader = createTransactionLoader({next: 'chunk-2', previous: null, revision: 'snapshot-1',
    ready: () => ready, changed: () => refreshes.push(true),
    load(url, signal) {const pending = deferred(); requests.push({url, signal, ...pending}); return pending.promise;},
    append(chunk, direction) {appended.push({chunk, direction});}, state(value) {states.push(value);}});
  return {loader, requests, appended, states, refreshes, ready(value) {ready = value;}};
}
test('only one request runs and successful chunks advance to the end', async () => {
  const h = harness(), first = h.loader.more();
  assert.equal(await h.loader.more(), false); assert.equal(h.requests.length, 1);
  h.requests[0].resolve({rows: ['row-101'], next: 'chunk-3', revision: 'snapshot-1'});
  assert.equal(await first, true); assert.equal(h.loader.next, 'chunk-3');
  const second = h.loader.more(); h.requests[1].resolve({rows: ['row-201'], next: null, revision: 'snapshot-1'});
  assert.equal(await second, true); assert.equal(await h.loader.more(), false);
  assert.equal(h.appended.length, 2); assert.equal(h.states.at(-1), 'done');
});
test('an account/filter cancellation rejects late rows even when fetch ignores abort', async () => {
  const h = harness(), pending = h.loader.more(); h.loader.cancel();
  assert.equal(h.requests[0].signal.aborted, true);
  h.requests[0].resolve({rows: ['stale'], next: 'wrong', revision: 'snapshot-1'});
  assert.equal(await pending, false); assert.equal(h.appended.length, 0); assert.equal(h.loader.next, 'chunk-2');
  const resumed = h.loader.more(); h.requests[1].resolve({rows: ['current'], next: null, revision: 'snapshot-1'});
  assert.equal(await resumed, true);
});
test('a failed load preserves the current position until an explicit retry', async () => {
  const h = harness(), first = h.loader.more(); h.requests[0].reject(new Error('Network unavailable'));
  assert.equal(await first, false); assert.equal(await h.loader.more(), false);
  assert.equal(h.requests.length, 1); assert.equal(h.loader.next, 'chunk-2');
  const retry = h.loader.more('next', true); assert.equal(h.requests[1].url, 'chunk-2');
  h.requests[1].resolve({rows: ['saved-editor-remains'], next: null, revision: 'snapshot-1'});
  assert.equal(await retry, true);
});
test('edits defer responses and retry the same chunk after the draft is saved', async () => {
  const h = harness(); h.ready(false); assert.equal(await h.loader.more(), false);
  h.ready(true); const pending = h.loader.more(); h.ready(false);
  h.requests[0].resolve({rows: ['not-yet'], next: 'chunk-3', revision: 'snapshot-1'});
  assert.equal(await pending, false); assert.equal(h.appended.length, 0); assert.equal(h.loader.next, 'chunk-2');
  h.ready(true); h.loader.mutation(); const current = h.loader.more();
  h.requests[1].resolve({rows: ['safe'], next: null, revision: 'snapshot-2'});
  assert.equal(await current, true);
});
test('unrelated revisions refresh instead of combining snapshots; local ordering edits rescan', async () => {
  const h = harness(), pending = h.loader.more();
  h.requests[0].resolve({rows: ['new-snapshot'], next: null, revision: 'snapshot-2'});
  assert.equal(await pending, false); assert.equal(h.appended.length, 0); assert.equal(h.refreshes.length, 1);
  h.loader.mutation('chunk-1'); const scan = h.loader.more(); assert.equal(h.requests[1].url, 'chunk-1');
  h.requests[1].resolve({rows: ['own-edits'], next: 'chunk-2', revision: 'snapshot-3'});
  assert.equal(await scan, true); assert.equal(h.refreshes.length, 1);
});
test('disposing a view prevents stale appends and stops further requests', async () => {
  const h = harness(), pending = h.loader.more(); h.loader.dispose();
  h.requests[0].resolve({rows: ['discarded'], next: 'chunk-3', revision: 'snapshot-1'});
  assert.equal(await pending, false); assert.equal(await h.loader.more(), false); assert.equal(h.appended.length, 0);
});
