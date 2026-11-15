const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/sidebar-order.js'), 'utf8').replaceAll('export function ', 'function ');
const plain = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag = 'div', dataset = {}, classes = []) {
    this.tag = tag; this.dataset = dataset; this.children = []; this.attributes = {};
    this.events = new Map(); this.disabled = false; this.hidden = false; this.textContent = '';
    this.classList = {values: new Set(classes), add: name => this.classList.values.add(name), remove: name => this.classList.values.delete(name)};
  }
  append(...items) {for (const item of items) {if (item.parentElement) item.parentElement.children = item.parentElement.children.filter(x => x !== item); item.parentElement = this; this.children.push(item);}}
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(value => this.matches(value.trim()));
    if (selector === 'a.sidebar-account') return this.tag === 'a' && this.classList.values.has('sidebar-account');
    if (selector.startsWith('.')) return this.classList.values.has(selector.slice(1));
    if (!selector.startsWith('[')) return this.tag === selector;
    const match = selector.match(/^\[data-([^=\]]+)(?:="([^"]*)")?\]$/);
    if (!match) throw Error(`Unknown selector ${selector}`);
    const key = match[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    return key in this.dataset && (match[2] === undefined || this.dataset[key] === match[2]);
  }
  closest(selector) {return this.matches(selector) ? this : this.parentElement?.closest(selector) || null;}
  querySelectorAll(selector) {return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);}
  querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
  contains(node) {return node === this || this.children.some(child => child.contains(node));}
  setAttribute(key, value) {this.attributes[key] = value;}
  removeAttribute(key) {delete this.attributes[key];}
  focus() {let parent = this; while (parent.parentElement) parent = parent.parentElement; parent.activeElement = this;}
  getBoundingClientRect() {return {top: 20, height: 40};}
  addEventListener(name, callback) {this.events.set(name, [...(this.events.get(name) || []), callback]);}
  removeEventListener(name, callback) {this.events.set(name, this.events.get(name).filter(value => value !== callback));}
}
function sidebar({budgetId = 'fictional', workspace = 'personal', environment = 'sandbox', defaults = ['a', 'hidden', 'b', 'c', 'off'], on = ['a', 'b', 'c'], off = ['off']} = {}) {
  const shell = new Element('div', {}, ['sidebar-accounts']);
  const panel = new Element('div', {sidebarOrder: '', budgetId, workspace, environment, defaultOrder: JSON.stringify(defaults)});
  shell.append(panel);
  const toggle = new Element('button', {accountReorder: ''}); toggle.hidden = true;
  const tools = new Element('div', {accountOrderTools: ''}); tools.hidden = true;
  const reset = new Element('button', {accountOrderReset: ''}), done = new Element('button', {accountOrderDone: ''});
  const status = new Element('p', {accountOrderStatus: ''}); tools.append(reset, done); panel.append(toggle, tools, status);
  const groups = {}, accounts = {};
  for (const [key, ids] of [['on-budget', on], ['off-budget', off]]) {
    const group = new Element('div', {accountOrderGroup: key}); groups[key] = group; panel.append(group);
    for (const id of ids) {
      const row = new Element('div', {accountOrderRow: id}, ['sidebar-account-row']);
      const grip = new Element('span', {}, ['sidebar-account-drag']);
      const link = new Element('a', {accountId: id}, ['sidebar-account']);
      const name = new Element('span', {}, ['sidebar-account-name']); name.textContent = `${id} 🌿 储蓄`;
      const controls = new Element('span', {}, ['sidebar-account-moves']);
      const up = new Element('button', {accountMove: 'up'}), down = new Element('button', {accountMove: 'down'});
      controls.append(up, down); link.append(name); row.append(grip, link, controls); group.append(row);
      accounts[id] = {row, grip, link, name, controls, up, down};
    }
  }
  return {shell, panel, toggle, tools, reset, done, status, groups, accounts, ids: key => groups[key || 'on-budget'].children.map(row => row.dataset.accountOrderRow)};
}
function harness({saved, blocked = false, ...settings} = {}) {
  const document = new Element('document'), window = new Element('window');
  const fixture = sidebar(settings); document.append(fixture.shell);
  const storage = new Map(), writes = [];
  window.localStorage = {
    getItem(key) {if (blocked) throw Error('blocked'); return storage.get(key) ?? null;},
    setItem(key, value) {if (blocked) throw Error('blocked'); storage.set(key, value); writes.push([key, value]);},
    removeItem(key) {if (blocked) throw Error('blocked'); storage.delete(key); writes.push([key, null]);},
  };
  let observer;
  const context = {document, window, MutationObserver: class {constructor(callback) {observer = this; this.callback = callback; this.observed = [];} observe(...args) {this.observed.push(args);} disconnect() {this.disconnected = true;}}};
  vm.runInNewContext(source, context);
  const key = context.sidebarOrderKey(fixture.panel.dataset);
  if (saved !== undefined) storage.set(key, typeof saved === 'string' ? saved : JSON.stringify(saved));
  const instance = context.initSidebarOrder();
  function emit(type, target, extra = {}) {
    const event = {target, prevented: false, stopped: false, clientY: 25,
      preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;},
      dataTransfer: {setData(key, value) {this[key] = value;}}, ...extra};
    for (const callback of document.events.get(type) || []) callback(event);
    return event;
  }
  return {...fixture, document, window, storage, writes, context, key, instance, observer, emit,
    click: element => emit('click', element), external(key, newValue) {for (const callback of window.events.get('storage') || []) callback({key, newValue});}};
}

test('reorder mode is discoverable; keyboard buttons save group order and retain hidden slots', () => {
  const h = harness();
  assert.equal(h.toggle.hidden, false); assert.equal(h.tools.hidden, true); assert.equal(h.writes.length, 0);
  h.click(h.toggle);
  assert.equal(h.panel.dataset.reordering, 'true'); assert.equal(h.tools.hidden, false);
  assert.equal(h.accounts.a.up.disabled, true); assert.equal(h.accounts.c.down.disabled, true);
  assert.equal(h.accounts.a.link.attributes.tabindex, '-1');
  assert.equal(h.click(h.accounts.a.link).prevented, true);
  h.click(h.accounts.c.up);
  assert.deepEqual(h.ids(), ['a', 'c', 'b']);
  assert.deepEqual(JSON.parse(h.storage.get(h.key)), ['a', 'hidden', 'c', 'b', 'off']);
  assert.match(h.status.textContent, /c 🌿 储蓄 moved.*saved/);
  assert.equal(h.document.activeElement, h.accounts.c.up);
  h.click(h.accounts.c.up);
  assert.deepEqual(h.ids(), ['c', 'a', 'b']); assert.equal(h.document.activeElement, h.accounts.c.down);
  h.click(h.done);
  assert.equal(h.panel.dataset.reordering, 'false'); assert.equal(h.tools.hidden, true);
  assert.equal(h.accounts.a.link.attributes.tabindex, undefined); assert.equal(h.accounts.a.row.draggable, false);
  assert.equal(h.click(h.accounts.a.link).prevented, false); assert.equal(h.document.activeElement, h.toggle);
  const reopened = harness({saved: h.storage.get(h.key)});
  assert.deepEqual(reopened.ids(), ['c', 'a', 'b']);
});

test('saved orders isolate environment, budget and workspace and handle hidden/new/deleted accounts', () => {
  const h = harness({saved: ['c', 'hidden', 'deleted', 'a', 'off', 'b'], defaults: ['a', 'hidden', 'b', 'c', 'new', 'off'], on: ['a', 'b', 'c', 'new']});
  assert.deepEqual(h.ids(), ['c', 'a', 'b', 'new']); assert.deepEqual(h.ids('off-budget'), ['off']);
  assert.deepEqual(plain(h.context.reconcileSidebarOrder(['c', 'hidden', 'deleted', 'a'], ['a', 'hidden', 'b', 'c', 'new'])), ['c', 'hidden', 'a', 'b', 'new']);
  const keys = new Set();
  for (const environment of ['sandbox', 'production']) for (const budgetId of ['a/b', 'a:b', '家庭']) for (const workspace of ['personal', 'demo']) keys.add(h.context.sidebarOrderKey({environment, budgetId, workspace}));
  assert.equal(keys.size, 12);
  const next = sidebar({budgetId: 'other', on: ['a', 'b', 'c']});
  h.document.append(next.shell); h.observer.callback();
  assert.deepEqual(next.ids(), ['a', 'b', 'c']);
});

test('invalid preference is ignored; storage failure retains page order through fragment replacement', () => {
  for (const saved of ['broken', '{}', '[1]', '["a","a"]', '[""]', JSON.stringify(['a'.repeat(513)])]) {
    const h = harness({saved}); assert.deepEqual(h.ids(), ['a', 'b', 'c']); assert.equal(h.writes.length, 0);
  }
  const h = harness({blocked: true}); h.click(h.toggle); h.click(h.accounts.b.up);
  assert.deepEqual(h.ids(), ['b', 'a', 'c']); assert.match(h.status.textContent, /for this page.*unavailable/);
  const fresh = sidebar(); h.shell.children = []; h.shell.append(fresh.panel); h.observer.callback();
  assert.deepEqual(fresh.ids(), ['b', 'a', 'c']); assert.equal(fresh.tools.hidden, true);
  assert.equal(h.writes.length, 0);
  h.click(fresh.toggle); h.click(fresh.reset); assert.deepEqual(fresh.ids(), ['a', 'b', 'c']);
});

test('Reset restores default order and Escape leaves edit mode without undoing saved movement', () => {
  const h = harness({saved: ['c', 'b', 'a', 'hidden', 'off']}); h.click(h.toggle);
  h.emit('keydown', h.accounts.c.down, {key: 'Escape'});
  assert.equal(h.tools.hidden, true); assert.deepEqual(h.ids(), ['c', 'b', 'a']); assert.equal(h.document.activeElement, h.toggle);
  h.click(h.toggle); h.click(h.reset);
  assert.deepEqual(h.ids(), ['a', 'b', 'c']); assert.equal(h.storage.has(h.key), false);
  assert.deepEqual(h.writes, [[h.key, null]]);
});

test('drag/drop moves within a group only and does not intercept unrelated drags', () => {
  const h = harness(); const outside = new Element('span'); h.document.append(outside);
  assert.equal(h.emit('dragstart', outside).prevented, false);
  assert.equal(h.emit('dragstart', h.accounts.a.grip).prevented, true);
  h.click(h.toggle);
  assert.equal(h.emit('dragstart', h.accounts.a.up).prevented, true);
  const started = h.emit('dragstart', h.accounts.a.grip);
  assert.equal(started.dataTransfer.effectAllowed, 'move'); assert.equal(started.dataTransfer['text/plain'], 'a');
  assert.equal(h.emit('dragover', h.accounts.off.grip).prevented, false);
  h.emit('drop', h.accounts.off.grip); assert.equal(h.writes.length, 0);
  h.emit('dragstart', h.accounts.a.grip);
  assert.equal(h.emit('dragover', h.accounts.c.grip, {clientY: 59}).prevented, true);
  assert.equal(h.accounts.c.row.dataset.dropPosition, 'after');
  h.emit('drop', h.accounts.c.grip);
  assert.deepEqual(h.ids(), ['b', 'c', 'a']); assert.deepEqual(h.ids('off-budget'), ['off']);
  assert.equal(h.accounts.c.row.dataset.dropPosition, undefined); assert.equal(h.accounts.a.row.classList.values.has('is-dragging'), false);
  assert.equal(h.writes.length, 1);
  h.emit('dragstart', h.accounts.a.grip); h.emit('dragover', h.accounts.b.grip); h.emit('dragend', h.accounts.a.grip);
  assert.equal(h.accounts.b.row.dataset.dropPosition, undefined); assert.equal(h.writes.length, 1);
});

test('storage events synchronize only matching scope, and disposal removes observers/listeners', () => {
  const h = harness();
  h.external('whab.sidebar-order.v1:sandbox:other:personal', '["c","b","a"]');
  assert.deepEqual(h.ids(), ['a', 'b', 'c']);
  h.external(h.key, '["c","b","a"]'); assert.deepEqual(h.ids(), ['c', 'b', 'a']);
  h.external(h.key, 'broken'); assert.deepEqual(h.ids(), ['a', 'b', 'c']);
  h.external(h.key, '["b","c","a"]'); h.external(h.key, null); assert.deepEqual(h.ids(), ['a', 'b', 'c']);
  h.click(h.toggle); h.accounts.b.up.focus();
  h.external(h.key, '["b","c","a"]');
  assert.equal(h.document.activeElement, h.accounts.b.down, 'An external reorder preserves keyboard focus when its previous button becomes disabled');
  h.instance.dispose(); assert.equal(h.observer.disconnected, true);
  assert.equal([...h.document.events.values(), ...h.window.events.values()].every(value => value.length === 0), true);
});
