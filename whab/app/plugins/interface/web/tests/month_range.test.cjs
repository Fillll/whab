const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/month-range.js'), 'utf8');
function harness(present = true) {
  const calls = [], observers = [], scrolls = [];
  function target(properties = {}) {
    const events = new Map();
    return Object.assign({style: {}, addEventListener(name, callback) {events.set(name, callback);},
      emit(name, properties = {}) {const event = {button: 0, pointerId: 1, detail: 1, preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;}, ...properties}; events.get(name)?.(event); return event;},
      setAttribute(name, value) {this[name] = value;},
      classList: {values: new Set(), contains(name) {return this.values.has(name);}, toggle(name, on) {if (on) this.values.add(name); else this.values.delete(name);}, add(name) {this.values.add(name);}, remove(name) {this.values.delete(name);}},
    }, properties);
  }
  const handles = Object.fromEntries(['move', 'start', 'end'].map(mode => [mode, target({dataset: {rangeHandle: mode}, closest() {return this;}})]));
  let capture = false;
  const frame = target({querySelector: () => handles.move, setPointerCapture() {capture = true;}, hasPointerCapture: () => capture, releasePointerCapture() {capture = false;}});
  const links = Array.from({length: 13}, (_, index) => target({href: `https://localhost/budgets/demo/budget/2027-${String(index + 1).padStart(2, '0')}/usd`,
    dataset: {month: `2027-${String(index + 1).padStart(2, '0')}`}, closest: () => null, getAttribute: () => `Month ${index + 1}`, getBoundingClientRect: () => ({left: index * 40 + (index >= 5 ? 20 : 0) - strip.scrollLeft, width: 36, top: 13, height: 29})}));
  links[4].classList.add('is-selected');
  let panCapture = false;
  const strip = target({setPointerCapture() {panCapture = true;}, hasPointerCapture: () => panCapture, releasePointerCapture() {panCapture = false;}, scrollLeft: 0, scrollTop: 0, clientWidth: 800, scrollWidth: 800, dataset: {timelineScope: 'fictional/USD'}, scrollTo(value) {scrolls.push(value); if (!this.deferScroll) this.scrollLeft = value.left;}, closest: () => ({dataset: {monthCount: '3'}}),
    querySelector: () => frame, querySelectorAll: () => links, getBoundingClientRect: () => ({left: 0, top: 0})});
  const context = {URL, AbortController, window: {matchMedia: () => ({matches: false})}, ResizeObserver: class {constructor(callback) {this.callback = callback; observers.push(this);} observe() {} disconnect() {this.disconnected = true;}}, document: {querySelector: () => present ? strip : null}};
  vm.runInNewContext(source.replaceAll('export function ', 'function '), context);
  const control = context.initMonthRange({navigate: (url, onCancel) => calls.push({url, onCancel})});
  return {context, calls, control, frame, handles, observers, links, strip, scrolls};
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

test('timeline stays still with room, recenters only near an edge, and clamps calendar ends', () => {
  const {context: {timelineScrollTarget}} = harness(false);
  assert.equal(timelineScrollTarget(220, 350, 100, 500, 900), 100);
  assert.equal(timelineScrollTarget(590, 740, 100, 500, 900), 415);
  assert.equal(timelineScrollTarget(90, 220, 100, 500, 900), 0);
  assert.equal(timelineScrollTarget(990, 1140, 100, 500, 600), 600);
});
test('range arrows preserve the timeline position until the selection reaches its edge', () => {
  const h = harness();
  h.strip.clientWidth = 400; h.strip.scrollWidth = 800; h.strip.scrollLeft = 80;
  h.frame.emit('keydown', {target: h.handles.move, key: 'ArrowRight'});
  assert.equal(h.strip.scrollLeft, 80);
  h.strip.clientWidth = 240;
  h.frame.emit('keydown', {target: h.handles.move, key: 'ArrowRight'});
  assert.equal(h.scrolls.at(-1).behavior, 'smooth');
  assert.ok(h.strip.scrollLeft > 80);
});

test('captured timeline position survives disposal after the old strip loses layout', () => {
  const h = harness(); h.strip.clientWidth = 400; h.strip.scrollWidth = 800; h.strip.scrollLeft = 80;
  h.control.capture(); h.strip.isConnected = false; h.strip.scrollLeft = 0;
  h.control.dispose();
  h.strip.isConnected = true;
  const fresh = h.context.initMonthRange({navigate() {}});
  assert.equal(h.strip.scrollLeft, 80);
  fresh.dispose();
});

test('a smooth edge scroll finishes centering after a fragment replaces an intermediate animation frame', () => {
  const h = harness(); h.strip.clientWidth = 240; h.strip.scrollWidth = 800;
  h.strip.scrollLeft = 100; h.strip.deferScroll = true;
  h.frame.emit('keydown', {target:h.handles.move, key:'ArrowRight'});
  const destination = h.scrolls.at(-1).left;
  h.strip.scrollLeft = 130; // Enough gap, but the requested animation has not finished.
  h.control.capture(); h.strip.isConnected = false; h.strip.scrollLeft = 0; h.control.dispose();
  h.strip.isConnected = true;
  h.context.initMonthRange({navigate() {}});
  assert.equal(h.scrolls.at(-1).left, destination);
  assert.equal(h.scrolls.at(-1).behavior, 'smooth');
});

test('dragging unselected months pans the timeline, preserves selection and suppresses the resulting click', () => {
  const h = harness(); h.strip.scrollLeft = 120;
  h.strip.emit('pointerdown', {target: h.links[1], clientX: 80});
  h.strip.emit('pointermove', {clientX: 30, pointerId: 2});
  assert.equal(h.strip.scrollLeft, 120, 'another pointer cannot pan');
  h.strip.emit('pointermove', {clientX: 30});
  assert.equal(h.strip.scrollLeft, 170);
  assert.equal(h.strip.classList.contains('is-panning'), true);
  assert.equal(h.strip.hasPointerCapture(), true);
  h.strip.emit('pointerup');
  assert.equal(h.strip.hasPointerCapture(), false);
  assert.equal(h.strip.classList.contains('is-panning'), false);
  assert.equal(h.calls.length, 0);
  assert.equal(h.links[4].classList.contains('is-selected'), true);
  const click = h.strip.emit('click');
  assert.equal(click.prevented, true); assert.equal(click.stopped, true);
  assert.equal(h.strip.emit('click').prevented, undefined);
});

test('small pointer motion retains month clicks and cancellation releases panning', () => {
  const h = harness();
  h.strip.emit('pointerdown', {target: h.links[1], clientX: 80});
  h.strip.emit('pointermove', {clientX: 82});
  h.strip.emit('pointerup');
  assert.equal(h.strip.emit('click').prevented, undefined);
  assert.equal(h.strip.emit('dragstart').prevented, true);
  h.strip.emit('pointerdown', {target: h.links[1], clientX: 80});
  h.strip.emit('pointermove', {clientX: 30});
  h.strip.emit('pointercancel');
  assert.equal(h.strip.classList.contains('is-panning'), false);
  assert.equal(h.strip.hasPointerCapture(), false);
  assert.equal(h.strip.emit('click', {detail: 0}).prevented, undefined, 'keyboard clicks remain available');
  h.strip.emit('pointerdown', {target: h.links[2], clientX: 50});
  h.strip.emit('pointerup');
  assert.equal(h.strip.emit('click').prevented, undefined, 'the next ordinary click works');
});
