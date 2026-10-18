/* Run with node --test plugins/interface/web/tests/period_picker.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/period-picker.js'), 'utf8');

function element(properties = {}) {
  const listeners = {}, attributes = {};
  return Object.assign({
    hidden: false, dataset: {}, textContent: '', value: '',
    addEventListener(type, callback) {(listeners[type] ||= []).push(callback);},
    removeEventListener(type, callback) {listeners[type] = (listeners[type] || []).filter(value => value !== callback);},
    emit(type, detail = {}) {
      const event = {target: this, prevented: false, key: '', preventDefault() {this.prevented = true;}, ...detail};
      for (const callback of [...(listeners[type] || [])]) callback(event);
      return event;
    },
    listenerCount(type) {return (listeners[type] || []).length;},
    setAttribute(name, value) {attributes[name] = String(value);},
    getAttribute(name) {return attributes[name] ?? null;},
    removeAttribute(name) {delete attributes[name];},
    contains(node) {return node === this;}, focus() {this.focused = true;},
    checkValidity() {return true;}, reportValidity() {return true;},
    querySelector: () => null, querySelectorAll: () => [],
  }, properties);
}

function harness() {
  const chosen = [];
  const toggle = element(), popup = element({hidden: true}), error = element({hidden: true});
  toggle.setAttribute('aria-expanded', 'false');
  const start = element(), end = element();
  const form = element({elements: {start_date: start, end_date: end},
    querySelector: selector => selector.includes('start_date') ? start : selector.includes('end_date') ? end : null});
  const choices = ['current-month', 'previous-month'].map(period => element({dataset: {periodChoice: period}}));
  const picker = element({querySelector: selector => ({
    '#transaction-period-toggle': toggle, '[data-period-popup]': popup, '[data-period-form]': form,
    '[data-period-error]': error,
  })[selector], querySelectorAll: () => choices,
  contains: node => [toggle, popup, form, start, end, error, ...choices].includes(node)});
  const root = element({querySelector: selector => selector === '[data-period-picker]' ? picker : null});
  const document = element();
  const context = {document, Date};
  vm.runInNewContext(source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), context);
  const control = context.initPeriodPicker(root, selection => chosen.push(JSON.parse(JSON.stringify(selection))));
  return {chosen, toggle, popup, error, start, end, form, choices, picker, document, context, control,
    submit: () => form.emit('submit')};
}

test('period presets choose the relative month and close the popup', () => {
  for (const [index, period] of [[0, 'current-month'], [1, 'previous-month']]) {
    const state = harness();
    state.toggle.emit('click');
    assert.equal(state.popup.hidden, false);
    assert.equal(state.toggle.getAttribute('aria-expanded'), 'true');
    state.choices[index].emit('click');
    assert.deepEqual(state.chosen, [{period, month: 'current-month'}]);
    assert.equal(state.popup.hidden, true);
    assert.equal(state.toggle.getAttribute('aria-expanded'), 'false');
  }
});

test('a custom inclusive date range is committed only on Apply', () => {
  const state = harness();
  state.toggle.emit('click');
  state.start.value = '2026-09-10';
  state.end.value = '2026-10-10';
  assert.deepEqual(state.chosen, []);
  assert.equal(state.submit().prevented, true);
  assert.deepEqual(state.chosen, [{period: 'custom', startDate: '2026-09-10', endDate: '2026-10-10'}]);
  assert.equal(state.popup.hidden, true);
});

test('custom ranges support a single boundary or the whole open-ended history', () => {
  for (const [startDate, endDate] of [['2026-10-10', ''], ['', '2026-10-10'], ['', '']]) {
    const state = harness();
    state.toggle.emit('click');
    state.start.value = startDate;
    state.end.value = endDate;
    state.submit();
    assert.deepEqual(state.chosen, [{period: 'custom', startDate, endDate}]);
  }
});

test('native date validation blocks malformed or unsupported dates before navigation', () => {
  const state = harness();
  state.toggle.emit('click');
  state.form.reportValidity = () => false;
  state.start.value = 'invalid';
  state.submit();
  assert.deepEqual(state.chosen, []);
  assert.equal(state.popup.hidden, false);
});

test('inverted dates keep the picker open without changing the register', () => {
  const state = harness();
  state.toggle.emit('click');
  state.start.value = '2026-10-11';
  state.end.value = '2026-10-10';
  state.submit();
  assert.deepEqual(state.chosen, []);
  assert.equal(state.popup.hidden, false);
  assert.equal(state.error.hidden, false);
  assert.notEqual(state.error.textContent, '');
});

test('Escape and outside pointer dismissal leave an unapplied range unchanged', () => {
  const state = harness();
  state.toggle.emit('click');
  state.start.value = '2026-09-10';
  assert.equal(state.picker.emit('keydown', {key: 'Escape'}).prevented, true);
  assert.equal(state.popup.hidden, true);
  assert.equal(state.toggle.focused, true);
  state.toggle.emit('click');
  state.document.emit('pointerdown', {target: state.form});
  assert.equal(state.popup.hidden, false);
  state.document.emit('pointerdown', {target: element()});
  assert.equal(state.popup.hidden, true);
  assert.deepEqual(state.chosen, []);
});

test('disposal releases the picker and outside listeners and missing markup is harmless', () => {
  const state = harness();
  state.toggle.emit('click');
  state.control.dispose();
  assert.equal(state.popup.hidden, true);
  assert.equal(state.toggle.listenerCount('click'), 0);
  assert.equal(state.form.listenerCount('submit'), 0);
  assert.equal(state.document.listenerCount('pointerdown'), 0);
  for (const choice of state.choices) assert.equal(choice.listenerCount('click'), 0);
  assert.doesNotThrow(() => state.context.initPeriodPicker({querySelector: () => null}, () => {}).dispose());
});
