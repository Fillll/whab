const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const context = {BigInt, Number, AbortController, Event};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-grid.js'), 'utf8').replaceAll('export function ', 'function '), context);
const {parseRegisterAmount, gridDestination} = context;
test('register signed amounts preserve currency precision and core spending sign', () => {
  assert.equal(parseRegisterAmount('-12.34', 2), 1234);
  assert.equal(parseRegisterAmount('+12.3', 2), -1230);
  assert.equal(parseRegisterAmount('12', 0), -12);
  assert.equal(parseRegisterAmount('-0.001', 3), 1);
  assert.equal(parseRegisterAmount('-0.00', 2, true), 0);
  for (const amount of ['0', '-0.00', '12.345', '1e3', 'NaN', '1,000', '10000000000.01']) assert.throws(() => parseRegisterAmount(amount, 2));
  assert.throws(() => parseRegisterAmount('1.1', 0));
});
test('arrows retain the actual column while horizontal navigation skips derived cells', () => {
  const rows = Array.from({length: 3}, () => ({cells: []}));
  for (const row of rows) row.cells = Array.from({length: 5}, (_, cellIndex) => ({cellIndex, parentElement: row, querySelector: () => cellIndex === 3 ? null : {}}));
  const memo = rows[1].cells[2];
  assert.equal(gridDestination(rows, memo, 'ArrowUp'), rows[0].cells[2]);
  assert.equal(gridDestination(rows, memo, 'ArrowDown'), rows[2].cells[2]);
  assert.equal(gridDestination(rows, memo, 'ArrowRight'), rows[1].cells[4]);
  assert.equal(gridDestination(rows, rows[1].cells[4], 'ArrowLeft'), memo);
  assert.equal(gridDestination(rows, rows[1].cells[4], 'Tab'), rows[2].cells[0]);
  assert.equal(gridDestination(rows, rows[1].cells[0], 'Tab', true), rows[0].cells[4]);
  assert.equal(gridDestination(rows, memo, 'Home'), rows[1].cells[0]);
  assert.equal(gridDestination(rows, memo, 'End'), rows[1].cells[4]);
  assert.equal(gridDestination(rows, rows[0].cells[2], 'ArrowUp'), undefined);
});

test('one click edits an imported field and Up/Down moves to the next row without F2', () => {
  const listeners = {}, rows = []; let active;
  for (let index = 0; index < 2; index++) {
    const row = {cells: [], hidden: false, dataset: {source: 'ynab'}};
    const cell = {parentElement: row, cellIndex: 0, classList: {add() {}, remove() {}},
      focus() {active = cell;}, scrollIntoView() {}, closest() {return cell;}};
    const control = {value: 'Imported memo', dataset: {}, readOnly: false, changes: 0,
      matches(selector) {return selector.split(',').some(value => value.trim() === 'textarea');},
      closest(selector) {return selector === 'td' ? cell : null;}, focus() {active = control;},
      select() {this.selectionStart = 0; this.selectionEnd = this.value.length;},
      dispatchEvent(event) {if (event.type === 'change') this.changes += 1;}};
    cell.querySelector = () => control; cell.control = control; row.cells.push(cell); rows.push(row);
  }
  const table = {tBodies: [{rows}], addEventListener(name, callback) {listeners[name] = callback;}};
  const grid = context.initTransactionGrid(table);
  const memo = rows[0].cells[0].control;
  assert.equal(memo.readOnly, false);
  let prevented = false;
  listeners.pointerdown({target: memo, preventDefault() {prevented = true;}});
  assert.equal(prevented, false); assert.equal(active, memo); assert.equal(memo.readOnly, false);
  listeners.keydown({target: memo, key: 'ArrowDown', preventDefault() {}, stopPropagation() {}});
  assert.equal(active, rows[1].cells[0]); assert.equal(memo.changes, 1);
  listeners.keydown({target: rows[1].cells[0], key: 'F2', preventDefault() {}, stopPropagation() {}});
  const second = rows[1].cells[0].control;
  listeners.keydown({target: second, key: 'ArrowUp', preventDefault() {}, stopPropagation() {}});
  assert.equal(active, second, 'Explicit F2 keeps cursor movement within the editor');
  grid.dispose();
});
