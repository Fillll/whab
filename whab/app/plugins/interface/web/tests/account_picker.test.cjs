/* Run with node --test plugins/interface/web/tests/account_picker.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/account-picker.js'), 'utf8');

function element(properties = {}) {
  const listeners = {}, attributes = {}, classes = new Set();
  return Object.assign({
    dataset: {}, hidden: false, childNodes: [], textContent: '',
    classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name);},
      add(name) {classes.add(name);}, remove(name) {classes.delete(name);},
      contains(name) {return classes.has(name);}},
    setAttribute(name, value) {attributes[name] = String(value);},
    getAttribute(name) {return attributes[name] ?? null;},
    removeAttribute(name) {delete attributes[name];},
    addEventListener(type, callback) {(listeners[type] ||= []).push(callback);},
    removeEventListener(type, callback) {listeners[type] = (listeners[type] || []).filter(value => value !== callback);},
    emit(type, detail = {}) {
      const event = {target: this, prevented: false, key: '',
        preventDefault() {this.prevented = true;}, ...detail};
      for (const callback of [...(listeners[type] || [])]) callback(event);
      return event;
    },
    listenerCount(type) {return (listeners[type] || []).length;},
    replaceChildren(...children) {this.childNodes = children;},
    focus() {this.focused = true;}, scrollIntoView() {},
    contains(node) {return node === this || this.childNodes.includes(node);},
    cloneNode() {return element({textContent: this.textContent});},
    querySelector: () => null, querySelectorAll: () => [],
  }, properties);
}

function harness(selected = ['checking']) {
  const chosen = [];
  const logoBindings = [];
  const native = element({options: ['all', 'checking', 'savings', 'travel', 'cash'].map(value => ({
    value, selected: value === 'all' ? !selected.length : selected.includes(value),
  }))});
  const value = element();
  value.querySelector = () => value.childNodes[1];
  const button = element({id: 'account-filter-toggle'});
  const apply = element(), search = element({value: ''}), selectAll = element(), selectNone = element();
  const count = element(), empty = element({hidden: true});
  const popup = element({hidden: true});
  button.setAttribute('aria-expanded', 'false');
  const options = [
    ['all', 'All accounts'], ['checking', 'Daily checking'],
    ['savings', 'Family savings'], ['travel', 'Travel card'], ['cash', 'Daily cash'],
  ].map(([value, label], index) => {
    const text = element({textContent: label});
    const image = element({textContent: `logo-${value}`});
    const option = element({id: `account-option-${index}`, dataset: {value}, childNodes: [image, text],
      querySelector: () => text});
    option.closest = () => option;
    for (const child of option.childNodes) child.closest = () => option;
    option.setAttribute('aria-selected', String(value === 'all' ? !selected.length : selected.includes(value)));
    return option;
  });
  const list = element({querySelectorAll: () => options,
    contains: node => options.includes(node) || options.some(option => option.childNodes.includes(node))});
  const picker = element({hidden: true, querySelector: selector => ({
    '#account-filter-toggle': button, '[role="listbox"]': list, '[data-account-picker-value]': value,
    '[data-account-picker-popup]': popup, '[data-account-picker-apply]': apply,
    '[data-account-picker-search]': search, '[data-account-picker-select-all]': selectAll,
    '[data-account-picker-select-none]': selectNone, '[data-account-picker-count]': count,
    '[data-account-picker-empty]': empty,
  })[selector], contains: node => [button, list, popup, apply, search, selectAll, selectNone, count, empty].includes(node) || list.contains(node)});
  const root = element({querySelector: selector => ({
    '[data-account-picker]': picker, '#account-filter': native,
  })[selector]});
  const document = element();
  let now = 1_000;
  const context = {document, Date: {now: () => now}, bindLogos(scope) {logoBindings.push(scope);}};
  vm.runInNewContext(source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), context);
  const control = context.initAccountPicker(root, selected => chosen.push([...selected]));
  const active = () => options.findIndex(option => option.id === search.getAttribute('aria-activedescendant'));
  const key = (target, key, detail) => picker.emit('keydown', target.emit('keydown', {key, ...detail}));
  return {native, value, button, options, list, popup, apply, picker, document, context, control, chosen, active, logoBindings,
    search, selectAll, selectNone, count, empty,
    selected: () => native.options.filter(option => option.selected && option.value !== 'all').map(option => option.value),
    advance: milliseconds => {now += milliseconds;},
    key: (name, detail) => key(button, name, detail),
    searchKey: (name, detail) => key(search, name, detail),
    filter: query => { search.value = query; search.emit('input'); },
    leave: relatedTarget => picker.emit('focusout', {relatedTarget}),
    choose: index => list.emit('click', {target: options[index].childNodes[0]})};
}

test('enhancement preserves the selected account and native fallback when unavailable', () => {
  const state = harness();
  assert.equal(state.native.hidden, true);
  assert.equal(state.picker.hidden, false);
  assert.equal(state.popup.hidden, true);
  assert.deepEqual(state.selected(), ['checking']);
  assert.deepEqual(state.chosen, []);
  assert.doesNotThrow(() => state.context.initAccountPicker({querySelector: () => null}, () => {}));
});

test('opening, selection changes and canceled navigation restoration bind every cloned header mark', () => {
  const state = harness();
  state.button.emit('click');
  const original = state.value.childNodes[0];
  assert.equal(state.logoBindings.length, 1);
  assert.equal(state.logoBindings[0], state.value);
  state.choose(2);
  assert.notEqual(state.value.childNodes[0], original);
  assert.equal(state.logoBindings.length, 2);
  const draft = state.value.childNodes[0];
  state.control.restore();
  assert.notEqual(state.value.childNodes[0], draft);
  assert.equal(state.logoBindings.length, 3);
  assert.equal(state.logoBindings.every(scope => scope === state.value), true);
  state.control.dispose();
});

test('pointer selection builds a multi-account draft and Apply commits once', () => {
  const state = harness();
  state.button.emit('click');
  assert.equal(state.popup.hidden, false);
  assert.equal(state.button.getAttribute('aria-expanded'), 'true');
  assert.equal(state.search.focused, true);
  assert.equal(state.active(), 1);
  state.choose(2);
  assert.deepEqual(state.chosen, []);
  assert.deepEqual(state.selected(), ['checking']);
  assert.equal(state.options[2].getAttribute('aria-selected'), 'true');
  assert.equal(state.options[1].getAttribute('aria-selected'), 'true');
  assert.equal(state.popup.hidden, false);
  state.apply.emit('click');
  assert.deepEqual(state.chosen, [['checking', 'savings']]);
  assert.deepEqual(state.selected(), ['checking', 'savings']);
  assert.equal(state.popup.hidden, true);
  assert.equal(state.button.getAttribute('aria-expanded'), 'false');
});

test('arrows wrap and Home/End navigate while selection waits for confirmation', () => {
  const state = harness();
  assert.equal(state.key('ArrowDown').prevented, true);
  assert.equal(state.active(), 1);
  state.key('ArrowUp');
  assert.equal(state.active(), 0);
  state.key('ArrowUp');
  assert.equal(state.active(), 4);
  state.key('ArrowDown');
  assert.equal(state.active(), 0);
  state.key('End');
  assert.equal(state.active(), 4);
  state.key('Home');
  assert.equal(state.active(), 0);
  assert.deepEqual(state.selected(), ['checking']);
  assert.deepEqual(state.chosen, []);
  state.key(' ');
  state.key('Enter');
  assert.deepEqual(state.selected(), []);
  assert.deepEqual(state.chosen, [[]]);
});

test('Space toggles without closing and Enter confirms the draft', () => {
  const state = harness(['savings']);
  state.key('ArrowUp');
  assert.equal(state.active(), 2);
  state.key('Escape');
  assert.equal(state.key(' ').prevented, true);
  state.key('ArrowDown');
  state.key(' ');
  assert.equal(state.popup.hidden, false);
  assert.deepEqual(state.chosen, []);
  state.key('Enter');
  assert.deepEqual(state.chosen, [['savings', 'travel']]);
  assert.equal(state.popup.hidden, true);
});

test('Escape cancels without changing the account and restores button focus', () => {
  const state = harness();
  state.key('Enter');
  state.key('End');
  state.key(' ');
  assert.equal(state.key('Escape').prevented, true);
  assert.equal(state.popup.hidden, true);
  assert.equal(state.button.focused, true);
  assert.deepEqual(state.selected(), ['checking']);
  assert.equal(state.options[4].getAttribute('aria-selected'), 'false');
  assert.equal(state.search.getAttribute('aria-activedescendant'), null);
  assert.deepEqual(state.chosen, []);
});

test('Tab stays inside picker; leaving focus and outside pointer commit without stealing focus', () => {
  const state = harness();
  state.button.emit('click');
  state.choose(2);
  state.button.focused = false;
  assert.equal(state.key('Tab').prevented, false);
  state.leave(state.search);
  assert.equal(state.popup.hidden, false);
  state.leave(state.selectAll);
  state.leave(state.selectNone);
  state.leave(state.apply);
  assert.equal(state.popup.hidden, false);
  state.leave(element());
  assert.equal(state.popup.hidden, true);
  assert.equal(state.button.focused, false);
  state.button.emit('click');
  state.choose(1);
  state.button.focused = false;
  state.document.emit('pointerdown', {target: state.options[2]});
  assert.equal(state.popup.hidden, false);
  state.document.emit('pointerdown', {target: element()});
  assert.equal(state.popup.hidden, true);
  assert.equal(state.button.focused, false);
  assert.deepEqual(state.chosen, [['checking', 'savings'], ['savings']]);
});

test('typeahead searches names, cycles repeated letters and resets its prefix', () => {
  const state = harness();
  state.key('d');
  assert.equal(state.popup.hidden, false);
  assert.equal(state.active(), 4);
  state.key('d');
  assert.equal(state.active(), 1);
  state.advance(701);
  state.key('f');
  assert.equal(state.active(), 2);
  state.key('a');
  assert.equal(state.active(), 2);
  state.key(' ');
  state.key('Enter');
  assert.deepEqual(state.chosen, [['checking', 'savings']]);
});

test('modifier shortcuts and composition do not change the active account', () => {
  const state = harness();
  for (const detail of [{ctrlKey: true}, {metaKey: true}, {altKey: true}, {isComposing: true}]) {
    assert.equal(state.key('ArrowDown', detail).prevented, false);
    assert.equal(state.popup.hidden, true);
  }
  assert.deepEqual(state.chosen, []);
});

test('disposal restores the native filter and releases document and control listeners', () => {
  const state = harness();
  state.button.emit('click');
  state.control.dispose();
  assert.equal(state.native.hidden, false);
  assert.equal(state.picker.hidden, true);
  assert.equal(state.popup.hidden, true);
  assert.equal(state.document.listenerCount('pointerdown'), 0);
  assert.equal(state.button.listenerCount('keydown'), 0);
  assert.equal(state.button.listenerCount('click'), 0);
  assert.equal(state.list.listenerCount('click'), 0);
  assert.equal(state.apply.listenerCount('click'), 0);
  assert.equal(state.search.listenerCount('input'), 0);
  assert.equal(state.search.listenerCount('keydown'), 0);
  assert.equal(state.selectAll.listenerCount('click'), 0);
  assert.equal(state.selectNone.listenerCount('click'), 0);
  assert.equal(state.picker.listenerCount('focusout'), 0);
  assert.equal(state.picker.listenerCount('keydown'), 0);
  state.choose(2);
  assert.deepEqual(state.chosen, []);
});

test('empty drafts disable Apply and cannot accidentally mean All accounts', () => {
  const state = harness();
  state.button.emit('click');
  state.choose(1);
  assert.equal(state.options[0].getAttribute('aria-selected'), 'false');
  assert.equal(state.apply.disabled, true);
  assert.equal(state.value.childNodes[1].textContent, 'No accounts selected');
  assert.match(state.count.textContent, /Select at least one account to apply/);
  state.apply.emit('click');
  assert.equal(state.popup.hidden, false);
  assert.deepEqual(state.chosen, []);
  assert.deepEqual(state.selected(), ['checking']);
  state.leave(element());
  assert.equal(state.popup.hidden, true);
  assert.equal(state.value.childNodes[1].textContent, 'Daily checking');
  state.button.emit('click');
  state.choose(2);
  state.choose(2);
  state.apply.emit('click');
  assert.deepEqual(state.chosen, []);
});

test('a single selected account keeps its logo in the collapsed control', () => {
  const state = harness([]);
  state.button.emit('click');
  state.selectNone.emit('click');
  state.choose(2);
  state.apply.emit('click');
  assert.deepEqual(state.chosen, [['savings']]);
  assert.equal(state.value.childNodes[0].textContent, 'logo-savings');
});

test('legacy All accounts opens with every account selected and normalizes full selection', () => {
  const state = harness([]);
  state.button.emit('click');
  assert.ok(state.options.every(option => option.getAttribute('aria-selected') === 'true'));
  assert.equal(state.count.textContent, '4 of 4 selected');
  state.apply.emit('click');
  assert.deepEqual(state.chosen, []);
  state.button.emit('click');
  state.choose(2);
  state.apply.emit('click');
  assert.deepEqual(state.chosen, [['checking', 'travel', 'cash']]);
  state.button.emit('click');
  state.selectAll.emit('click');
  state.apply.emit('click');
  assert.deepEqual(state.chosen, [['checking', 'travel', 'cash'], []]);
  assert.equal(state.native.options[0].selected, true);
  assert.deepEqual(state.selected(), []);
});

test('case-insensitive search preserves hidden selections and bulk actions affect all accounts', () => {
  const state = harness();
  state.button.emit('click');
  state.filter('  FAMILY  ');
  assert.deepEqual(state.options.map(option => option.hidden), [true, true, false, true, true]);
  assert.equal(state.options[1].getAttribute('aria-selected'), 'true');
  assert.equal(state.empty.hidden, true);
  state.choose(2);
  assert.equal(state.count.textContent, '2 of 4 selected');
  state.filter('Daily');
  assert.equal(state.options[2].getAttribute('aria-selected'), 'true');
  state.selectAll.emit('click');
  assert.equal(state.count.textContent, '4 of 4 selected');
  assert.ok(state.options.every(option => option.getAttribute('aria-selected') === 'true'));
  state.selectNone.emit('click');
  assert.ok(state.options.every(option => option.getAttribute('aria-selected') === 'false'));
  assert.equal(state.apply.disabled, true);
  state.choose(4);
  assert.equal(state.apply.disabled, false);
  state.apply.emit('click');
  assert.deepEqual(state.chosen, [['cash']]);
});

test('search typing and editing stay native while arrows and Enter choose visible matches', () => {
  const state = harness();
  state.button.emit('click');
  state.filter('Daily');
  assert.equal(state.active(), 1);
  for (const key of ['d', ' ', 'Home', 'End', 'Backspace', 'Tab']) {
    assert.equal(state.searchKey(key).prevented, false);
  }
  assert.equal(state.searchKey('ArrowDown').prevented, true);
  assert.equal(state.active(), 4);
  assert.equal(state.searchKey('Enter').prevented, true);
  assert.equal(state.options[4].getAttribute('aria-selected'), 'true');
  assert.equal(state.popup.hidden, false);
  state.searchKey('ArrowDown');
  assert.equal(state.active(), 1);
  state.searchKey('ArrowUp');
  assert.equal(state.active(), 4);
  state.searchKey('ArrowDown', {isComposing: true});
  assert.equal(state.active(), 4);
  assert.equal(state.button.getAttribute('aria-activedescendant'), null);
  state.apply.emit('click');
  assert.deepEqual(state.chosen, [['checking', 'cash']]);
});

test('no matching accounts clears active descendant and keyboard selection is a no-op', () => {
  const state = harness();
  state.button.emit('click');
  state.filter('not a bank');
  assert.equal(state.empty.hidden, false);
  assert.equal(state.active(), -1);
  state.searchKey('ArrowDown');
  state.searchKey('ArrowUp');
  state.searchKey('Enter');
  state.key('x');
  assert.equal(state.active(), -1);
  assert.equal(state.count.textContent, '1 of 4 selected');
  state.choose(2);
  assert.equal(state.count.textContent, '1 of 4 selected');
  state.filter('Travel');
  assert.equal(state.empty.hidden, true);
  assert.equal(state.active(), 3);
});

test('filtered typeahead skips hidden choices and Escape restores the committed selection', () => {
  const state = harness(['savings']);
  state.button.emit('click');
  state.filter('Daily');
  state.key('d');
  assert.equal(state.active(), 4);
  state.key('d');
  assert.equal(state.active(), 1);
  state.selectNone.emit('click');
  assert.equal(state.searchKey('Escape').prevented, true);
  assert.equal(state.popup.hidden, true);
  assert.deepEqual(state.chosen, []);
  assert.equal(state.value.childNodes[1].textContent, 'Family savings');
  state.button.emit('click');
  assert.equal(state.search.value, '');
  assert.ok(state.options.every(option => !option.hidden));
  assert.equal(state.active(), 2);
});

test('empty draft outside click cancels, and bulk button keyboard events are not intercepted', () => {
  const state = harness([]);
  state.button.emit('click');
  state.selectNone.emit('click');
  for (const target of [state.selectAll, state.selectNone, state.apply]) {
    assert.equal(state.picker.emit('keydown', {target, key: 'Enter'}).prevented, false);
    assert.equal(state.picker.emit('keydown', {target, key: ' '}).prevented, false);
  }
  state.document.emit('pointerdown', {target: element()});
  assert.equal(state.popup.hidden, true);
  assert.deepEqual(state.chosen, []);
  assert.equal(state.value.childNodes[1].textContent, 'All accounts');
});
