const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/amount-privacy.js'), 'utf8');
const dataKey = name => name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);

class Element {
  constructor(tag = 'div', attributes = {}, text = '') {
    this.tag = tag; this.localName = tag; this.attributes = {...attributes}; this.textContent = text;
    this.children = []; this.events = new Map(); this.style = {}; this.value = '';
    this.namespaceURI = 'http://www.w3.org/1999/xhtml';
    this.dataset = new Proxy({}, {
      get: (_, key) => this.getAttribute(`data-${dataKey(key)}`) ?? undefined,
      set: (_, key, value) => {this.setAttribute(`data-${dataKey(key)}`, value); return true;},
    });
    this.classList = {add: name => {const names = new Set((this.getAttribute('class') || '').split(' ').filter(Boolean)); names.add(name); this.setAttribute('class', [...names].join(' '));}};
  }
  append(...items) {for (const item of items) {item.remove(); item.parentElement = this; this.children.push(item);}}
  after(item) {item.remove(); item.parentElement = this.parentElement; const index = this.parentElement.children.indexOf(this); this.parentElement.children.splice(index + 1, 0, item);}
  remove() {if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(item => item !== this); this.parentElement = null;}
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(item => this.matches(item.trim()));
    const match = selector.match(/^([a-z-]+)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/);
    if (!match) throw Error(`Unsupported selector: ${selector}`);
    return (!match[1] || this.tag === match[1]) && (!match[2] || this.hasAttribute(match[2]) && (match[3] === undefined || this.getAttribute(match[2]) === match[3]));
  }
  closest(selector) {return this.matches(selector) ? this : this.parentElement?.closest(selector) || null;}
  querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
  querySelectorAll(selector) {return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);}
  setAttribute(name, value) {this.attributes[name] = String(value);}
  getAttribute(name) {return this.attributes[name] ?? null;}
  hasAttribute(name) {return this.getAttribute(name) !== null;}
  removeAttribute(name) {delete this.attributes[name];}
  get isConnected() {let root = this; while (root.parentElement) root = root.parentElement; return root.tag === 'document';}
  get previousElementSibling() {return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1] || null;}
  get nextElementSibling() {return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null;}
  get hidden() {return this.hasAttribute('hidden');}
  set hidden(value) {if (value) this.setAttribute('hidden', ''); else this.removeAttribute('hidden');}
  get title() {return this.getAttribute('title') || '';}
  set title(value) {this.setAttribute('title', value);}
  addEventListener(type, callback) {this.events.set(type, [...this.events.get(type) || [], callback]);}
  dispatchEvent(event) {let root = this; while (root.parentElement) root = root.parentElement; return !root.emit(event.type, this, event).prevented;}
  focus() {
    let root = this; while (root.parentElement) root = root.parentElement;
    if (root.activeElement === this) return;
    const previous = root.activeElement; root.activeElement = this;
    if (previous) for (const type of ['change', 'blur', 'focusout']) root.emit(type, previous, {relatedTarget: this});
  }
}
function harness({saved, blocked = false, preparse = false} = {}) {
  const document = new Element('document'), window = new Element('window'), root = new Element('html');
  document.documentElement = root; document.append(root);
  document.createElement = tag => new Element(tag);
  document.createElementNS = (namespace, tag) => {const node = new Element(tag); node.namespaceURI = namespace; return node;};
  const body = new Element('body', {'data-private-currency': 'USD'}), toggle = new Element('button', {'data-amount-privacy-toggle': '', hidden: ''}), status = new Element('span', {'data-amount-privacy-status': ''});
  const amount = new Element('span', {'data-private-amount': '', title: '$1,234.56 available'}, '$1,234.56');
  const input = new Element('input', {'data-private-amount': '', 'aria-label': 'Assign $234.56', title: '$234.56'}); input.value = '234.56';
  const label = new Element('span', {'data-private-amount-attributes': 'title aria-label aria-valuenow', title: 'Available $1,234.56', 'aria-label': 'Balance $1,234.56', 'aria-valuenow': '1234.56'});
  const ordinary = new Element('span', {}, 'November 2026 · 19 transactions · 20%');
  body.append(toggle, status, amount, input, label, ordinary);
  if (!preparse) root.append(body);
  const storage = new Map(saved === undefined ? [] : [['whab.amounts', saved]]), writes = [];
  window.localStorage = {
    getItem(key) {if (blocked) throw Error('blocked'); return storage.get(key) ?? null;},
    setItem(key, value) {if (blocked) throw Error('blocked'); storage.set(key, value); writes.push([key, value]);},
  };
  let observer;
  const context = {document, window, Event: class {constructor(type, options) {this.type = type; Object.assign(this, options);}}, MutationObserver: class {constructor(callback) {observer = this; this.callback = callback;} observe() {} disconnect() {}}};
  const emit = document.emit = (type, target, extra = {}) => {
    const event = {target, prevented: false, stopped: false, immediate: false, ...extra,
      preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;}, stopImmediatePropagation() {this.immediate = true; this.stopped = true;}};
    for (const callback of document.events.get(type) || []) {callback(event); if (event.immediate) break;}
    return event;
  };
  vm.runInNewContext(source, context);
  return {document, window, root, body, toggle, status, amount, input, label, ordinary, observer, storage, writes, emit,
    parse() {root.append(body); observer.callback([{type: 'childList', target: root}]);},
    click(target) {const down = emit('pointerdown', target); if (!down.prevented) target.focus(); return emit('click', target);},
    change(node, name, value) {if (value === null) node.removeAttribute(name); else node.setAttribute(name, value); observer.callback([{type: 'attributes', target: node, attributeName: name}]);},
    refresh() {observer.callback([]);},
    external(key, newValue) {for (const callback of window.events.get('storage') || []) callback({key, newValue});},
  };
}

test('prepaint preference precedes body parsing; placeholders keep a fixed length and restore exact originals', () => {
  const h = harness({saved: 'hidden', preparse: true});
  assert.equal(h.root.dataset.amounts, 'hidden'); assert.equal(h.writes.length, 0);
  h.parse();
  assert.equal(h.toggle.hidden, false); assert.equal(h.toggle.getAttribute('aria-label'), 'Show amounts');
  assert.equal(h.toggle.getAttribute('aria-pressed'), 'true');
  assert.equal(h.amount.textContent, '$1,234.56'); assert.equal(h.amount.nextElementSibling.textContent, '$X,XXX');
  assert.equal(h.amount.nextElementSibling.hidden, false); assert.equal(h.input.nextElementSibling.textContent, '$X,XXX');
  assert.equal(h.ordinary.nextElementSibling, null); assert.equal(h.ordinary.textContent, 'November 2026 · 19 transactions · 20%');
  assert.equal(h.amount.title, 'Amount hidden'); assert.equal(h.input.getAttribute('aria-label'), 'Amount hidden');
  assert.equal(h.label.getAttribute('aria-valuenow'), null); assert.equal(h.label.title, 'Amount hidden');
  h.click(h.toggle);
  assert.equal(h.root.dataset.amounts, 'shown'); assert.equal(h.amount.nextElementSibling.hidden, true);
  assert.equal(h.amount.title, '$1,234.56 available'); assert.equal(h.input.getAttribute('aria-label'), 'Assign $234.56');
  assert.equal(h.label.getAttribute('aria-valuenow'), '1234.56'); assert.equal(h.label.title, 'Available $1,234.56');
  assert.deepEqual(h.writes, [['whab.amounts', 'shown']]); assert.equal(h.toggle.getAttribute('aria-pressed'), 'false');
});

test('hiding a focused money draft cannot cause blur/change saves; reveal retains its exact value', () => {
  const h = harness(); let saves = 0;
  h.document.addEventListener('blur', () => saves++); h.document.addEventListener('change', () => saves++);
  h.input.focus(); h.input.value = '000123.4500';
  const down = h.emit('pointerdown', h.toggle); assert.equal(down.prevented, true);
  h.emit('click', h.toggle);
  assert.equal(saves, 0); assert.equal(h.input.value, '000123.4500'); assert.equal(h.document.activeElement, h.toggle);
  const mask = h.input.nextElementSibling;
  assert.equal(mask.type, 'button'); assert.equal(mask.getAttribute('aria-label'), 'Show amounts to edit');
  h.click(mask);
  assert.equal(h.root.dataset.amounts, 'shown'); assert.equal(h.input.value, '000123.4500'); assert.equal(h.document.activeElement, h.input);
  // Unrelated controls and ordinary shown edits retain their own save behavior.
  saves = 0; assert.equal(h.emit('change', h.input).immediate, false); assert.equal(saves, 1);
  h.click(h.toggle); saves = 0;
  const unrelated = new Element('input'); h.body.append(unrelated);
  assert.equal(h.emit('change', unrelated).immediate, false); assert.equal(saves, 1);
});

test('currency masks use one fixed four-digit pattern for every size, sign and precision', () => {
  const h = harness({saved: 'hidden'});
  for (const value of ['0', '5', '99.99', '1234.56', '-123456789.000', '000123.4500', '']) {
    h.input.value = value; h.refresh();
    assert.equal(h.input.nextElementSibling.textContent, '$X,XXX');
    assert.equal(h.input.value, value);
  }
  for (const [currency, expected] of [['JPY', 'JPY X,XXX'], ['KWD', 'KWD X,XXX'], ['EUR', 'EUR X,XXX'], ['', 'X,XXX'], ['bad-currency', 'X,XXX']]) {
    h.change(h.input, 'data-private-currency', currency);
    assert.equal(h.input.nextElementSibling.textContent, expected);
    assert.doesNotMatch(h.input.nextElementSibling.textContent, /\d/);
  }
  h.change(h.input, 'data-private-amount', 'Hidden balance');
  assert.equal(h.input.nextElementSibling.textContent, 'Hidden balance', 'Explicit fixed plugin placeholders retain their contract');
});

test('unavailable balances use the same mask and restore their unavailable meaning', () => {
  const h = harness({saved: 'hidden'});
  const balance = new Element('strong', {'data-private-amount': '', 'data-private-currency':'USD', title:'Balance unavailable'}, 'Unavailable');
  h.body.append(balance); h.refresh();
  assert.equal(balance.nextElementSibling.textContent, '$X,XXX');
  assert.equal(balance.title, 'Amount hidden');
  h.click(h.toggle);
  assert.equal(balance.textContent, 'Unavailable');
  assert.equal(balance.title, 'Balance unavailable');
  assert.equal(balance.nextElementSibling.hidden, true);
});

test('revealing a rounded amount opens its exact editor without changing cents', () => {
  const h = harness({saved: 'hidden'});
  h.input.setAttribute('data-money-rounded', ''); h.input.setAttribute('data-money-idle', '');
  h.input.value = '000123.4500'; let edits = 0;
  h.document.addEventListener('money-edit', event => {edits++; event.target.removeAttribute('data-money-idle');});
  h.click(h.input.nextElementSibling);
  assert.equal(edits, 1); assert.equal(h.input.hasAttribute('data-money-idle'), false);
  assert.equal(h.input.value, '000123.4500'); assert.equal(h.document.activeElement, h.input);
  assert.equal(h.root.dataset.amounts, 'shown');
});

test('privacy reveal lets the grid register editing before exact input receives typing', () => {
  const h = harness({saved: 'hidden'}); let activations = 0, fallbackEdits = 0;
  h.input.setAttribute('data-money-rounded', ''); h.input.setAttribute('data-money-idle', '');
  h.document.addEventListener('money-display-activate', event => {
    activations++; event.preventDefault();
    event.target.setAttribute('data-grid-editing', 'true'); event.target.removeAttribute('data-money-idle');
    event.target.focus();
  });
  h.document.addEventListener('money-edit', () => fallbackEdits++);
  h.click(h.input.nextElementSibling);
  assert.equal(activations, 1); assert.equal(fallbackEdits, 0);
  assert.equal(h.input.dataset.gridEditing, 'true'); assert.equal(h.document.activeElement, h.input);
  assert.equal(h.input.value, '234.56');
});

test('monetary masks follow account currency choices without editing or saving the amount', () => {
  const h = harness({saved: 'hidden'});
  const form = new Element('form'), currency = new Element('select', {name: 'currency'});
  const balance = new Element('span', {'data-private-amount':'', 'data-private-currency':'USD'}, '$1,234.56');
  currency.value = 'EUR'; form.append(currency, h.input, balance); h.body.append(form); h.refresh();
  assert.equal(h.input.nextElementSibling.textContent, 'EUR X,XXX');
  assert.equal(balance.nextElementSibling.textContent, '$X,XXX', 'Committed balance keeps its own currency while a different currency is only a draft');
  currency.value = 'JPY'; h.emit('change', currency);
  assert.equal(h.input.nextElementSibling.textContent, 'JPY X,XXX');
  assert.equal(h.input.value, '234.56');
  assert.equal(h.writes.length, 0);
  h.change(balance, 'data-private-currency', 'JPY');
  assert.equal(balance.nextElementSibling.textContent, 'JPY X,XXX', 'A refreshed saved balance adopts its authoritative currency');
});

test('dynamic rows and genuine metadata updates remain masked and restore the latest exact data', () => {
  const h = harness({saved: 'hidden'});
  h.change(h.label, 'title', 'Available $9,876.00'); assert.equal(h.label.title, 'Amount hidden');
  h.change(h.label, 'aria-valuenow', '9876'); assert.equal(h.label.getAttribute('aria-valuenow'), null);
  h.change(h.input, 'aria-label', null); assert.equal(h.input.getAttribute('aria-label'), null);
  h.amount.textContent = '-$9,876.00'; h.refresh(); assert.equal(h.amount.nextElementSibling.textContent, '$X,XXX');
  const fresh = new Element('span', {'data-private-amount': ''}, '999999999.99'); h.body.append(fresh); h.refresh();
  assert.equal(fresh.nextElementSibling.textContent, '$X,XXX'); assert.equal(fresh.nextElementSibling.hidden, false);
  const oldMask = h.amount.nextElementSibling; h.amount.remove(); h.refresh(); assert.equal(oldMask.isConnected, false);
  assert.equal(h.amount.title, '$1,234.56 available');
  h.body.append(h.amount); h.refresh(); assert.equal(h.amount.title, 'Amount hidden');
  h.click(h.toggle);
  assert.equal(h.label.title, 'Available $9,876.00'); assert.equal(h.label.getAttribute('aria-valuenow'), '9876');
  assert.equal(h.input.getAttribute('aria-label'), null); assert.equal(fresh.textContent, '999999999.99');
  assert.equal(h.amount.title, '$1,234.56 available');
});

test('removing private markers restores formerly owned attributes without stale masking', () => {
  const h = harness({saved: 'hidden'});
  h.change(h.label, 'data-private-amount-attributes', 'title');
  assert.equal(h.label.getAttribute('aria-label'), 'Balance $1,234.56'); assert.equal(h.label.getAttribute('aria-valuenow'), '1234.56');
  assert.equal(h.label.title, 'Amount hidden');
  h.change(h.label, 'data-private-amount-attributes', null); assert.equal(h.label.title, 'Available $1,234.56');
  const mask = h.amount.nextElementSibling; h.change(h.amount, 'data-private-amount', null);
  assert.equal(mask.isConnected, false); assert.equal(h.amount.title, '$1,234.56 available');
});

test('opaque sections, hidden controls and SVG text preserve their own visibility and structure', () => {
  const h = harness({saved: 'hidden'});
  const block = new Element('div', {'data-private-content': 'Chart hidden'}), nested = new Element('span', {'data-private-amount': ''}, '$42');
  block.append(nested); h.body.append(block); h.refresh();
  assert.equal(block.nextElementSibling.textContent, 'Chart hidden'); assert.equal(nested.nextElementSibling, null);
  const emphasized = new Element('strong', {'data-private-amount': ''}, '$123'); h.body.append(emphasized); h.refresh();
  assert.equal(emphasized.nextElementSibling.localName, 'strong', 'Fixed placeholders preserve semantic amount emphasis');
  h.change(h.input, 'hidden', ''); assert.equal(h.input.nextElementSibling.hidden, true);
  h.change(h.input, 'hidden', null); assert.equal(h.input.nextElementSibling.hidden, false);
  h.input.style.display = 'none'; h.refresh(); assert.equal(h.input.nextElementSibling.hidden, true);
  h.input.style.display = ''; h.refresh(); assert.equal(h.input.nextElementSibling.hidden, false);
  const svg = h.document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const text = h.document.createElementNS(svg.namespaceURI, 'text'); text.setAttribute('data-private-amount', ''); text.setAttribute('x', '42'); text.setAttribute('y', '17'); text.textContent = '$75'; svg.append(text); h.body.append(svg); h.refresh();
  const mask = text.nextElementSibling; assert.equal(mask.namespaceURI, svg.namespaceURI); assert.equal(mask.getAttribute('x'), '42'); assert.equal(mask.getAttribute('y'), '17'); assert.equal(mask.textContent, '$X,XXX');
  h.click(h.toggle); assert.equal(mask.hidden, true); assert.equal(text.textContent, '$75');
});

test('cross-tab synchronization, reset and unavailable storage keep the toggle usable', () => {
  const h = harness();
  h.external('unrelated', 'hidden'); h.external('whab.amounts', 'invalid'); assert.equal(h.root.dataset.amounts, 'shown');
  h.external('whab.amounts', 'hidden'); assert.equal(h.root.dataset.amounts, 'hidden');
  h.external('whab.amounts', null); assert.equal(h.root.dataset.amounts, 'shown');
  h.external('whab.amounts', 'hidden'); h.external(null, null); assert.equal(h.root.dataset.amounts, 'shown');
  assert.equal(h.writes.length, 0);
  for (const saved of ['invalid', '', 'true']) assert.equal(harness({saved}).root.dataset.amounts, 'shown');
  const blocked = harness({blocked: true}); blocked.click(blocked.toggle);
  assert.equal(blocked.root.dataset.amounts, 'hidden'); assert.match(blocked.status.textContent, /for this page.*unavailable/);
  assert.equal(blocked.writes.length, 0); blocked.click(blocked.input.nextElementSibling); assert.equal(blocked.root.dataset.amounts, 'shown');
});
