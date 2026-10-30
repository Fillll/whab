/* Exercise app wiring with the real budget editor and form parser across page mounts. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const directory = path.join(__dirname, '../src/whab_plugin_web/static');
const app = fs.readFileSync(path.join(directory, 'app.js'), 'utf8');
const budget = fs.readFileSync(path.join(directory, 'budget-edits.js'), 'utf8');
const sourceLinks = fs.readFileSync(path.join(directory, 'budget-source-links.js'), 'utf8');
const controls = fs.readFileSync(path.join(__dirname, 'budget_controls.test.cjs'), 'utf8');
const dom = {};
vm.runInNewContext(controls.slice(controls.indexOf('class Node {'), controls.indexOf('const patchContext =')) + '\nglobalThis.node = (tag, attributes, children) => new Node(tag, attributes, children);', dom);
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(yes => {resolve = yes;});
  return {promise, resolve};
}
class Event {
  constructor(type, options = {}) {Object.assign(this, {type, defaultPrevented: false}, options);}
  preventDefault() {this.defaultPrevented = true;}
}
class Target {
  constructor(properties = {}) {this.listeners = new Map(); Object.assign(this, properties);}
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
  }
  removeEventListener(type, callback) {this.listeners.get(type)?.delete(callback);}
  dispatchEvent(event) {for (const callback of this.listeners.get(event.type) || []) callback(event); return !event.defaultPrevented;}
}
function input(name, value, attributes = {}) {
  const field = dom.node('input', {name, value, ...attributes}, []);
  field.name = name;
  field.checkValidity = () => true;
  field.reportValidity = () => {field.reported = true;};
  return field;
}
function form(fields, attributes) {
  const result = dom.node('form', attributes, fields);
  Object.defineProperty(result, 'elements', {get() {
    const elements = this.querySelectorAll('input, textarea, select');
    elements.namedItem = name => elements.find(field => field.name === name);
    return elements;
  }});
  return result;
}
function page(kind, assigned = '100') {
  const amount = input('assigned', assigned, {'data-cents': '', 'data-minor-units': '0', 'data-budget-key': 'amount'});
  const assignment = form([input('workspace', 'personal'), input('month', '2026-10'), amount], {
    'data-api': '/budget/food', 'data-inline-assignment': '', 'data-budget-key': 'assignment',
  });
  const planner = dom.node('div', {class: 'budget-planner', 'data-budget-key': 'planner',
    'data-month': '2026-10', 'data-currency': 'USD', 'data-workspace': 'personal',
    'data-budget-id': 'fictional', 'data-budget-route': 'household'}, [assignment]);
  const month = input('month', '2026-10', {'data-navigation': 'month'});
  const monthNumber = input('monthNumber', '10', {'data-navigation': 'month', 'data-month-number': ''});
  const year = input('year', '2026', {'data-navigation': 'month', 'data-month-year': ''});
  const picker = dom.node('div', {'data-month-picker': ''}, [monthNumber, year]);
  const root = {querySelectorAll: selector => selector !== '[data-navigation]' ? [] : kind === 'insights' ? [month] : kind === 'budget' ? [monthNumber, year] : []};
  return {kind, planner: kind === 'budget' ? planner : null, amount, assignment, month, monthNumber, year, picker, root};
}
function harness(initial = 'budget') {
  let current = page(initial), navigationOptions, openDialog = false, logos = 0;
  const requests = [], refreshes = [], registers = [], notices = [], locations = [], readyEvents = [], tickets = [];
  const category = form([input('workspace', 'personal'), input('name', 'Fictional category'),
    input('group', 'Fictional'), input('target', '0', {'data-cents': '', 'data-minor-units': '0'})], {'data-api': '/categories'});
  const document = new Target({activeElement: null,
    querySelector: selector => selector === '.budget-planner' ? current.planner : selector === 'dialog[open]' && openDialog ? {} : null,
    querySelectorAll: selector => selector === 'form[data-api]' ? [category] : current.root.querySelectorAll(selector),
  });
  const window = new Target({scrollY: 0, scrollTo() {}});
  const navigator = {
    navigate(url, options) {
      const pending = deferred();
      const ticket = {url, options, ...pending}; tickets.push(ticket);
      void navigationOptions.flush().then(saved => {ticket.saved = saved;});
      return pending.promise;
    },
    cancelPending() {for (const ticket of tickets) if (!ticket.canceled) {
      ticket.canceled = true; ticket.options.onCancel?.(); ticket.resolve(true);
    }},
    pendingDestination: () => null,
  };
  const runtime = {document, window, CustomEvent: Event, URL, BigInt,
    getContext: () => ({}),
    location: {href: `https://localhost:5173/budgets/household/${initial}/2026-10/usd`,
      assign: value => locations.push(String(value)), reload: () => locations.push('reload')},
    api(url, body) {const pending = deferred(); requests.push({url, body, ...pending}); return pending.promise;},
    fetch(url, options) {const pending = deferred(); refreshes.push({url, options, ...pending}); return pending.promise;},
    DOMParser: class {parseFromString() {return {querySelector: () => page('budget', String(requests.at(-1).body.assigned)).planner};}},
    updateAccountFields() {},
    initAccountList() {return {dirty: false, flush: async () => true, dispose() {}};},
    initMonthRange() {return {dispose() {}};},
    initAccountNavigation(options) {navigationOptions = options; return navigator;},
    initTransactionRegister() {
      const register = {dirty: false, disposed: false, flushes: 0,
        async flush() {this.flushes += 1; return true;}, dispose() {this.disposed = true;}};
      registers.push(register); return register;
    },
    bindLogos() {logos += 1;}, notify: message => notices.push(message),
    pageUrl: overrides => new URL(`https://localhost:5173/budgets/household/${current.kind}/${overrides.month || '2026-10'}/usd`),
  };
  document.addEventListener('whab:page-ready', () => readyEvents.push(current.kind));
  const header = app.slice(app.indexOf('let currentRegister = null;'), app.indexOf('function minorAmount'));
  const parserAndForms = app.slice(app.indexOf('function minorAmount'), app.indexOf('function updateAccountFields'));
  const pageControls = app.slice(app.indexOf('const pageControls = new WeakSet();'), app.indexOf("document.addEventListener('click', event => {", app.indexOf('const pageControls = new WeakSet();')));
  const initialRegister = app.slice(app.indexOf('\ncurrentRegister = initTransactionRegister'), app.indexOf("\ndocument.addEventListener('click', async event", app.indexOf('\ncurrentRegister = initTransactionRegister')));
  vm.runInNewContext(budget.replace(/^import .*$/gm, '').replaceAll('export function ', 'function ') +
    sourceLinks.replace('export function ', 'function ') + header + parserAndForms + pageControls + initialRegister, runtime);
  return {runtime, document, window, navigator, requests, refreshes, registers, notices, locations, readyEvents, tickets, category,
    get current() {return current;}, get navigationOptions() {return navigationOptions;}, get logos() {return logos;},
    dialog(value) {openDialog = value;},
    async replace(kind) {
      if (!(await navigationOptions.flush()) || !navigationOptions.beforeReplace()) return false;
      current = page(kind); runtime.location.href = `https://localhost:5173/budgets/household/${kind}/2026-10/usd`;
      navigationOptions.onReplace({content: current.root}); return true;
    },
    async finishSave(index = requests.length - 1) {
      requests[index].resolve({ok: true}); await tick();
      refreshes.at(-1).resolve({ok: true, text: async () => ''}); await tick();
    },
  };
}

test('Budget to Accounts to Budget repeatedly remounts active editors and releases the previous page', async () => {
  const state = harness();
  for (let cycle = 0; cycle < 2; cycle += 1) {
    const old = state.current;
    assert.equal(await state.replace('accounts'), true);
    assert.equal(old.amount.handlers.size, 0);
    assert.equal(old.assignment.handlers.size, 0);
    assert.equal(state.registers.at(-2).disposed, true);
    assert.equal(await state.replace('budget'), true);
    const fresh = state.current;
    assert.ok(fresh.amount.handlers.has('input'));
    assert.equal(state.document.listeners.get('click').size, 2, 'one source-link delegate and the active editor');
    fresh.amount.value = String(120 + cycle); fresh.amount.fire('input'); fresh.amount.fire('blur');
    assert.equal(state.requests.length, cycle + 1);
    assert.equal(state.requests.at(-1).url, '/budget/food');
    assert.equal(state.requests.at(-1).body.assigned, 120 + cycle);
    await state.finishSave();
    assert.equal(await state.navigationOptions.flush(), true);
    assert.equal(fresh.amount.value, String(120 + cycle));
  }
  assert.deepEqual(state.readyEvents, ['accounts', 'budget', 'accounts', 'budget']);
  assert.equal(state.logos, 4);
  assert.equal(state.locations.length, 0);
});

test('open dialogs and provider vetoes block navigation guards while dialog mutation saves still flush', async () => {
  const state = harness();
  let busy = true, guarded = 0;
  state.document.addEventListener('whab:before-navigate', event => {guarded += 1; if (busy) event.preventDefault();});
  assert.equal(await state.navigationOptions.flush(), false);
  assert.equal(state.navigationOptions.beforeReplace(), false);
  assert.equal(state.current.amount.handlers.size > 0, true);
  busy = false; state.dialog(true);
  assert.equal(await state.navigationOptions.flush(), false);
  assert.equal(state.navigationOptions.beforeReplace(), false);
  const before = guarded;
  const submitting = state.category.fire('submit');
  await tick();
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, '/categories');
  assert.equal(guarded, before, 'mutation save barriers must not call the navigation-only dialog guard');
  state.requests[0].resolve({ok: true}); await submitting; await tick();
  assert.deepEqual(state.locations, ['reload']);
  state.dialog(false);
  assert.equal(await state.navigationOptions.flush(), true);
});

test('fresh Insights month controls activate once per node and retain the selected URL', async () => {
  const state = harness('accounts');
  assert.equal(await state.replace('insights'), true);
  const month = state.current.month;
  state.runtime.initPageControls(state.current.root);
  assert.equal(month.handlers.size, 1);
  month.value = '2026-11'; const changing = month.fire('change');
  assert.equal(state.tickets.length, 1);
  assert.equal(state.tickets[0].url.pathname, '/budgets/household/insights/2026-11/usd');
  state.tickets[0].resolve(true); await changing;
  assert.equal(state.locations.length, 0);
  assert.equal(await state.replace('accounts'), true);
  assert.equal(await state.replace('insights'), true);
  state.current.month.value = '2026-12'; const next = state.current.month.fire('change');
  assert.equal(state.tickets.length, 2);
  state.tickets[1].resolve(true); await next;
  assert.equal(state.tickets[1].url.pathname, '/budgets/household/insights/2026-12/usd');
});

test('selector cancellation during its save barrier restores only the canceled selection', async () => {
  const state = harness('insights');
  const barrier = deferred();
  state.registers.at(-1).flush = () => barrier.promise;
  const month = state.current.month;
  month.value = '2026-11'; const changing = month.fire('change'); await tick();
  assert.equal(state.tickets[0].saved, undefined);
  state.navigator.cancelPending();
  assert.equal(month.value, '2026-10');
  barrier.resolve(true); await changing; await tick();
  assert.equal(state.locations.length, 0);
  month.value = '2026-11'; const first = month.fire('change');
  month.value = '2026-12'; const later = month.fire('change');
  state.tickets[1].options.onCancel();
  assert.equal(month.value, '2026-12', 'a late cancellation cannot overwrite a newer selector draft');
  state.tickets[1].resolve(true); state.tickets[2].resolve(true); await Promise.all([first, later]);
  assert.equal(state.locations.length, 0);
});

test('explicit month and year controls validate and navigate together across page mounts', async () => {
  const state = harness('accounts'); await state.replace('budget');
  const {picker, monthNumber, year} = state.current;
  state.runtime.initPageControls(state.current.root);
  assert.equal(picker.handlers.size, 1, 'one listener for the pair');
  year.value = '2.034e3'; // Native number fields accept numeric notation; addresses use four-digit years.
  const changing = picker.fire('change');
  assert.equal(state.tickets[0].url.pathname, '/budgets/household/budget/2034-10/usd');
  state.tickets[0].options.onCancel();
  assert.equal(year.value, '2026'); assert.equal(monthNumber.value, '10');
  state.tickets[0].resolve(true); await changing;
  year.checkValidity = () => false; year.value = '2201';
  await picker.fire('change');
  assert.equal(year.reported, true); assert.equal(state.tickets.length, 1);
  year.checkValidity = () => true;
  year.value = '1900'; monthNumber.value = '01';
  const first = picker.fire('change');
  year.value = '2200'; monthNumber.value = '12';
  const later = picker.fire('change');
  state.tickets[1].options.onCancel();
  assert.equal(year.value, '2200', 'a late cancellation retains newer year and month choices');
  assert.equal(state.tickets[1].url.pathname, '/budgets/household/budget/1900-01/usd');
  assert.equal(state.tickets[2].url.pathname, '/budgets/household/budget/2200-12/usd');
  state.tickets[1].resolve(true); state.tickets[2].resolve(true); await Promise.all([first, later]);
});
