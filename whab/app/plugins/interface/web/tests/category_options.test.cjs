/* Shared native options are materialized only when their select is opened. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-register.js'), 'utf8');
const context = {};
vm.runInNewContext(source.slice(0, source.indexOf('export function initTransactionRegister'))
  .replace(/^import .*$/m, '').replaceAll('export function ', 'function '), context);
const {hydrateTransactionCategories} = context;

function harness(value) {
  let lazy = true;
  const placeholder = {value: '', label: 'Income / uncategorized'};
  const options = [{value: 'food', label: 'Food'}, {value: 'rent', label: 'Rent'}];
  const select = {value, options: [{cloneNode: () => ({...placeholder})}],
    hasAttribute: name => name === 'data-category-options-lazy' && lazy,
    removeAttribute: () => {lazy = false;}, replacements: [],
    replaceChildren(...nodes) {this.replacements.push(nodes); this.value = '';}};
  const template = {content: {cloneNode: () => options.map(row => ({...row}))}};
  return {select, template, placeholder, options};
}

test('opening a category picker preserves its selection and contextual empty option', () => {
  for (const value of ['', 'food']) {
    const state = harness(value);
    hydrateTransactionCategories(state.select, state.template);
    assert.equal(state.select.value, value);
    assert.deepEqual(state.select.replacements[0], [state.placeholder, state.options]);
    assert.notEqual(state.select.replacements[0][1], state.options);
    assert.equal(state.select.hasAttribute('data-category-options-lazy'), false);
  }
});

test('already hydrated controls keep their edited value and option nodes', () => {
  const state = harness('rent');
  hydrateTransactionCategories(state.select, state.template);
  state.select.value = 'food';
  hydrateTransactionCategories(state.select, state.template);
  assert.equal(state.select.value, 'food');
  assert.equal(state.select.replacements.length, 1);
});

test('missing controls and templates leave native options intact', () => {
  hydrateTransactionCategories(null, null);
  const state = harness('rent');
  hydrateTransactionCategories(state.select, null);
  hydrateTransactionCategories(state.select, {});
  assert.equal(state.select.value, 'rent');
  assert.equal(state.select.replacements.length, 0);
});
