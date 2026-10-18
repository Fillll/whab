/* Run with node --test plugins/interface/web/tests/budget_controls.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/budget-edits.js'), 'utf8');
const context = {};
vm.runInNewContext(source.slice(0, source.indexOf('function nodeKey'))
  .replace(/^import .*$/m, '').replaceAll('export function ', 'function '), context);
const {createSaveQueue} = context;

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

async function tick() {
  await new Promise(resolve => setImmediate(resolve));
}

function harness() {
  const saves = [];
  const refreshes = [];
  const events = [];
  const commits = [];
  let active = 0;
  let maximumActive = 0;
  let idles = 0;
  const queue = createSaveQueue({
    save(key, payload) {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      const pending = deferred();
      saves.push({key, payload, resolve: () => {active -= 1; pending.resolve();},
        reject: message => {active -= 1; pending.reject(new Error(message));}});
      return pending.promise;
    },
    refresh() {
      const pending = deferred();
      refreshes.push(pending);
      return pending.promise;
    },
    onCommit: (key, payload) => commits.push({key, payload}),
    onState: (key, state) => events.push({key, ...state}),
    onIdle: () => {idles += 1;},
  });
  return {queue, saves, refreshes, events, commits,
    maximumActive: () => maximumActive, idles: () => idles};
}

const amount = (assigned, month = '2026-10', currency = 'USD') => ({
  workspace: 'demo', month, currency, assigned,
});

test('unchanged blur does not write, refresh, or show a saving state', async () => {
  const state = harness();
  state.queue.remember('rent', amount(1000));
  await state.queue.enqueue('rent', amount(1000));
  assert.equal(state.saves.length, 0);
  assert.equal(state.refreshes.length, 0);
  assert.equal(state.events.length, 0);
  assert.equal(state.queue.hasPending(), false);
});

test('rapid edits serialize core writes, coalesce queued drafts, and wait for authoritative totals', async () => {
  const state = harness();
  state.queue.remember('rent', amount(1000));
  state.queue.remember('utilities', amount(2000));
  const first = state.queue.enqueue('rent', amount(1234));
  state.queue.enqueue('utilities', amount(2100, '2026-11', 'JPY'));
  state.queue.enqueue('utilities', amount(2200, '2026-11', 'JPY'));
  state.queue.enqueue('rent', amount(1400));
  assert.equal(state.saves.length, 1);
  assert.equal(state.commits.length, 0);
  assert.equal(state.refreshes.length, 0);
  assert.equal(state.queue.hasPending(), true);
  assert.equal(state.events.at(-1).status, 'queued');

  state.saves[0].resolve();
  await tick();
  assert.equal(state.commits[0].payload.assigned, 1234);
  assert.equal(state.refreshes.length, 1);
  assert.equal(state.saves.length, 1, 'another write must wait until core totals have refreshed');
  assert.equal(state.events.some(event => event.status === 'saved'), false);
  state.refreshes[0].resolve();
  await tick();
  assert.equal(state.saves[1].key, 'utilities');
  assert.deepEqual(state.saves[1].payload, amount(2200, '2026-11', 'JPY'));
  assert.equal(state.events.some(event => event.key === 'rent' && event.status === 'saved'), false);

  state.saves[1].resolve();
  await tick();
  state.refreshes[1].resolve();
  await tick();
  assert.equal(state.saves[2].key, 'rent');
  assert.equal(state.saves[2].payload.assigned, 1400);
  assert.equal(state.queue.hasPending(), true);
  state.saves[2].resolve();
  await tick();
  state.refreshes[2].resolve();
  await first;
  assert.equal(state.saves.length, 3);
  assert.equal(state.maximumActive(), 1);
  assert.equal(state.queue.hasPending(), false);
  assert.equal(state.queue.hasErrors(), false);
  assert.equal(state.idles(), 1);
  assert.deepEqual(state.events.filter(event => event.status === 'saved').map(event => event.key),
    ['utilities', 'rent']);
});

test('a rejected value does not commit or refresh and remains an error while other fields save', async () => {
  const state = harness();
  const first = state.queue.enqueue('rent', amount(100000000));
  state.queue.enqueue('utilities', amount(2500));
  state.saves[0].reject('Assignment exceeds available money.');
  await tick();
  assert.equal(state.commits.length, 0);
  assert.equal(state.refreshes.length, 0);
  assert.equal(state.saves.length, 2);
  const rejected = state.events.find(event => event.key === 'rent' && event.status === 'error');
  assert.equal(rejected.error.message, 'Assignment exceeds available money.');
  assert.equal(rejected.committed, false);
  assert.equal(state.queue.hasErrors(), true);
  state.saves[1].resolve();
  await tick();
  state.refreshes[0].resolve();
  await first;
  assert.equal(state.commits.length, 1);
  assert.equal(state.commits[0].key, 'utilities');
  assert.equal(state.queue.hasErrors(), true, 'another field refresh must not clear a rejected draft');

  const corrected = state.queue.enqueue('rent', amount(1500));
  state.saves[2].resolve();
  await tick();
  state.refreshes[1].resolve();
  await corrected;
  assert.equal(state.queue.hasErrors(), false);
});

test('a newer draft survives rejection of its earlier in-flight value', async () => {
  const state = harness();
  const first = state.queue.enqueue('rent', amount(100000000));
  state.queue.enqueue('rent', amount(1500));
  state.saves[0].reject('Assignment exceeds available money.');
  await tick();
  assert.equal(state.saves[1].payload.assigned, 1500);
  assert.equal(state.events.some(event => event.status === 'error'), false);
  state.saves[1].resolve();
  await tick();
  state.refreshes[0].resolve();
  await first;
  assert.equal(state.queue.hasErrors(), false);
  assert.equal(state.events.at(-1).status, 'saved');
});

test('a successful write with failed refresh retries only the read and never repeats the mutation', async () => {
  const state = harness();
  const first = state.queue.enqueue('rent', amount(1234));
  state.saves[0].resolve();
  await tick();
  state.refreshes[0].reject(new Error('Connection interrupted.'));
  await first;
  assert.equal(state.commits.length, 1);
  const failed = state.events.at(-1);
  assert.equal(failed.status, 'error');
  assert.equal(failed.committed, true);
  assert.match(failed.error.message, /Saved, but totals could not be refreshed/);
  assert.equal(state.queue.hasErrors(), true);

  const retry = state.queue.enqueue('rent', amount(1234));
  assert.equal(state.saves.length, 1);
  assert.equal(state.refreshes.length, 2);
  assert.equal(state.queue.hasPending(), true);
  state.refreshes[1].resolve();
  await retry;
  assert.equal(state.saves.length, 1);
  assert.equal(state.commits.length, 1);
  assert.equal(state.queue.hasErrors(), false);
  assert.equal(state.events.at(-1).status, 'saved');
});

test('discard removes an unsent draft without cancelling a different in-flight save', async () => {
  const state = harness();
  const first = state.queue.enqueue('rent', amount(1234));
  state.queue.enqueue('utilities', amount(2500));
  state.queue.discard('utilities');
  state.saves[0].resolve();
  await tick();
  state.refreshes[0].resolve();
  await first;
  assert.equal(state.saves.length, 1);
  assert.equal(state.queue.hasErrors(), false);
});

// A small tree implements only the DOM operations used by patchBudget; real-browser
// checks cover focus, selection and scrolling on actual rendered budget forms.
class Node {
  constructor(name, attributes = {}, children = []) {
    this.nodeName = name.toUpperCase();
    this.nodeType = name === '#text' ? 3 : 1;
    this.nodeValue = name === '#text' ? attributes : null;
    this.properties = new Map(this.nodeType === 1 ? Object.entries(attributes) : []);
    this.childNodes = children;
    this.handlers = new Map();
    this.value = this.getAttribute('value') || '';
    this.type = this.getAttribute('type') || '';
    this.checked = this.hasAttribute('checked');
    for (const child of children) child.parentNode = this;
  }
  get attributes() { return [...this.properties].map(([name, value]) => ({name, value})); }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() {
    const siblings = this.parentNode?.childNodes || [];
    return siblings[siblings.indexOf(this) + 1] || null;
  }
  get id() { return this.getAttribute('id'); }
  get isConnected() { return true; }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(value) { if (value) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(child => child.textContent).join(''); }
  set textContent(value) {
    if (this.nodeType === 3) this.nodeValue = value;
    else {this.childNodes = value ? [text(value)] : []; for (const child of this.childNodes) child.parentNode = this;}
  }
  get dataset() {
    const attribute = key => 'data-' + key.replace(/[A-Z]/g, value => '-' + value.toLowerCase());
    return new Proxy({}, {
      get: (_, key) => this.getAttribute(attribute(key)) ?? undefined,
      set: (_, key, value) => {this.setAttribute(attribute(key), value); return true;},
      deleteProperty: (_, key) => {this.removeAttribute(attribute(key)); return true;},
    });
  }
  addEventListener(event, handler) { this.handlers.set(event, handler); }
  fire(event, values = {}) { return this.handlers.get(event)?.({preventDefault() {}, ...values}); }
  select() {}
  blur() { this.fire('blur'); }
  contains(node) { return this === node || this.childNodes.some(child => child.contains(node)); }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    return this.childNodes.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  getAttribute(name) { return this.properties.get(name) ?? null; }
  hasAttribute(name) { return this.properties.has(name); }
  setAttribute(name, value) { this.properties.set(name, String(value)); }
  removeAttribute(name) { this.properties.delete(name); }
  matches(selectors) {
    if (this.nodeType !== 1) return false;
    return selectors.split(',').some(selector => {
      selector = selector.trim();
      if (selector.startsWith('.')) return (this.getAttribute('class') || '').split(' ').includes(selector.slice(1));
      const match = selector.match(/^(\w+)?\[([^\]]+)\]$/);
      return match ? (!match[1] || match[1].toUpperCase() === this.nodeName) && this.hasAttribute(match[2]) : selector.toUpperCase() === this.nodeName;
    });
  }
  cloneNode(deep) {
    return new Node(this.nodeName.toLowerCase(), this.nodeType === 3 ? this.nodeValue :
      Object.fromEntries(this.properties), deep ? this.childNodes.map(child => child.cloneNode(true)) : []);
  }
  remove() {
    if (this.parentNode) this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1);
    this.parentNode = null;
  }
  insertBefore(child, cursor) {
    if (child === cursor) return;
    child.remove();
    const index = cursor ? this.childNodes.indexOf(cursor) : this.childNodes.length;
    this.childNodes.splice(index, 0, child);
    child.parentNode = this;
  }
  replaceWith(child) {
    this.parentNode.insertBefore(child, this);
    this.remove();
  }
}
const text = value => new Node('#text', value);
const element = (tag, key, children = [], attributes = {}) =>
  new Node(tag, {'data-budget-key': key, ...attributes}, children);
const patchContext = {};
vm.runInNewContext(source.slice(source.indexOf('function nodeKey'), source.indexOf('export function initBudgetEdits'))
  .replaceAll('export function ', 'function '), patchContext);
const {patchBudget, syncBudgetGroups} = patchContext;

test('changed totals preserve editor nodes, local error state, expanded explanations and collapsed rows', () => {
  const input = element('input', 'amount', [], {value: '12.34', 'aria-invalid': 'true'});
  input.value = 'newer draft';
  const error = element('p', 'error', [text('Cannot save this draft.')], {'data-budget-local': ''});
  const form = element('form', 'assignment', [input, error], {'data-save-state': 'error'});
  const total = text('100.00');
  const details = element('details', 'explanation', [element('summary', 'label', [text('Where it comes from')])], {open: ''});
  const group = element('button', 'group', [], {'aria-expanded': 'false'});
  const row = element('tr', 'row', [form], {'data-budget-group-member': '1', hidden: ''});
  const current = element('div', 'planner', [element('strong', 'total', [total]), details, group, row]);
  const fresh = element('div', 'planner', [
    element('strong', 'total', [text('110.00')]),
    element('details', 'explanation', [element('summary', 'label', [text('Where it comes from')])]),
    element('button', 'group', [], {'aria-expanded': 'true'}),
    element('tr', 'row', [element('form', 'assignment', [
      element('input', 'amount', [], {value: '15.00'}),
      element('p', 'error', [], {'data-budget-local': '', hidden: ''}),
    ])], {'data-budget-group-member': '1'}),
  ]);
  patchBudget(current, fresh, node => node === input);
  assert.equal(current.childNodes[1], details);
  assert.equal(current.childNodes[2], group);
  assert.equal(current.childNodes[3], row);
  assert.equal(row.childNodes[0], form);
  assert.equal(form.childNodes[0], input);
  assert.equal(form.childNodes[1], error);
  assert.equal(total.nodeValue, '110.00');
  assert.equal(input.value, 'newer draft');
  assert.equal(input.getAttribute('aria-invalid'), 'true');
  assert.equal(error.firstChild.nodeValue, 'Cannot save this draft.');
  assert.equal(form.getAttribute('data-save-state'), 'error');
  assert.equal(details.hasAttribute('open'), true);
  assert.equal(group.getAttribute('aria-expanded'), 'false');
  assert.equal(row.hasAttribute('hidden'), true);
});

test('adding or removing a carryover column retains the same keyed assignment input', () => {
  const input = element('input', 'amount', [], {value: '12.34'});
  const assignment = element('td', 'assigned', [element('form', 'assignment', [input])]);
  const available = element('td', 'available', [text('25.00')]);
  const row = element('tr', 'row', [assignment, available]);
  patchBudget(row, element('tr', 'row', [
    element('td', 'carryover', [text('10.00')]),
    element('td', 'assigned', [element('form', 'assignment', [element('input', 'amount', [], {value: '12.34'})])]),
    element('td', 'available', [text('35.00')]),
  ]));
  assert.equal(row.childNodes[1], assignment);
  assert.equal(assignment.firstChild.firstChild, input);
  assert.equal(row.childNodes[2], available);
  assert.equal(available.firstChild.nodeValue, '35.00');
  patchBudget(row, element('tr', 'row', [
    element('td', 'assigned', [element('form', 'assignment', [element('input', 'amount', [], {value: '15.00'})])]),
    element('td', 'available', [text('30.00')]),
  ]));
  assert.equal(row.childNodes.length, 2);
  assert.equal(row.firstChild, assignment);
  assert.equal(assignment.firstChild.firstChild, input);
  assert.equal(input.value, '15.00');
});

function budgetForm(key, assigned, inline = true) {
  return element('form', key, [
    element('input', 'amount', [], {value: String(assigned), 'data-cents': '', 'data-minor-units': '0'}),
    element('p', 'error', [], {class: 'field-error', 'data-budget-local': '', hidden: ''}),
    element('span', 'status', [], {'data-budget-save-status': '', 'data-budget-local': '', hidden: ''}),
  ], {'data-api': '/budget/' + key, 'data-method': 'PUT', 'data-month': '2026-11',
    [inline ? 'data-inline-assignment' : 'data-budget-mutation']: ''});
}

function editorHarness({initialInline = 1000, validate = false} = {}) {
  const tree = (inline = 1000, inspector = 1000) => element('div', 'budget-planner', [
    budgetForm('rent', inline), budgetForm('inspector', inspector, false),
  ], {class: 'budget-planner', 'data-month': '2026-10', 'data-currency': 'JPY',
    'data-workspace': 'demo', 'data-budget-id': 'demo', 'data-budget-route': 'demo'});
  const planner = tree(initialInline);
  const calls = [], refreshes = [], navigations = [];
  const document = {activeElement: null, querySelector: () => planner, addEventListener() {}};
  const state = {planner, document, calls, refreshes, navigations, fresh: tree(initialInline), tree};
  const runtime = {
    document,
    window: {scrollY: 0, addEventListener() {}},
    location: {href: 'https://localhost:5174/budgets/demo/budget/2026-10/jpy', assign: href => navigations.push(href)},
    api(apiPath, payload, method) {
      const pending = deferred();
      calls.push({apiPath, payload, method, ...pending});
      return pending.promise;
    },
    fetch(url, options) {
      const pending = deferred();
      refreshes.push({url, options, ...pending});
      return pending.promise;
    },
    DOMParser: class {
      parseFromString() { return {querySelector: () => state.fresh}; }
    },
  };
  vm.runInNewContext(source.replace(/^import .*$/m, '').replaceAll('export function ', 'function '), runtime);
  state.controller = runtime.initBudgetEdits({readForm: (form, {authoritative = false} = {}) => {
    const assigned = Number(form.querySelector('[data-cents]').value);
    if (validate && (!Number.isSafeInteger(assigned) || !authoritative && assigned < 0)) {
      throw new Error('Enter a whole-number amount.');
    }
    return {workspace: 'demo', month: form.dataset.month, currency: 'JPY', assigned};
  }});
  return state;
}

async function refreshEditor(state, index = state.refreshes.length - 1) {
  state.refreshes[index].resolve({ok: true, text: async () => ''});
  await tick();
}

test('an inactive unsaved inspector draft survives another field refresh without becoming its saved baseline', async () => {
  const state = editorHarness();
  const [inline, inspector] = state.planner.childNodes;
  const draft = inspector.querySelector('input');
  draft.value = '1500';
  draft.fire('input');
  inline.querySelector('input').value = '1200';
  inline.querySelector('input').fire('blur');
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].payload.assigned, 1200);
  state.calls[0].resolve();
  await tick();
  state.fresh = state.tree(1200, 1000);
  await refreshEditor(state);
  assert.equal(inspector.querySelector('input'), draft);
  assert.equal(draft.value, '1500');
  assert.equal(inspector.dataset.saveState, 'unsaved');
  inspector.fire('submit');
  assert.equal(state.calls.length, 2, 'the untouched draft must still require a core write');
  assert.equal(state.calls[1].payload.assigned, 1500);
  state.calls[1].resolve();
  await tick();
  state.fresh = state.tree(1200, 1500);
  await refreshEditor(state);
  assert.equal(await state.controller.flush(), true);
  assert.deepEqual(state.navigations, []);
});

test('typing a newer value cancels navigation waiting for a previous save and keeps that draft', async () => {
  const state = editorHarness();
  const form = state.planner.firstChild;
  const input = form.querySelector('input');
  input.value = '1200';
  input.fire('blur');
  let cancelled = 0;
  state.controller.navigate('/next-month', () => {cancelled += 1;});
  input.value = '1300';
  input.fire('input');
  state.document.activeElement = input;
  state.calls[0].resolve();
  await tick();
  state.fresh = state.tree(1200);
  await refreshEditor(state);
  assert.deepEqual(state.navigations, []);
  assert.equal(cancelled, 1);
  assert.equal(form.querySelector('input'), input);
  assert.equal(input.value, '1300');
  assert.equal(form.dataset.saveState, 'unsaved');
  input.fire('blur');
  assert.equal(state.calls[1].payload.assigned, 1300);
  state.calls[1].resolve();
  await tick();
  state.fresh = state.tree(1300);
  await refreshEditor(state);
  state.controller.navigate('/next-month');
  await tick();
  assert.deepEqual(state.navigations, ['/next-month']);
});

test('navigation waits for all writes and refreshes and stays on the page after a rejection', async () => {
  const state = editorHarness();
  const input = state.planner.firstChild.querySelector('input');
  input.value = '1200';
  input.fire('blur');
  let cancelled = 0;
  state.controller.navigate('/next-month', () => {cancelled += 1;});
  assert.deepEqual(state.navigations, []);
  state.calls[0].reject(new Error('Assignment exceeds available money.'));
  await tick();
  assert.deepEqual(state.navigations, []);
  assert.equal(cancelled, 1);
  assert.equal(state.planner.firstChild.querySelector('.field-error').textContent,
    'Assignment exceeds available money.');
  assert.equal(input.value, '1200');
  input.value = '1100';
  input.fire('input');
  state.planner.firstChild.fire('submit');
  state.controller.navigate('/next-month');
  state.calls[1].resolve();
  await tick();
  assert.deepEqual(state.navigations, []);
  assert.equal(state.refreshes[0].url, 'https://localhost:5174/budgets/demo/budget/2026-10/jpy');
  assert.equal(state.refreshes[0].options.headers['X-WHAB-Budget-Fragment'], '1');
  state.fresh = state.tree(1100);
  await refreshEditor(state);
  assert.deepEqual(state.navigations, ['/next-month']);
});

test('coalescing preserves last-submit order when two editors write the same logical value', async () => {
  const state = harness();
  const first = state.queue.enqueue('inline-rent', amount(1000));
  state.queue.enqueue('inspector-rent', amount(1100));
  state.queue.enqueue('inline-rent', amount(1200));
  state.queue.enqueue('inspector-rent', amount(1300));
  state.saves[0].resolve();
  await tick();
  state.refreshes[0].resolve();
  await tick();
  assert.equal(state.saves[1].key, 'inline-rent');
  assert.equal(state.saves[1].payload.assigned, 1200);
  state.saves[1].resolve();
  await tick();
  state.refreshes[1].resolve();
  await tick();
  assert.equal(state.saves[2].key, 'inspector-rent');
  assert.equal(state.saves[2].payload.assigned, 1300);
  state.saves[2].resolve();
  await tick();
  state.refreshes[2].resolve();
  await first;
  assert.equal(state.queue.hasErrors(), false);
});


test('a category arriving from another tab inherits its retained group collapse state', () => {
  const group = element('button', 'group:home', [], {'data-budget-group': '1', 'aria-expanded': 'false'});
  const existing = element('tr', 'rent', [], {'data-budget-group-member': '1', hidden: ''});
  const current = element('div', 'planner', [group, existing]);
  const fresh = element('div', 'planner', [
    element('button', 'group:home', [], {'data-budget-group': '1', 'aria-expanded': 'true'}),
    element('tr', 'rent', [], {'data-budget-group-member': '1'}),
    element('tr', 'utilities', [], {'data-budget-group-member': '1'}),
    element('button', 'group:travel', [], {'data-budget-group': '2', 'aria-expanded': 'true'}),
    element('tr', 'flights', [], {'data-budget-group-member': '2'}),
  ]);
  patchBudget(current, fresh);
  syncBudgetGroups(current);
  const added = current.childNodes[2];
  const newGroup = current.childNodes[3];
  const newGroupRow = current.childNodes[4];
  assert.equal(current.firstChild, group);
  assert.equal(existing.hidden, true);
  assert.equal(added.hidden, true);
  assert.equal(newGroupRow.hidden, false);
  group.setAttribute('aria-expanded', 'true');
  syncBudgetGroups(current);
  assert.equal(existing.hidden, false);
  assert.equal(added.hidden, false);
  newGroup.setAttribute('aria-expanded', 'false');
  syncBudgetGroups(current);
  assert.equal(newGroupRow.hidden, true);
  assert.equal(existing.hidden, false);
});


test('an imported negative assignment stays readable without blocking other edits or navigation', async () => {
  const state = editorHarness({initialInline: -100, validate: true});
  const imported = state.planner.firstChild;
  const input = imported.querySelector('input');
  input.fire('blur');
  assert.equal(state.calls.length, 0);
  assert.equal(imported.querySelector('.field-error').hidden, true);
  assert.equal(await state.controller.flush(), true);
  const other = state.planner.childNodes[1];
  other.querySelector('input').value = '1200';
  other.fire('submit');
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].payload.assigned, 1200);
  state.calls[0].resolve();
  await tick();
  state.fresh = state.tree(-100, 1200);
  await refreshEditor(state);
  assert.equal(input.value, '-100');
  assert.equal(other.dataset.saveState, 'saved');
  input.value = '-200';
  input.fire('input');
  input.fire('blur');
  assert.equal(state.calls.length, 1, 'a newly typed negative must not be sent to core');
  assert.equal(imported.querySelector('.field-error').hidden, false);
  assert.equal(imported.querySelector('.field-error').textContent, 'Enter a whole-number amount.');
  let cancelled = 0;
  state.controller.navigate('/next-month', () => {cancelled += 1;});
  await tick();
  assert.equal(cancelled, 1);
  assert.deepEqual(state.navigations, []);
  input.fire('keydown', {key: 'Escape'});
  assert.equal(input.value, '-100');
  assert.equal(imported.querySelector('.field-error').hidden, true);
  state.controller.navigate('/next-month');
  await tick();
  assert.deepEqual(state.navigations, ['/next-month']);
  assert.equal(state.calls.length, 1);
});

test('amount parsing accepts signed authoritative imports without relaxing new-edit limits', () => {
  const app = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
  const parser = {};
  vm.runInNewContext(app.slice(app.indexOf('function minorAmount'), app.indexOf('function formBody')), parser);
  assert.equal(parser.minorAmount('-1.00', 2, true), -100);
  assert.equal(parser.minorAmount('-1', 0, true), -1);
  assert.equal(parser.minorAmount('-0.001', 3, true), -1);
  assert.throws(() => parser.minorAmount('-1.00', 2), /Enter an amount/);
  assert.equal(parser.minorAmount('10000000000.01', 2, true), 1000000000001);
  assert.throws(() => parser.minorAmount('10000000000.01', 2), /too large/);
  assert.equal(parser.minorAmount('90071992547409.91', 2, true), Number.MAX_SAFE_INTEGER);
  assert.throws(() => parser.minorAmount('90071992547409.92', 2, true), /too large/);
});

test('explicitly reverting to the acknowledged value while a save is in flight queues the revert', async () => {
  const state = editorHarness({initialInline: 300, validate: true});
  const form = state.planner.firstChild;
  const input = form.querySelector('input');
  input.value = '310';
  input.fire('keydown', {key: 'Enter'});
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].payload.assigned, 310);
  input.value = '300';
  input.fire('keydown', {key: 'Enter'});
  assert.equal(state.calls.length, 1, 'the revert waits for the first acknowledgement');
  state.calls[0].resolve();
  await tick();
  state.fresh = state.tree(310);
  await refreshEditor(state);
  assert.equal(state.calls.length, 2);
  assert.equal(state.calls[1].payload.assigned, 300);
  assert.equal(input.value, '300');
  state.calls[1].resolve();
  await tick();
  state.fresh = state.tree(300);
  await refreshEditor(state);
  assert.equal(form.dataset.saveState, 'saved');
  assert.equal(await state.controller.flush(), true);
  assert.equal(state.calls.length, 2);
  assert.equal(input.value, '300');
});
