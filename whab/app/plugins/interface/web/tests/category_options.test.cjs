/* Shared native options are materialized only when their select is opened. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-register.js'), 'utf8');
const context = {};
vm.runInNewContext(source.slice(0, source.indexOf('export function initTransactionRegister'))
  .replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), context);
const {hydrateTransactionCategories} = context;

function option(value, kind = 'expense', label = value) {
  return {value, label, dataset: {categoryKind: kind}, disabled: false,
    cloneNode() {return option(this.value, this.dataset.categoryKind, this.label);}};
}
function fragment(values) {
  const rows = values.map(value => value.cloneNode(true));
  const groups = [{children: rows, removed: false, remove() {this.removed = true;}}];
  for (const row of rows) row.remove = () => rows.splice(rows.indexOf(row), 1);
  return {rows, groups, querySelectorAll: selector => selector === 'option' ? [...rows] : groups,
    append: row => rows.push(row)};
}
function harness(value, {paired = false, currentKind = 'expense', onlyTransfer = false} = {}) {
  let lazy = true;
  const placeholder = option('', 'expense', paired ? 'Transfer (matched)' : 'Uncategorized');
  const options = [...(onlyTransfer ? [] : [option('food'), option('rent'), option('salary', 'income'), option('opening', 'opening_balance')]), option('transfer', 'transfer')];
  const select = {value, options: [placeholder, ...(value ? [option(value, currentKind)] : [])],
    hasAttribute: name => name === 'data-category-options-lazy' && lazy,
    removeAttribute: () => {lazy = false;}, replacements: [],
    closest: () => paired ? {dataset: {transferPairValid: 'true'}} : null,
    replaceChildren(...nodes) {this.replacements.push(nodes); this.options = [nodes[0], ...nodes[1].rows]; this.value = '';}};
  const template = {content: {cloneNode: () => fragment(options)}};
  return {select, template, placeholder, options};
}

for (const value of ['', 'food']) test(`unmatched category picker excludes Transfer and preserves ${value || 'empty'} selection`, () => {
  const state = harness(value);
  hydrateTransactionCategories(state.select, state.template);
  assert.equal(state.select.value, value);
  assert.deepEqual(state.select.options.map(row => row.value), ['', 'food', 'rent', 'salary', 'opening']);
  assert.equal(state.select.options[0].label, 'Uncategorized');
  assert.notEqual(state.select.options[0], state.placeholder);
  assert.equal(state.select.hasAttribute('data-category-options-lazy'), false);
});

test('confirmed pairs offer only Transfer categories and the matched empty option', () => {
  const state = harness('transfer', {paired: true, currentKind: 'transfer'});
  hydrateTransactionCategories(state.select, state.template);
  assert.deepEqual(state.select.options.map(row => row.value), ['', 'transfer']);
  assert.equal(state.select.value, 'transfer');
  assert.equal(state.select.options[0].label, 'Transfer (matched)');
});

test('legacy invalid and missing saved classifications remain visible but disabled', () => {
  for (const [value, paired, currentKind] of [['transfer', false, 'transfer'], ['rent', true, 'expense'], ['missing', false, 'expense']]) {
    const state = harness(value, {paired, currentKind});
    hydrateTransactionCategories(state.select, state.template);
    assert.equal(state.select.value, value);
    const current = state.select.options.find(row => row.value === value);
    assert.equal(current.disabled, true);
    assert.equal(state.select.options.filter(row => row.value === value).length, 1);
  }
});

test('removed transfer options do not leave empty groups in a new transaction picker', () => {
  const state = harness('', {onlyTransfer: true});
  hydrateTransactionCategories(state.select, state.template);
  assert.deepEqual(state.select.options.map(row => row.value), ['']);
  assert.equal(state.select.replacements[0][1].groups[0].removed, true);
});

test('a link affordance without validated pair proof does not offer Transfer categories', () => {
  const state = harness('');
  state.select.closest = () => ({dataset: {transferPairValid: 'false'}, querySelector: () => ({})});
  hydrateTransactionCategories(state.select, state.template);
  assert.equal(state.select.options.some(row => row.dataset.categoryKind === 'transfer'), false);
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
