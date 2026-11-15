/* Transaction account artwork follows the visible account without provider requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function harness() {
  const hydrated = [];
  const context = {bindLogos: row => hydrated.push(row)};
  const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-register.js'), 'utf8');
  vm.runInNewContext(source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), context);
  function mark(name) {
    const classes = new Set(['sidebar-bank-mark', 'account-picker-mark']);
    return {name, hidden: false, attributes: {},
      classList: {add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value)},
      cloneNode: () => mark(name),
      setAttribute(key, value) {this.attributes[key] = value;},
      replaceWith(next) {current = next;},
    };
  }
  const options = ['first', '共同 "quoted"', 'unknown'].map(value => ({dataset: {value},
    querySelector: selector => selector === '.sidebar-bank-mark' ? mark(value === 'unknown' ? 'fallback' : value) : {textContent: value},
  }));
  const root = {querySelectorAll: () => options};
  const cell = {title: 'first'};
  let current = mark('first');
  const row = {querySelector: selector => selector === '[data-transaction-account-mark]' ? current : cell};
  return {hydrated, row, cell, options, mark: () => current,
    update: value => context.updateTransactionAccountMark(root, row, value),
    missingMark: () => {current = null;},
  };
}

test('changing the visible account clones its existing local mark and hydrates its lazy image', () => {
  const state = harness();
  state.update('共同 "quoted"');
  assert.equal(state.mark().name, '共同 "quoted"');
  assert.equal(state.cell.title, '共同 "quoted"');
  assert.equal(state.mark().classList.contains('transaction-account-mark'), true);
  assert.equal(state.mark().classList.contains('account-picker-mark'), false);
  assert.equal(state.mark().attributes['data-transaction-account-mark'], '');
  assert.deepEqual(state.hydrated, [state.row]);
  state.update('first');
  assert.equal(state.mark().name, 'first', 'discarding an account draft can restore its original bank mark');
});

test('an unrecognized institution uses its fallback and a disconnected account never keeps another logo', () => {
  const state = harness();
  state.update('unknown');
  assert.equal(state.mark().name, 'fallback');
  state.update('disconnected');
  assert.equal(state.mark().hidden, true);
  assert.equal(state.hydrated.length, 1);
  state.missingMark();
  state.update('first');
  assert.equal(state.hydrated.length, 1);
});
