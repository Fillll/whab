/* Run with node --test plugins/interface/web/tests/form_edit_guard.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
const guard = source.slice(source.indexOf('async function flushEdits()'), source.indexOf('initBudgetSourceLinks();'));
const genericForms = source.slice(source.indexOf('const budgetEdits = initBudgetEdits'), source.indexOf('function updateAccountFields'));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(yes => {resolve = yes;});
  return {promise, resolve};
}
function harness({budgetFlush = async () => true, registerFlush = async () => true} = {}) {
  const order = [], error = {hidden: true}, listeners = {}, requests = [], navigations = [];
  const form = {dataset: {api: '/transactions', method: 'POST'},
    hasAttribute: () => false, querySelectorAll: () => [], querySelector: () => error,
    addEventListener: (type, callback) => {listeners[type] = callback;},
    setAttribute() {}, removeAttribute() {},
  };
  const context = {
    document: {querySelectorAll: () => [form]},
    currentRegister: {flush: async () => {order.push('register'); return registerFlush();}},
    initBudgetEdits: () => ({flush: async () => {order.push('budget'); return budgetFlush();}}),
    formBody: () => ({workspace: 'personal', merchant: 'New transaction'}),
    api: async (path, body, method) => {order.push('mutation'); requests.push({path, body: JSON.parse(JSON.stringify(body)), method});},
    location: {reload: () => {order.push('reload'); navigations.push('reload');},
      assign: value => navigations.push(value)},
    notify() {}, pageUrl() {throw new Error('Unexpected currency navigation.');}, Error,
  };
  vm.runInNewContext(guard + genericForms, context);
  return {order, error, form, requests, navigations,
    submit: () => listeners.submit({preventDefault() {}})};
}

test('a dialog mutation waits for the current transaction save before making its request or reloading', async () => {
  const pending = deferred();
  const state = harness({registerFlush: () => pending.promise});
  const submitted = state.submit();
  await tick();
  assert.deepEqual(state.order, ['budget', 'register']);
  assert.equal(state.requests.length, 0);
  assert.equal(state.navigations.length, 0);
  pending.resolve(true);
  await submitted;
  assert.deepEqual(state.order, ['budget', 'register', 'mutation', 'reload']);
  assert.equal(state.requests.length, 1);
});

test('an unsaved transaction failure keeps the dialog mutation and full reload blocked', async () => {
  const state = harness({registerFlush: async () => false});
  await state.submit();
  assert.deepEqual(state.order, ['budget', 'register']);
  assert.equal(state.error.hidden, false);
  assert.equal(state.error.textContent, 'Save the transaction or budget edit before continuing.');
  assert.equal(state.requests.length, 0);
  assert.equal(state.navigations.length, 0);
  assert.equal(state.form.dataset.busy, undefined);
});

test('a failed budget edit blocks the transaction flush and later mutation', async () => {
  const state = harness({budgetFlush: async () => false});
  await state.submit();
  assert.deepEqual(state.order, ['budget']);
  assert.equal(state.requests.length, 0);
  assert.equal(state.navigations.length, 0);
});
