const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, `../src/whab_plugin_web/static/${name}.js`), 'utf8');
const dataKey = name => name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);

function harness({privacy = false, hidden = false, value = '-68000.49', disabled = false} = {}) {
  const observers = [], microtasks = [], listeners = new Map(); let document;
  function mutation(target, type, attributeName) {
    if (!target.isConnected) return;
    for (const observer of observers) if (observer.options && (type === 'childList' ? observer.options.childList : observer.options.attributes && (!observer.options.attributeFilter || observer.options.attributeFilter.includes(attributeName)))) {
      observer.records.push({target, type, attributeName});
    }
  }
  class Element {
    constructor(tag, attributes = {}) {
      this.localName = tag; this.attributes = {...attributes}; this.children = []; this.events = new Map(); this.style = {}; this.value = ''; this.text = '';
      this.namespaceURI = 'http://www.w3.org/1999/xhtml';
      this.dataset = new Proxy({}, {get: (_, key) => this.getAttribute(`data-${dataKey(key)}`) ?? undefined,
        set: (_, key, value) => {this.setAttribute(`data-${dataKey(key)}`, value); return true;},
        deleteProperty: (_, key) => {this.removeAttribute(`data-${dataKey(key)}`); return true;}});
      this.classList = {add: name => {const values = new Set((this.getAttribute('class') || '').split(' ').filter(Boolean)); values.add(name); this.setAttribute('class', [...values].join(' '));}};
    }
    get isConnected() {return this === root || Boolean(this.parentElement?.isConnected);}
    setAttribute(name, value) {this.attributes[name] = String(value); mutation(this, 'attributes', name);}
    getAttribute(name) {return this.attributes[name] ?? null;}
    hasAttribute(name) {return Object.hasOwn(this.attributes, name);}
    removeAttribute(name) {if (this.hasAttribute(name)) {delete this.attributes[name]; mutation(this, 'attributes', name);}}
    get hidden() {return this.hasAttribute('hidden');} set hidden(value) {value ? this.setAttribute('hidden', '') : this.removeAttribute('hidden');}
    get disabled() {return this.hasAttribute('disabled');} set disabled(value) {value ? this.setAttribute('disabled', '') : this.removeAttribute('disabled');}
    get title() {return this.getAttribute('title') || '';} set title(value) {this.setAttribute('title', value);}
    get textContent() {return this.text;} set textContent(value) {this.text = String(value); mutation(this, 'childList');}
    get nextElementSibling() {return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null;}
    get previousElementSibling() {return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1] || null;}
    append(...nodes) {for (const node of nodes) {node.remove(); node.parentElement = this; this.children.push(node); mutation(this, 'childList');}}
    after(node) {node.remove(); const parent = this.parentElement; node.parentElement = parent; parent.children.splice(parent.children.indexOf(this) + 1, 0, node); mutation(parent, 'childList');}
    remove() {if (this.parentElement) {const parent = this.parentElement; parent.children.splice(parent.children.indexOf(this), 1); this.parentElement = null; mutation(parent, 'childList');}}
    matches(selector) {
      if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
      const match = selector.match(/^([a-z-]+)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/);
      if (!match) throw Error(`Unsupported selector: ${selector}`);
      return (!match[1] || this.localName === match[1]) && (!match[2] || this.hasAttribute(match[2]) && (match[3] === undefined || this.getAttribute(match[2]) === match[3]));
    }
    closest(selector) {return this.matches(selector) ? this : this.parentElement?.closest(selector) || null;}
    querySelectorAll(selector) {return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);}
    querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
    addEventListener(type, callback) {(this.events.get(type) || (this.events.set(type, []), this.events.get(type))).push(callback);}
    dispatchEvent(event) {return emit(event.type, this);}
    focus() {const previous = document.activeElement; document.activeElement = this; if (previous && previous !== this) {emit('blur', previous, {relatedTarget: this}); emit('focusout', previous, {relatedTarget: this});} emit('focusin', this);}
    select() {this.selectionStart = 0; this.selectionEnd = this.value.length;}
  }
  const root = new Element('html'), body = new Element('body', {'data-private-currency': 'USD'});
  root.append(body);
  const field = new Element('input', {'data-private-amount':'', 'data-money-rounded':'', 'data-minor-units':'2', 'aria-label':'Transaction amount'});
  field.value = value; if (disabled) field.disabled = true;
  const toggle = new Element('button', {'data-amount-privacy-toggle':''}), status = new Element('span', {'data-amount-privacy-status':''}), other = new Element('button');
  body.append(field, toggle, status, other);
  const emit = (type, target, extra = {}) => {
    const event = {type, target, ...extra, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {this.stopped = true;}};
    for (const callback of listeners.get(type) || []) {callback(event); if (event.stopped) break;}
    if (!event.stopped) for (const callback of target.events.get(type) || []) callback(event);
    return true;
  };
  document = {documentElement:root, activeElement:other, createElement:tag=>new Element(tag), createElementNS:(_,tag)=>new Element(tag),
    querySelectorAll:selector=>root.querySelectorAll(selector),
    addEventListener(type, callback) {(listeners.get(type) || (listeners.set(type, []), listeners.get(type))).push(callback);}};
  const context = {document, window:{localStorage:{getItem:()=>hidden?'hidden':'shown',setItem(){}}, addEventListener(){}}, Intl, Number, BigInt, Event,
    queueMicrotask:callback=>microtasks.push(callback),
    MutationObserver:class {constructor(callback) {this.callback = callback; this.records=[]; observers.push(this);} observe(_,options) {this.options=options;} disconnect() {this.options=null; this.records=[];}}};
  if (privacy) vm.runInNewContext(source('amount-privacy'), context);
  vm.runInNewContext(source('money-display'), context);
  function settle() {
    for (let turns = 0; turns < 50; turns++) {
      while (microtasks.length) microtasks.shift()();
      const ready = observers.filter(observer => observer.records.length);
      if (!ready.length) return turns;
      for (const observer of ready) {const records=observer.records.splice(0); observer.callback(records);}
    }
    throw new Error('Money display and privacy mutation observers did not settle');
  }
  settle();
  return {field, root, body, toggle, other, document, Element, emit, settle,
    display:()=>body.querySelector('[data-money-display]'),
    mask:()=>body.querySelector('[data-private-mask]'),
    refresh(){emit('money-refresh',field);settle();},
    edit(){emit('click',this.display());settle();},
    leave(){other.focus();settle();},
  };
}

test('idle money is comma grouped and half-away rounded while the exact editor and no-op interaction preserve every digit', () => {
  const h = harness(); let changes=0;
  h.field.addEventListener('change',()=>changes++);
  for (const [value,shown] of [['68000.00','68,000'],['1000.49','1,000'],['-1234.50','-1,235'],['0.49','0'],['-0.49','0'],['0.50','1'],['-0.50','-1'],['1234.567','1,235'],['1234','1,234'],['00012.3400','12']]) {
    h.field.value=value; h.refresh(); assert.equal(h.display().textContent,shown);
    h.edit(); assert.equal(h.field.value,value); assert.equal(h.document.activeElement,h.field); assert.equal(h.display().hidden,true);
    h.leave(); assert.equal(h.field.value,value); assert.equal(h.display().hidden,false); assert.equal(h.field.hasAttribute('data-money-idle'),true);
  }
  assert.equal(changes,0,'Displaying or opening an exact draft never dispatches a save');
});

test('exact edited drafts, invalid text and disabled controls survive display changes and node refreshes', () => {
  const h=harness(); h.edit(); h.field.value='000987.6500'; h.emit('input',h.field); h.settle();
  assert.equal(h.field.value,'000987.6500'); assert.equal(h.display().hidden,true);
  h.leave(); assert.equal(h.display().textContent,'988'); assert.equal(h.field.value,'000987.6500');
  h.field.value='broken'; h.refresh(); assert.equal(h.display().textContent,'Invalid amount');
  h.field.disabled=true; h.settle(); assert.equal(h.display().disabled,true); h.edit(); assert.equal(h.document.activeElement,h.other);
  h.display().remove(); h.settle(); assert.equal(h.display().textContent,'Invalid amount'); assert.equal(h.field.value,'broken');
  h.field.remove(); h.settle(); assert.equal(h.display(),null);
});

test('privacy and rounded display settle together and reveal the precise draft without duplicate masks or save events', () => {
  const h=harness({privacy:true}); let changes=0; h.field.addEventListener('change',()=>changes++);
  assert.equal(h.display().textContent,'-68,000'); assert.equal(h.mask().textContent,'$X,XXX');
  h.edit(); h.field.value='-123.4500'; h.emit('input',h.field); h.settle();
  h.emit('click',h.toggle); h.settle(); assert.equal(h.root.dataset.amounts,'hidden'); assert.equal(h.field.value,'-123.4500');
  h.emit('click',h.mask()); h.settle(); assert.equal(h.root.dataset.amounts,'shown'); assert.equal(h.document.activeElement,h.field); assert.equal(h.field.value,'-123.4500'); assert.equal(h.display().hidden,true);
  h.leave(); assert.equal(h.display().textContent,'-123'); assert.equal(changes,0);
  for(let count=0;count<4;count++){h.emit('click',h.toggle);assert.ok(h.settle()<15);}
  assert.equal(h.body.querySelectorAll('[data-money-display]').length,1); assert.equal(h.body.querySelectorAll('[data-private-mask]').length,1);
});

test('native invalid feedback opens the exact editor without changing the invalid draft', () => {
  const h=harness({value:'1.234'}); h.emit('invalid',h.field);h.settle();
  assert.equal(h.field.value,'1.234');assert.equal(h.document.activeElement,h.field);assert.equal(h.display().hidden,true);
});

test('read-only subscription money rounds per currency without negative zero or precision in the label', () => {
  const context={Intl};vm.runInNewContext(source('subscriptions').replace(/^import .*$/gm,'').replaceAll('export function ','function '),context);
  for(const [minor,currency,expected] of [[150,'USD','$2'],[-150,'USD','-$2'],[-49,'USD','$0'],[6800049,'USD','$68,000'],[1234,'JPY','¥1,234'],[1234500,'KWD','KWD 1,235'],[-499,'KWD','KWD 0']]){
    assert.equal(context.money(minor,currency),expected);
  }
});
