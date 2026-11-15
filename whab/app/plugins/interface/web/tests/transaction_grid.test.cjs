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

test('amount privacy keeps drafts unsaved and makes keyboard editing reveal amounts first', () => {
  const listeners = {}, row = {cells: [], hidden: false};
  let hidden = false, revealed = 0, active;
  const cells = ['amount', 'memo'].map((kind, cellIndex) => {
    const cell = {parentElement: row, cellIndex, classList: {add() {}, remove() {}},
      focus() {active = cell;}, scrollIntoView() {}, closest(selector) {return selector === 'td' ? cell : null;},
      contains(target) {return target === control || target === mask;}};
    const control = {value: kind === 'amount' ? '-12.34' : 'Fictional memo', dataset: {}, changes: 0, inputs: 0,
      matches(selector) {return selector.split(',').some(value => value.trim().startsWith('input:not'));},
      closest(selector) {
        if (selector === 'td') return cell;
        if (selector === 'html[data-amounts="hidden"]') return hidden ? {} : null;
        if (selector === '[data-private-amount], [data-private-content]') return kind === 'amount' ? control : null;
        return null;
      },
      focus() {active = control;}, select() {},
      dispatchEvent(event) {if (event.type === 'change') this.changes++; if (event.type === 'input') this.inputs++;}};
    const mask = {matches: selector => selector.split(',').includes('button'),
      click() {hidden = false; revealed++; active = control;}};
    cell.querySelector = selector => selector === 'button[data-private-mask]:not([hidden])' ? hidden && kind === 'amount' ? mask : null : control;
    cell.control = control; row.cells.push(cell); return cell;
  });
  const table = {tBodies: [{rows: [row]}], addEventListener(name, callback) {listeners[name] = callback;}};
  const grid = context.initTransactionGrid(table), amount = cells[0].control, memo = cells[1].control;
  const key = (cell, name) => listeners.keydown({target: cell, key: name, preventDefault() {}, stopPropagation() {}});
  grid.focus(amount); amount.value = '-77.01'; hidden = true;
  listeners.focusout({target: amount, relatedTarget: null});
  assert.equal(amount.changes, 0, 'Hiding a dirty amount must not save it when grid editing finishes');
  key(cells[0], '9');
  assert.equal(amount.value, '-77.01'); assert.equal(amount.inputs, 0);
  key(cells[0], 'F2');
  assert.equal(revealed, 1); assert.equal(active, amount); assert.equal(amount.value, '-77.01');
  hidden = true;
  listeners.pointerdown({target: cells[0], preventDefault() {}});
  assert.equal(revealed, 2, 'Clicking the amount cell reveals the real editor instead of editing invisibly');
  hidden = true;
  grid.focus(memo); memo.value = 'Updated fictional memo';
  listeners.focusout({target: memo, relatedTarget: null});
  assert.equal(memo.changes, 1, 'Nonmonetary editing still works while amounts are hidden');
  assert.equal(amount.changes, 0); grid.dispose();
});

test('rounded amount activation joins grid editing so Enter, arrows, Tab and typing retain exact editor behavior', () => {
  const listeners = {}, rows = []; let active;
  for (let index = 0; index < 2; index++) {
    const row = {cells: [], hidden: false};
    for (let column = 0; column < 2; column++) {
      const cell = {parentElement: row, cellIndex: column, classList: {add() {},remove() {}},
        focus() {active=cell;},scrollIntoView(){},closest(){return cell;},contains(target){return target===control;}};
      const control = {value:column===0?'68000.49':'Memo',dataset:column===0?{moneyRounded:''}:{},changes:0,inputs:0,opened:0,
        matches(selector){return selector.includes('input:not([type="date"])');},
        closest(selector){return selector==='td'?cell:null;},focus(){active=control;},
        select(){this.selectionStart=0;this.selectionEnd=this.value.length;},
        dispatchEvent(event){if(event.type==='change')this.changes++;if(event.type==='input')this.inputs++;if(event.type==='money-edit')this.opened++;}};
      cell.querySelector=()=>control;cell.control=control;row.cells.push(cell);
    }
    rows.push(row);
  }
  const table={tBodies:[{rows}],addEventListener(name,callback){listeners[name]=callback;}};
  context.initTransactionGrid(table);
  const first=rows[0].cells[0].control,second=rows[1].cells[0].control;
  const activate=control=>{let prevented=false;listeners['money-display-activate']({target:control,preventDefault(){prevented=true;}});assert.equal(prevented,true);};
  const key=(target,key)=>listeners.keydown({target,key,preventDefault(){},stopPropagation(){}});
  activate(first);assert.equal(active,first);assert.equal(first.value,'68000.49');assert.equal(first.opened,1);
  key(first,'Enter');assert.equal(active,rows[1].cells[0]);assert.equal(first.changes,1);assert.equal(first.value,'68000.49');
  activate(second);key(second,'ArrowUp');assert.equal(active,rows[0].cells[0]);assert.equal(second.changes,1);
  activate(first);key(first,'Tab');assert.equal(active,rows[0].cells[1]);assert.equal(first.value,'68000.49');
  key(rows[0].cells[1],'ArrowLeft');key(rows[0].cells[0],'7');assert.equal(active,first);assert.equal(first.value,'7');assert.equal(first.inputs,1);
});
