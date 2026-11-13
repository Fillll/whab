const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transaction-columns.js'), 'utf8');
const key = 'whab.transaction-column-widths.v1';

function harness({saved, unavailable = false} = {}) {
  const storage = new Map(saved === undefined ? [] : [[key, saved]]);
  const writes = [], headers = [], handles = [];
  let group = null;
  const table = {dataset: {}, style: {},
    classList: {classes: new Set(), add(value) {this.classes.add(value);}, remove(value) {this.classes.delete(value);}},
    tHead: {rows: [{cells: headers}]},
    querySelectorAll: () => handles,
    prepend(value) {group = value;},
  };
  const defaults = [36, 128, 160, 36, 280, 240, 200, 108, 64, 36];
  for (const [index, width] of defaults.entries()) {
    const header = {getBoundingClientRect: () => ({width: group ? parseFloat(group.children[index].style.width) : width})};
    headers.push(header);
    const field = {2: 'account', 4: 'merchant', 5: 'category', 6: 'note'}[index];
    if (!field) continue;
    const listeners = new Map(), captures = new Set();
    const handle = {parentElement: header, dataset: {resizeColumn: field}, attributes: {},
      addEventListener(type, fn) {listeners.set(type, fn);},
      removeEventListener(type, fn) {assert.equal(listeners.get(type), fn); listeners.delete(type);},
      setAttribute(name, value) {this.attributes[name] = value;},
      focus() {this.focused = true;},
      setPointerCapture(id) {captures.add(id);}, hasPointerCapture: id => captures.has(id),
      releasePointerCapture(id) {captures.delete(id); this.emit('lostpointercapture');},
      emit(type, args = {}) {
        const event = {button: 0, pointerId: 1, clientX: 500, prevented: false, stopped: false,
          preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;}, ...args};
        listeners.get(type)?.(event);
        return event;
      }, listeners,
    };
    handles.push(handle);
  }
  const context = {Number, Math, JSON, Object, parseFloat,
    window: {localStorage: {
      getItem(name) {if (unavailable) throw new Error('Disabled'); return storage.get(name) ?? null;},
      setItem(name, value) {if (unavailable) throw new Error('Quota'); storage.set(name, value); writes.push(value);},
      removeItem(name) {if (unavailable) throw new Error('Disabled'); storage.delete(name); writes.push(null);},
    }},
    document: {createElement: () => ({style: {}, children: [], append(value) {this.children.push(value);}, remove() {group = null;}})},
  };
  vm.runInNewContext(source.replace('export function ', 'function '), context);
  const columns = context.initTransactionColumns(table);
  return {table, headers, handles, columns, storage, writes, context,
    width: index => headers[index].getBoundingClientRect().width,
    initializedAgain: () => context.initTransactionColumns(table)};
}

test('drag changes only the chosen column, captures the pointer and saves only on release', () => {
  const h = harness(), payee = h.handles[1];
  const before = h.headers.map((_, index) => h.width(index));
  assert.equal(payee.attributes['aria-valuenow'], '280');
  assert.equal(payee.emit('pointerdown', {button: 2}).prevented, false);
  payee.emit('pointermove', {clientX: 600});
  assert.equal(h.width(4), 280);
  const start = payee.emit('pointerdown');
  assert.equal(start.prevented, true); assert.equal(start.stopped, true);
  assert.equal(payee.focused, true); assert.equal(payee.hasPointerCapture(1), true);
  assert.equal(h.table.classList.classes.has('is-resizing-columns'), true);
  payee.emit('pointermove', {pointerId: 2, clientX: 700});
  assert.equal(h.width(4), 280);
  payee.emit('pointermove', {clientX: 600});
  assert.equal(h.width(4), 380);
  assert.equal(h.table.style.width, `${before.reduce((a, b) => a + b) + 100}px`);
  assert.deepEqual(h.headers.map((_, index) => h.width(index)), before.map((width, index) => index === 4 ? 380 : width));
  assert.equal(payee.attributes['aria-valuetext'], '380 pixels');
  assert.equal(h.writes.length, 0);
  payee.emit('pointerup', {pointerId: 2});
  assert.equal(h.writes.length, 0);
  payee.emit('pointerup');
  assert.equal(h.table.classList.classes.size, 0); assert.equal(payee.hasPointerCapture(1), false);
  assert.deepEqual(JSON.parse(h.storage.get(key)), {merchant: 380});
  const click = payee.emit('click');
  assert.equal(click.prevented, true); assert.equal(click.stopped, true, 'Resizing must not activate sorting');
  h.columns.dispose();
  assert.equal(payee.listeners.size, 0);
});

test('keyboard sizing has bounds, larger steps and an individual reset retaining other choices', () => {
  const h = harness(), [account, payee, category, memo] = h.handles;
  assert.equal(payee.emit('keydown', {key: 'Tab'}).prevented, false);
  memo.emit('keydown', {key: 'ArrowRight', shiftKey: true});
  assert.equal(h.width(6), 250);
  account.emit('keydown', {key: 'ArrowLeft'});
  assert.equal(h.width(2), 150);
  for (let index = 0; index < 25; index++) category.emit('keydown', {key: 'ArrowRight', shiftKey: true});
  assert.equal(h.width(5), 1000);
  for (let index = 0; index < 25; index++) category.emit('keydown', {key: 'ArrowLeft', shiftKey: true});
  assert.equal(h.width(5), 100);
  category.emit('keydown', {key: 'Home'});
  assert.equal(h.width(5), 240); assert.equal(h.width(6), 250);
  assert.deepEqual(JSON.parse(h.storage.get(key)), {note: 250, account: 150});
  memo.emit('dblclick'); account.emit('keydown', {key: 'Home'});
  assert.equal(h.storage.has(key), false); assert.equal(h.table.style.width, '');
  assert.equal(h.table.dataset.columnWidths, undefined);
});

test('Escape, pointer cancellation, lost capture and disposal abandon uncommitted widths', () => {
  for (const action of ['Escape', 'pointercancel', 'lostpointercapture', 'dispose']) {
    const h = harness({saved: JSON.stringify({merchant: 350, note: 300})}), payee = h.handles[1];
    payee.emit('pointerdown'); payee.emit('pointermove', {clientX: 650});
    assert.equal(h.width(4), 500);
    if (action === 'Escape') payee.emit('keydown', {key: 'Escape'});
    else if (action === 'dispose') h.columns.dispose();
    else payee.emit(action);
    assert.equal(h.width(4), 350); assert.equal(h.width(6), 300);
    assert.equal(h.writes.length, 0); assert.equal(payee.hasPointerCapture(1), false);
  }
  const h = harness(), memo = h.handles[3];
  memo.emit('pointerdown'); memo.emit('pointermove', {clientX: 700}); memo.emit('pointercancel');
  assert.equal(h.width(6), 200); assert.equal(h.table.dataset.columnWidths, undefined);
});

test('saved widths survive a fresh view and malformed/unavailable storage stays usable', () => {
  const first = harness();
  first.handles[3].emit('keydown', {key: 'ArrowRight'});
  first.columns.dispose();
  const next = harness({saved: first.storage.get(key)});
  assert.equal(next.width(6), 210);
  next.handles[1].emit('keydown', {key: 'ArrowRight'});
  assert.deepEqual(JSON.parse(next.storage.get(key)), {note: 210, merchant: 290});
  for (const saved of ['{', 'null', '[]', JSON.stringify({note: '200', merchant: null, account: {}, category: true})]) {
    const h = harness({saved});
    assert.equal(h.table.dataset.columnWidths, undefined);
    h.handles[3].emit('keydown', {key: 'ArrowRight'});
    assert.equal(h.width(6), 210);
  }
  const bounded = harness({saved: JSON.stringify({note: 50000, category: -2, ignored: 123})});
  assert.equal(bounded.width(6), 1000); assert.equal(bounded.width(5), 100);
  const disabled = harness({unavailable: true});
  disabled.handles[3].emit('keydown', {key: 'ArrowRight'});
  assert.equal(disabled.width(6), 210);
  disabled.handles[3].emit('keydown', {key: 'Home'});
  assert.equal(disabled.width(6), 200);
  disabled.context.initTransactionColumns(null).dispose();
});
