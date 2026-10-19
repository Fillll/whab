/* Run with node --test plugins/interface/web/tests/logo_images.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/logo-images.js'), 'utf8');
function events() {
  const listeners = new Map();
  return {
    addEventListener(name, handler) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(handler); },
    removeEventListener(name, handler) { listeners.get(name)?.delete(handler); },
    emit(name) { for (const handler of [...(listeners.get(name) || [])]) handler(); },
  };
}

function harness({observer = true} = {}) {
  const images = [], started = [], timers = new Map(), observed = new Set();
  let receive, timer = 0;
  const document = {...events(), querySelectorAll: () => images};
  const window = {...events(), innerWidth: 1200, innerHeight: 900};
  const context = {document, window,
    setTimeout(callback) { const id = ++timer; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  if (observer) context.IntersectionObserver = class {
    constructor(callback) {receive = callback;}
    observe(parent) {observed.add(parent);}
    unobserve(parent) {observed.delete(parent);}
  };
  vm.runInNewContext(source.replace('export function ', 'function '), context);
  function image(name, {visible = true, cached = false, copied = false, eager = false} = {}) {
    let src = eager ? `/api/logos/${name}` : null;
    const fallback = {hidden: false};
    const classes = new Set();
    const parent = {rect: {width: visible ? 18 : 0, height: visible ? 18 : 0, top: 20, left: 20, bottom: 38, right: 38},
      getBoundingClientRect() {return this.rect;},
      classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name);}, contains(name) {return classes.has(name);}}};
    const image = {...events(), name, dataset: {logoSrc: `/api/logos/${name}`, ...(copied ? {logoBound: 'true'} : {})},
      parentElement: parent, previousElementSibling: fallback, hidden: true, isConnected: true,
      complete: cached, naturalWidth: cached ? 18 : 0,
      getAttribute(name) {return name === 'src' ? src : null;},
      removeAttribute(name) {if (name === 'src') src = null;},
      load() {this.naturalWidth = 18; this.complete = true; this.emit('load');},
      fail() {this.naturalWidth = 0; this.complete = true; this.emit('error');},
    };
    Object.defineProperty(image, 'src', {get: () => src, set: value => {src = value; started.push(name);}});
    images.push(image);
    return image;
  }
  return {image, images, started, timers, observed, document, window, context,
    bind: subset => context.bindLogos(subset ? {querySelectorAll: () => subset} : document),
    intersect(subset, visible = true) {receive(subset.map(image => ({target: image.parentElement, isIntersecting: visible, boundingClientRect: image.parentElement.rect})));},
    expireFirst() {const [id, callback] = timers.entries().next().value; timers.delete(id); callback();},
  };
}

test('decorative artwork observes visible fallback wrappers and starts only two requests', () => {
  const state = harness();
  const first = state.image('first'), second = state.image('second'), third = state.image('third');
  const popup = state.image('popup', {visible: false});
  state.bind();
  assert.deepEqual(state.started, []);
  assert.equal(state.observed.has(first.parentElement), true);
  state.intersect([first, second, third, popup]);
  assert.deepEqual(state.started, ['first', 'second']);
  assert.equal(popup.src, null);
  first.load();
  assert.deepEqual(state.started, ['first', 'second', 'third']);
  assert.equal(first.hidden, false);
  assert.equal(first.previousElementSibling.hidden, true);
  assert.equal(first.parentElement.classList.contains('has-logo'), true);
  popup.parentElement.rect.width = popup.parentElement.rect.height = 18;
  state.intersect([popup]);
  second.fail();
  assert.deepEqual(state.started, ['first', 'second', 'third', 'popup']);
  assert.equal(second.hidden, true);
  assert.equal(second.previousElementSibling.hidden, false);
});

test('failed and eventless images release slots while late events cannot resurrect them', () => {
  const state = harness();
  const first = state.image('timeout'), second = state.image('other'), third = state.image('next');
  state.bind(); state.intersect([first, second, third]);
  state.expireFirst();
  assert.deepEqual(state.started, ['timeout', 'other', 'next']);
  assert.equal(first.src, null);
  first.load();
  assert.equal(first.hidden, true);
  assert.equal(first.previousElementSibling.hidden, false);
  assert.equal(first.parentElement.classList.contains('has-logo'), false);
  assert.equal(state.timers.size, 2);
});

test('detached register images are canceled and queued detached images never begin', () => {
  const state = harness();
  const first = state.image('old'), second = state.image('kept'), third = state.image('detached');
  state.bind(); state.intersect([first, second, third]);
  first.isConnected = third.isConnected = false;
  const fresh = state.image('fresh');
  state.bind([fresh]); state.intersect([fresh]);
  assert.equal(first.src, null);
  assert.deepEqual(state.started, ['old', 'kept', 'fresh']);
  first.load(); second.load(); fresh.load();
  assert.equal(state.timers.size, 0);
  assert.equal(state.observed.size, 0);
});

test('cached marks are displayed immediately and copied binding attributes do not skip clones', () => {
  const state = harness();
  const cached = state.image('cached', {cached: true, eager: true});
  const clone = state.image('clone', {copied: true, eager: true});
  state.bind(); state.bind();
  assert.equal(cached.hidden, false);
  assert.equal(cached.previousElementSibling.hidden, true);
  assert.equal(clone.src, null);
  state.intersect([clone]);
  assert.deepEqual(state.started, ['clone']);
  clone.load();
  assert.equal(clone.hidden, false);
});

test('queued images that leave view wait until their fallback becomes visible again', () => {
  const state = harness();
  const first = state.image('first'), second = state.image('second'), third = state.image('later');
  state.bind(); state.intersect([first, second, third]);
  state.intersect([third], false);
  first.load();
  assert.deepEqual(state.started, ['first', 'second']);
  state.intersect([third]);
  assert.deepEqual(state.started, ['first', 'second', 'later']);
});

test('browsers without observation defer hidden popup marks until a visible interaction', () => {
  const state = harness({observer: false});
  const visible = state.image('visible'), popup = state.image('popup', {visible: false});
  state.bind();
  assert.deepEqual(state.started, ['visible']);
  popup.parentElement.rect.width = popup.parentElement.rect.height = 18;
  state.document.emit('click');
  assert.deepEqual(state.started, ['visible', 'popup']);
  visible.load(); popup.fail();
  assert.equal(state.timers.size, 0);
});

test('register replacement binds refreshed sidebar artwork as well as main content', () => {
  const state = harness();
  const main = state.image('new-register'), sidebar = state.image('new-sidebar');
  const app = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
  const navigation = app.slice(app.indexOf('const accountNavigation = initAccountNavigation'), app.indexOf('function minorAmount'));
  let options, disposed = false;
  Object.assign(state.context, {
    currentRegister: {dispose() {disposed = true;}},
    flushEdits: async () => true, notify() {},
    initAccountNavigation(value) {options = value; return {};},
    initTransactionRegister: () => ({dispose() {}}),
    initBudgetEdits: () => ({dispose() {return true;}}),
    initPageControls() {}, formBody() {}, CustomEvent: class {},
  });
  state.context.document.dispatchEvent = () => true;
  vm.runInNewContext(navigation, state.context);
  options.onReplace({content: {querySelectorAll: () => [main]}});
  assert.equal(disposed, true);
  assert.equal(state.observed.has(main.parentElement), true);
  assert.equal(state.observed.has(sidebar.parentElement), true);
});
