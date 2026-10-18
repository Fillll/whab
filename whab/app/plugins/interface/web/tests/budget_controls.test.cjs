/* Run with node --test plugins/interface/web/tests/budget_controls.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
const controls = source.slice(source.indexOf('function minorAmount'), source.indexOf('function updateAccountFields'));

function control(name, value, type = 'hidden', attributes = []) {
  const flags = new Set(attributes);
  const handlers = new Map();
  return {
    name, value, type, disabled: false, readOnly: false, checked: false,
    dataset: {minorUnits: '2'}, handlers,
    hasAttribute: name => flags.has(name),
    setAttribute: name => flags.add(name), removeAttribute: name => flags.delete(name),
    addEventListener: (event, handler) => handlers.set(event, handler),
    select() {}, blur() {},
  };
}

function form(month, kind) {
  const inline = kind === 'amount';
  const markers = new Set(inline ? ['data-inline-assignment'] : ['data-budget-mutation', 'data-auto']);
  const attributes = new Map();
  const handlers = new Map();
  const choices = inline ? [] : ['category', 'balance'].map(value => control('mode', value, 'radio'));
  if (choices.length) choices[0].checked = true;
  const amount = inline ? control('assigned', '10.00', 'text', ['data-cents']) : null;
  const save = inline ? control('', '', 'submit') : null;
  const elements = [
    control('workspace', 'demo'), control('month', month), control('currency', 'USD'),
    ...(inline ? [amount, save] : choices),
  ];
  elements.namedItem = name => elements.find(element => element.name === name);
  const error = {hidden: true};
  const result = {
    amount, choices, save, error, attributes, elements,
    dataset: {api: inline ? '/budget/utilities' : '/budget/settings/rollover', method: 'PUT'},
    hasAttribute: name => markers.has(name),
    querySelector: selector => selector === '.field-error' ? error : amount,
    querySelectorAll: () => choices,
    addEventListener: (event, handler) => handlers.set(event, handler),
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: name => attributes.delete(name),
    requestSubmit: () => handlers.get('submit')({preventDefault() {}}),
    change: () => handlers.get('change')(),
  };
  return result;
}

function setup(forms) {
  const documentHandlers = new Map();
  const calls = [];
  const navigations = [];
  let active = 0;
  let maximumActive = 0;
  const context = {
    document: {
      querySelectorAll: () => forms,
      addEventListener: (event, handler) => documentHandlers.set(event, handler),
    },
    api: (apiPath, body, method) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      return new Promise((resolve, reject) => calls.push({
        path: apiPath, body: {...body}, method,
        resolve: () => {active -= 1; resolve();},
        reject: message => {active -= 1; reject(new Error(message));},
      }));
    },
    pageUrl: () => '/current-budget',
    location: {assign: value => navigations.push(value), reload: () => navigations.push('reload')},
    notify: message => {throw new Error(`Unexpected toast: ${message}`);},
  };
  vm.runInNewContext(controls, context);
  return {
    calls, navigations, maximumActive: () => maximumActive,
    navigate: href => {
      let prevented = false;
      documentHandlers.get('click')({
        target: {closest: () => ({href, target: '', hasAttribute: () => false})},
        preventDefault: () => prevented = true,
      });
      return prevented;
    },
  };
}

test('amount blur locks all month controls before a carryover click and defers navigation', async () => {
  const amount = form('2026-10', 'amount');
  const rollover = form('2026-10', 'rollover');
  const nextRollover = form('2026-11', 'rollover');
  const state = setup([amount, rollover, nextRollover]);
  amount.amount.value = '12.34';
  const pending = amount.amount.handlers.get('blur')();
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].body.assigned, 1234);
  assert.equal(amount.amount.readOnly, true);
  assert.equal(amount.save.disabled, true);
  assert.ok([...rollover.choices, ...nextRollover.choices].every(choice => choice.disabled));
  await rollover.requestSubmit();
  await nextRollover.requestSubmit();
  assert.equal(state.calls.length, 1);
  assert.equal(state.maximumActive(), 1);
  assert.equal(state.navigate('/next-budget-month'), true);
  assert.deepEqual(state.navigations, []);
  state.calls[0].resolve();
  await pending;
  assert.deepEqual(state.navigations, ['/next-budget-month']);
});

test('two month carryover changes cannot race and failure restores all saved control states', async () => {
  const amount = form('2026-10', 'amount');
  const historical = form('2026-09', 'amount');
  historical.amount.disabled = true;
  historical.amount.readOnly = true;
  historical.save.disabled = true;
  const rollover = form('2026-10', 'rollover');
  const nextRollover = form('2026-11', 'rollover');
  const state = setup([amount, historical, rollover, nextRollover]);
  rollover.choices[0].checked = false;
  rollover.choices[1].checked = true;
  const pending = rollover.change();
  assert.equal(state.calls[0].body.mode, 'balance');
  assert.equal(state.calls[0].body.month, '2026-10');
  assert.equal(state.calls[0].method, 'PUT');
  assert.equal(amount.amount.readOnly, true);
  assert.ok(nextRollover.choices.every(choice => choice.disabled));
  await nextRollover.requestSubmit();
  amount.amount.value = '22.22';
  await amount.requestSubmit();
  assert.equal(state.calls.length, 1);
  assert.equal(state.navigate('/leave-after-save'), true);
  state.calls[0].reject('Could not save the carryover setting.');
  await pending;
  assert.equal(rollover.error.hidden, false);
  assert.equal(rollover.error.textContent, 'Could not save the carryover setting.');
  assert.equal(rollover.attributes.has('aria-busy'), false);
  assert.deepEqual(rollover.choices.map(choice => choice.checked), [true, false]);
  assert.deepEqual(nextRollover.choices.map(choice => choice.checked), [true, false]);
  assert.ok([...rollover.choices, ...nextRollover.choices].every(choice => !choice.disabled));
  assert.equal(amount.amount.readOnly, false);
  assert.equal(amount.save.disabled, false);
  assert.equal(historical.amount.disabled, true);
  assert.equal(historical.amount.readOnly, true);
  assert.equal(historical.save.disabled, true);
  assert.deepEqual(state.navigations, []);
  nextRollover.choices[0].checked = false;
  nextRollover.choices[1].checked = true;
  const retry = nextRollover.change();
  assert.equal(state.calls.length, 2);
  assert.equal(state.calls[1].body.month, '2026-11');
  assert.equal(state.maximumActive(), 1);
  state.calls[1].resolve();
  await retry;
  assert.deepEqual(state.navigations, ['/current-budget']);
});
