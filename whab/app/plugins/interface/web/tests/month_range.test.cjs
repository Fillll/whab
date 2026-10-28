const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/month-range.js'), 'utf8');
function harness(present = true) {
  const calls = [], observers = [];
  function target(properties = {}) {
    const events = new Map();
    return Object.assign({style: {}, addEventListener(name, callback) {events.set(name, callback);},
      emit(name, properties = {}) {const event = {button: 0, pointerId: 1, preventDefault() {}, ...properties}; events.get(name)?.(event);},
      setAttribute(name, value) {this[name] = value;},
      classList: {values: new Set(), contains(name) {return this.values.has(name);}, toggle(name, on) {if (on) this.values.add(name); else this.values.delete(name);}, add(name) {this.values.add(name);}, remove(name) {this.values.delete(name);}},
    }, properties);
  }
  const handles = Object.fromEntries(['move', 'start', 'end'].map(mode => [mode, target({dataset: {rangeHandle: mode}, closest() {return this;}})]));
  let capture = false;
  const frame = target({querySelector: () => handles.move, setPointerCapture() {capture = true;}, hasPointerCapture: () => capture, releasePointerCapture() {capture = false;}});
  const links = Array.from({length: 13}, (_, index) => target({href: `https://localhost/budgets/demo/budget/2027-${String(index + 1).padStart(2, '0')}/usd`,
    getAttribute: () => `Month ${index + 1}`, getBoundingClientRect: () => ({left: index * 40 + (index >= 5 ? 20 : 0), width: 36, top: 13, height: 29})}));
  links[4].classList.add('is-selected');
  const strip = target({scrollLeft: 0, scrollTop: 0, clientWidth: 800, closest: () => ({dataset: {monthCount: '3'}}),
    querySelector: () => frame, querySelectorAll: () => links, getBoundingClientRect: () => ({left: 0, top: 0})});
  const context = {URL, AbortController, ResizeObserver: class {constructor(callback) {this.callback = callback; observers.push(this);} observe() {} disconnect() {this.disconnected = true;}}, document: {querySelector: () => present ? strip : null}};
  vm.runInNewContext(source.replaceAll('export function ', 'function '), context);
  const control = context.initMonthRange({navigate: (url, onCancel) => calls.push({url, onCancel})});
  return {context, calls, control, frame, handles, observers, links};
}
test('range endpoints stay within one to five months, including both calendar edges', () => {
  const {context: {rangeStep}} = harness(false);
  for (let start = 0; start < 13; start++) for (let count = 1; count <= Math.min(5, 13 - start); count++) {
    for (const mode of ['move', 'start', 'end']) for (let delta = -20; delta <= 20; delta++) {
      const next = rangeStep(start, count, mode, delta, 13);
      assert.ok(next.start >= 0 && next.count >= 1 && next.count <= 5 && next.start + next.count <= 13);
      if (mode === 'move') assert.equal(next.count, count);
      if (mode === 'start') assert.equal(next.start + next.count, start + count);
      if (mode === 'end') assert.equal(next.start, start);
    }
  }
});
test('pointer drag previews continuously, snaps past year gaps, and commits only on release', () => {
  const h = harness();
  h.frame.emit('pointerdown', {target: h.handles.move, clientX: 100});
  h.frame.emit('pointermove', {clientX: 160});
  assert.equal(h.calls.length, 0);
  assert.equal(h.links[5].classList.contains('is-selected'), true);
  h.frame.emit('pointerup');
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url.pathname, '/budgets/demo/budget/2027-06/usd');
  assert.equal(h.calls[0].url.searchParams.get('months'), '3');
  h.calls[0].onCancel();
  assert.equal(h.links[4].classList.contains('is-selected'), true);
});
test('cancelled pointers restore selection and keyboard resizing preserves the opposite endpoint', () => {
  const h = harness();
  h.frame.emit('pointerdown', {target: h.handles.end, clientX: 200});
  h.frame.emit('pointermove', {clientX: 280});
  h.frame.emit('pointercancel');
  assert.equal(h.calls.length, 0);
  assert.equal(h.links[7].classList.contains('in-range'), false);
  h.frame.emit('keydown', {target: h.handles.end, key: 'ArrowRight'});
  assert.equal(h.calls[0].url.searchParams.get('months'), '4');
  h.frame.emit('keydown', {target: h.handles.start, key: 'ArrowRight'});
  assert.equal(h.calls[1].url.searchParams.get('months'), '3');
  assert.equal(h.calls[1].url.pathname, '/budgets/demo/budget/2027-06/usd');
  h.control.dispose(); assert.equal(h.observers[0].disconnected, true);
  harness(false).control.dispose();
});
