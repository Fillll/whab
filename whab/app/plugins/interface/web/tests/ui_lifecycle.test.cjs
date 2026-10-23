const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/ui-lifecycle.js'), 'utf8');
const generation = 'a'.repeat(64);
function harness(states = []) {
  const calls = [], holds = [];
  let dirty = false, ready = true, reloads = 0, queued = null;
  const context = {setTimeout, clearTimeout};
  vm.runInNewContext(source.replace('export function', 'function') + ';this.create = createUIViews;', context);
  const views = context.create({generation, isDirty: () => dirty,
    ready: async () => typeof ready === 'function' ? ready() : ready,
    freeze: value => holds.push(value), reload: () => reloads++,
    schedule: value => {queued = value;}, cancel: () => {queued = null;},
    request: async (route, body, method) => {
      calls.push({route, body: JSON.parse(JSON.stringify(body)), method});
      const value = states.shift();
      if (value instanceof Error) throw value;
      return value || {view: 'v', pending: null, reload: false};
    }});
  return {views, calls, holds, setDirty: value => {dirty = value;}, setReady: value => {ready = value;},
    get reloads() {return reloads;}, get queued() {return queued;}};
}
test('a clean view freezes before flushing and acknowledging only the current proposal', async () => {
  const state = harness([{view: 'v', pending: {id: 'p'}}, {pending: {id: 'p'}}]);
  state.setReady(() => {assert.deepEqual(state.holds, [true]); return true;});
  await state.views.tick();
  assert.deepEqual(state.calls, [
    {route: '', method: 'POST', body: {generation, dirty: false}},
    {route: '/v', method: 'PUT', body: {generation, dirty: false, prepared: 'p'}},
  ]);
  assert.equal(typeof state.queued, 'function');
});
test('an unsaved form defers the proposal and remains editable', async () => {
  const state = harness([{view: 'v', pending: {id: 'p'}}]);
  state.setDirty(true); state.setReady(false);
  await state.views.tick();
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].body.dirty, true);
  assert.deepEqual(state.holds, [true, false]);
  assert.equal(state.reloads, 0);
});
test('a canceled proposal releases the old view without reloading', async () => {
  const state = harness([{view: 'v', pending: {id: 'p'}}, {pending: null}, {pending: null}]);
  await state.views.tick(); await state.views.tick();
  assert.deepEqual(state.holds, [true, false]);
  assert.equal(state.calls.at(-1).body.prepared, null);
  assert.equal(state.reloads, 0);
});
test('a clean committed view flushes and reloads once', async () => {
  const state = harness([{view: 'v', pending: null, reload: true}]);
  await state.views.tick(); await state.views.tick();
  assert.equal(state.reloads, 1);
  assert.equal(state.calls.length, 1);
  assert.equal(state.queued, null);
});
test('an old offline view with a draft ignores a reload instruction', async () => {
  const state = harness([{view: 'v', pending: null, reload: true}]);
  state.setDirty(true);
  await state.views.tick();
  assert.equal(state.reloads, 0);
  assert.deepEqual(state.holds, []);
});
test('an edit arriving during a flush prevents both acknowledgment and reload', async () => {
  const state = harness([{view: 'v', pending: {id: 'p'}, reload: true}]);
  state.setReady(() => {state.setDirty(true); return true;});
  await state.views.tick();
  assert.equal(state.calls.length, 1);
  assert.equal(state.reloads, 0);
  assert.deepEqual(state.holds, [true, false]);
});
test('network failure after acknowledgment releases input; a later proposal is rechecked', async () => {
  const state = harness([{view: 'v', pending: {id: 'p'}}, new Error('offline'),
    {pending: {id: 'p'}}, {pending: {id: 'p'}}]);
  await state.views.tick(); await state.views.tick();
  assert.deepEqual(state.holds, [true, false, true]);
  assert.equal(state.calls.filter(row => row.body.prepared === 'p').length, 2);
  state.views.stop();
  assert.equal(state.queued, null);
  assert.equal(state.holds.at(-1), false);
});
test('unknown expired views register again without changing their generation', async () => {
  const error = new Error('expired'); error.code = 'ui_view_unknown';
  const state = harness([{view: 'v', pending: null}, error, {view: 'new', pending: null}]);
  await state.views.tick(); await state.views.tick(); await state.views.tick();
  assert.equal(state.calls.at(-1).method, 'POST');
  assert.equal(state.calls.at(-1).body.generation, generation);
});
test('legacy contexts do not start coordination and overlapping polls are serialized', async () => {
  const context = {setTimeout, clearTimeout};
  vm.runInNewContext(source.replace('export function', 'function') + ';this.create = createUIViews;', context);
  assert.equal(context.create({generation: null}), null);
  const state = harness();
  await Promise.all([state.views.tick(), state.views.tick()]);
  assert.equal(state.calls.length, 1);
});
