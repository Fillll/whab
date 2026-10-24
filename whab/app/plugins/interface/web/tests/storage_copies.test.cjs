/* Deterministic host replies exercise reviews, unknown outcomes and stale dialogs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/storage-copies.js'), 'utf8');
const C = '11111111-1111-4111-8111-111111111111';
const I = '22222222-2222-4222-8222-222222222222';
const E = '33333333-3333-4333-8333-333333333333';
const T = '44444444-4444-4444-8444-444444444444';
const NEW = '55555555-5555-4555-8555-555555555555';
const plain = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {resolve, reject, promise};}
function status({copies = [], operations = [], primary = {}, errors = {}} = {}) {
  return {primary: {supported: true, plugin_id: 'sqlite', instance_id: I, authority_epoch: E,
    generation: 7, authoritative: true, role: 'primary', health: 'ready', admission: 'open', ...primary}, copies, operations, errors};
}
function copy(state = 'saved') {
  return {id: C, destination: 'json', state, error: null, watermark: {source_generation: 7, namespace_generation: 2}};
}
function operation(state) {return {id: T, copy_id: C, state, error: state === 'failed' ? {code: 'promotion_failed'} : null};}
function runtime(overrides = {}) {
  const context = {setTimeout, clearTimeout, ...overrides};
  vm.runInNewContext(source.replace(/^import .*$/m, '').replaceAll('export function ', 'function ') +
    '\nthis.create = createStorageCopies; this.init = initStorageCopies;', context);
  return context;
}
function harness({initial = status(), flush = async () => true} = {}) {
  const requests = [], snapshots = [], timers = new Set();
  let latest = initial, current = true, changes = 0, next = null;
  const controller = runtime().create({
    request: async (path, body, expected) => {
      requests.push({path, body: plain(body), expected});
      if (body === undefined) return typeof latest === 'function' ? latest() : latest;
      const answer = next; next = null;
      if (answer instanceof Error) throw answer;
      return typeof answer === 'function' ? answer() : answer;
    }, beforeChange: async () => {changes += 1; return flush();},
    isCurrent: () => current, onState: state => snapshots.push(plain(state)),
    schedule: callback => {timers.add(callback); return callback;}, cancel: timer => timers.delete(timer),
  });
  return {controller, requests, snapshots, timers, get state() {return snapshots.at(-1);},
    get changes() {return changes;}, reply: value => {next = value;}, status: value => {latest = value;},
    current: value => {current = value;}, mutations: () => requests.filter(row => row.body !== undefined)};
}
async function preview(state) {
  state.reply({preview_id: C, destination: 'json', source_generation: 7, supported: true});
  await state.controller.preview('json');
}
async function reviewedPromotion(state) {
  state.status(status({copies: [copy()]}));
  await state.controller.reviewPromotion(C);
}

test('an empty destination preview is reviewed before a separate explicit attachment', async () => {
  const state = harness(); await state.controller.start(); await preview(state);
  assert.equal(state.state.review.kind, 'attach');
  assert.deepEqual(state.mutations().map(row => row.path), ['/storage/copies/preview']);
  state.reply({id: C, state: 'pending'}); state.status(status({copies: [copy('pending')]}));
  await state.controller.attach();
  assert.equal(state.state.review, null);
  assert.equal(state.state.status.copies[0].state, 'pending');
  assert.match(state.state.message, /waiting|Follow/);
  assert.equal(state.state.message.includes('now the authoritative'), false);
  assert.equal(state.changes, 2);
});

test('preview and attach cancellation never submit an implicit mutation', async () => {
  const state = harness(); await state.controller.start(); await preview(state);
  state.controller.dismiss(); await state.controller.attach();
  assert.equal(state.mutations().length, 1);
  await state.controller.preview('sqlite');
  assert.equal(state.mutations().length, 1);
});

test('202 promotion stays pending through queued/running and requires matching durable authority', async () => {
  const state = harness(); await state.controller.start(); await reviewedPromotion(state);
  assert.equal(state.mutations().length, 0);
  state.reply(() => {state.status(status({copies: [copy()], operations: [operation('queued')]})); return {id: T, state: 'queued'};});
  await state.controller.promote();
  assert.match(state.state.message, /Waiting/); assert.equal(state.state.ticket.id, T);
  for (const phase of ['running', 'complete']) {
    state.status(status({copies: [copy('promoting')], operations: [operation(phase)]}));
    await state.controller.refresh(); assert.ok(state.state.ticket);
    assert.equal(state.state.message.includes('now the authoritative'), false);
  }
  state.status(status({copies: [copy('promoted')], operations: [operation('complete')],
    primary: {plugin_id: 'json', instance_id: NEW, authority_epoch: NEW}}));
  await state.controller.refresh();
  assert.equal(state.state.ticket, null); assert.match(state.state.message, /JSON is now the authoritative primary/);
  assert.equal(state.mutations().length, 1);
});

test('changed authority between review and confirmation prevents the promotion request', async () => {
  const state = harness(); await state.controller.start(); await reviewedPromotion(state);
  state.status(status({copies: [copy()], primary: {authority_epoch: NEW}}));
  await state.controller.promote();
  assert.equal(state.mutations().length, 0); assert.match(state.state.error, /changed/);
});

test('a lost promotion reply is reconciled without a second POST, including restart with no operation ticket', async () => {
  const state = harness(); await state.controller.start(); await reviewedPromotion(state);
  state.reply(new Error('private transport detail'));
  await state.controller.promote();
  assert.ok(state.state.uncertain); assert.match(state.state.error, /not confirmed/);
  await state.controller.promote(); assert.equal(state.mutations().length, 1);
  state.status(status({copies: [copy('promoted')], primary: {plugin_id: 'json', instance_id: NEW, authority_epoch: NEW}}));
  await state.controller.refresh();
  assert.equal(state.state.uncertain, null); assert.equal(state.state.error, '');
  assert.match(state.state.message, /now the authoritative primary/);
});

test('lost attach and disable responses are confirmed only by the corresponding persisted copy state', async () => {
  const state = harness(); await state.controller.start(); await preview(state);
  state.reply(new Error('lost attach')); await state.controller.attach();
  assert.ok(state.state.uncertain);
  state.status(status({copies: [copy()]})); await state.controller.refresh();
  assert.equal(state.state.uncertain, null);
  state.reply(new Error('lost disable')); await state.controller.disable(C);
  assert.ok(state.state.uncertain);
  state.status(status({copies: [copy('disabled')]})); await state.controller.refresh();
  assert.equal(state.state.uncertain, null); assert.match(state.state.message, /data is retained/);
});

test('malformed accepted replies remain unknown and need an explicit refreshed review', async () => {
  const state = harness(); await state.controller.start(); await preview(state);
  state.reply({}); await state.controller.attach();
  assert.ok(state.state.uncertain); assert.match(state.state.error, /No automatic retry/);
  await state.controller.preview('json'); assert.equal(state.mutations().length, 2);
  await state.controller.acknowledgeUnknown();
  assert.equal(state.state.uncertain, null); assert.match(state.state.message, /No completion was assumed/);
  assert.equal(state.mutations().length, 2);
});

test('a declined request reports no completion and does not repeat the mutation', async () => {
  const state = harness(); await state.controller.start(); await preview(state);
  const rejected = new Error('private error payload'); rejected.status = 400;
  state.reply(rejected); await state.controller.attach();
  assert.equal(state.state.uncertain, null); assert.equal(state.mutations().length, 2);
  assert.equal(JSON.stringify(state.snapshots).includes('private error payload'), false);
});

test('failed or recovered promotion status stays attributable and never claims success', async () => {
  for (const phase of ['failed', 'recovery_required']) {
    const state = harness(); await state.controller.start(); await reviewedPromotion(state);
    state.reply(() => {state.status(status({copies: [copy()], operations: [operation(phase)],
      primary: phase === 'recovery_required' ? {health: 'recovery_required', role: 'fenced', authoritative: false, admission: 'gated'} : {}})); return {id: T, state: 'queued'};});
    await state.controller.promote();
    assert.equal(state.state.ticket, null); assert.match(state.state.error, /recovery|did not complete/);
    assert.equal(state.state.message.includes('now the authoritative'), false);
  }
});

test('an unsaved editor blocks preview, retry, disable and promotion before any mutation', async () => {
  const state = harness({initial: status({copies: [copy()]}), flush: async () => false});
  await state.controller.start(); await preview(state); await state.controller.retry(C);
  await state.controller.disable(C); await state.controller.reviewPromotion(C);
  assert.equal(state.mutations().length, 0); assert.match(state.state.error, /Save or cancel/);
});

test('double clicks serialize changes and closing during a pending flush cannot submit', async () => {
  const pending = deferred(); const state = harness({flush: () => pending.promise});
  await state.controller.start();
  const one = state.controller.preview('json'); const two = state.controller.preview('json');
  await tick(); assert.equal(state.changes, 1);
  state.controller.stop(); pending.resolve(true); await Promise.all([one, two]);
  assert.equal(state.mutations().length, 0);
});

test('a delayed preview cannot reopen its review after close and reopen', async () => {
  const pending = deferred(); const state = harness(); await state.controller.start();
  state.reply(() => pending.promise); const work = state.controller.preview('json'); await tick();
  state.controller.stop(); await state.controller.start();
  pending.resolve({preview_id: C, destination: 'json', source_generation: 7, supported: true});
  await work; assert.equal(state.state.review, null);
});

test('older status replies cannot overwrite a reopened dialog or another budget', async () => {
  const old = deferred(); const state = harness({initial: () => old.promise});
  const first = state.controller.start(); await tick(); state.controller.stop();
  state.status(status({primary: {plugin_id: 'json'}})); await state.controller.start();
  old.resolve(status()); await first;
  assert.equal(state.state.status.primary.plugin_id, 'json');
  const next = deferred(); state.status(() => next.promise); const read = state.controller.refresh();
  state.current(false); next.resolve(status()); await read;
  assert.equal(state.state.status.primary.plugin_id, 'json');
});

test('polls never overlap, stop cancels scheduling, and read errors can be refreshed', async () => {
  const pending = deferred(); const state = harness({initial: () => pending.promise});
  const one = state.controller.start(); const two = state.controller.refresh();
  assert.equal(state.requests.length, 1); pending.resolve(status()); await Promise.all([one, two]);
  assert.equal(state.timers.size, 1);
  state.status(() => {throw new Error('private read detail');}); await state.controller.refresh();
  assert.equal(state.state.status, null); assert.match(state.state.error, /primary save is unaffected/);
  state.status(status()); await state.controller.refresh(); assert.equal(state.state.error, '');
  state.controller.stop(); assert.equal(state.timers.size, 0);
});

test('unsupported, gated, fenced, reattachment, and malformed status cannot issue unsafe changes', async () => {
  for (const value of [status({primary: {supported: false}}), status({primary: {admission: 'gated'}}),
    status({primary: {role: 'fenced'}}), status({primary: {health: 'recovery_required'}}), {},
    status({errors: {configuration: {code: 'invalid_copy_configuration'}}})]) {
    const state = harness({initial: value}); await state.controller.start(); await preview(state);
    assert.equal(state.mutations().length, 0);
  }
  const state = harness({initial: status({copies: [copy('reattach_required')]})});
  await state.controller.start(); await state.controller.retry(C); await state.controller.reviewPromotion(C);
  assert.equal(state.mutations().length, 0);
});

test('retry remains separate from primary success and disable keeps saved data', async () => {
  const state = harness({initial: status({copies: [copy('failed')]})}); await state.controller.start();
  state.reply({id: C, state: 'pending'}); await state.controller.retry(C);
  assert.match(state.state.message, /primary is saved/); assert.equal(state.state.status.primary.authoritative, true);
  state.reply({id: C, state: 'disabled'}); state.status(status({copies: [copy('disabled')]}));
  await state.controller.disable(C); assert.match(state.state.message, /data is retained/);
});

class Node {
  constructor(tag = 'div', page = null) {this.tagName = tag.toUpperCase(); this.page = page; this.dataset = {}; this.children = []; this.listeners = {}; this.attributes = {}; this.disabled = false; this._text = ''; this._value = null;}
  append(...children) {for (const child of children) {child.parent = this; this.children.push(child);}}
  replaceChildren(...children) {this.children = []; this._text = ''; this.append(...children);}
  set textContent(value) {this._text = String(value); this.children = [];}
  get textContent() {return this._text + this.children.map(child => child.textContent).join('');}
  get options() {return this.children.filter(child => child.tagName === 'OPTION');}
  get value() {return this._value ?? this.options[0]?.value ?? '';}
  set value(value) {this._value = value;}
  addEventListener(type, callback) {(this.listeners[type] ||= []).push(callback);}
  async emit(type, detail = {}) {const event = {preventDefault() {this.defaultPrevented = true;}, ...detail}; for (const listener of this.listeners[type] || []) await listener(event); return event;}
  setAttribute(name, value) {this.attributes[name] = String(value);}
  contains(node) {return node === this || this.children.some(child => child.contains(node));}
  querySelectorAll(selector) {
    const match = node => selector === '[data-storage-control]' && node.dataset.storageControl !== undefined;
    return this.children.flatMap(child => [...(match(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  focus() {this.page.activeElement = this;}
}
function domHarness({workspace = 'personal', initial = status(), supplied = true} = {}) {
  const page = new Node('document'); page.activeElement = null;
  page.createElement = tag => new Node(tag, page);
  const panel = page.createElement('div'), message = page.createElement('p'), error = page.createElement('p'), refresh = page.createElement('button');
  const dialog = page.createElement('dialog'); dialog.dataset.workspace = workspace; dialog.open = true;
  dialog.querySelector = selector => ({'[data-storage-copy-panel]': panel, '[data-storage-copy-message]': message,
    '[data-storage-copy-error]': error, '[data-storage-copy-refresh]': refresh})[selector];
  const opener = page.createElement('button'); page.getElementById = () => dialog;
  page.querySelectorAll = () => [opener];
  const requests = [], scheduled = new Set(); let latest = initial, prefix = '/budgets/fictional/api';
  const request = async (path, body, expected) => {requests.push({path, body, expected}); return latest;};
  const context = runtime({fetch: async (path, options) => {requests.push({path, options}); return {ok: true, status: 200, json: async () => latest};}});
  const controller = context.init({document: page, context: () => ({apiPrefix: prefix, workspace}), beforeChange: async () => true,
    ...(supplied ? {request} : {}), schedule: callback => {scheduled.add(callback); return callback;}, cancel: timer => scheduled.delete(timer)});
  return {controller, page, panel, message, error, refresh, dialog, opener, requests, scheduled,
    status: value => {latest = value;}, prefix: value => {prefix = value;}};
}

test('DOM status uses fixed text for hostile metadata and never interprets server HTML or paths', async () => {
  const state = domHarness({initial: status({copies: [{...copy('failed'), destination: '<script>/private/model</script>',
    error: {code: '<img src=x onerror=private>'}}], errors: {scheduler: {code: 'copy_scheduling_failed', path: '/private/key'}}})});
  await state.opener.emit('click');
  assert.match(state.panel.textContent, /Other storage copy/);
  assert.match(state.panel.textContent, /primary is saved|primary save stays successful/);
  assert.equal(state.panel.textContent.includes('<script>'), false);
  assert.equal(state.panel.textContent.includes('/private/'), false);
  assert.equal(state.panel.children.some(node => node.tagName === 'SCRIPT'), false);
});

test('unchanged polls retain the focused destination and review controls', async () => {
  const state = domHarness(); await state.opener.emit('click');
  const select = state.panel.querySelectorAll('[data-storage-control]').find(node => node.tagName === 'SELECT');
  select.focus(); const original = [...state.panel.children]; await state.controller.refresh();
  assert.equal(state.page.activeElement, select); assert.deepEqual(state.panel.children, original);
  state.status(status({primary: {generation: 8}})); await state.controller.refresh();
  assert.equal(state.page.activeElement.dataset.storageControl, 'destination');
  assert.equal(state.page.activeElement.value, 'json');
});

test('unsupported backends and Demo show no changing controls or provider calls', async () => {
  const demo = domHarness({workspace: 'demo'}); assert.equal(demo.controller, null); assert.equal(demo.requests.length, 0);
  const state = domHarness({initial: status({primary: {supported: false, plugin_id: 'client'}})});
  await state.opener.emit('click'); assert.match(state.panel.textContent, /does not support/);
  assert.equal(state.panel.querySelectorAll('[data-storage-control]').length, 0);
});

test('request paths are captured per budget and closing stops only browser polling', async () => {
  const state = domHarness({supplied: false}); await state.opener.emit('click');
  assert.equal(state.requests[0].path, '/budgets/fictional/api/storage/copies');
  state.prefix('/budgets/other/api'); await state.controller.refresh(); assert.equal(state.requests.length, 1);
  await state.dialog.emit('close'); assert.equal(state.scheduled.size, 0);
  assert.equal(state.requests.filter(row => row.options?.method === 'POST').length, 0);
});

test('opening the copy dialog blocks ordinary navigation but permits its own guarded save event', async () => {
  const state = domHarness(); await state.opener.emit('click');
  assert.equal((await state.page.emit('whab:before-navigate')).defaultPrevented, true);
  assert.equal((await state.page.emit('whab:before-navigate', {detail: {source: 'storage-copies'}})).defaultPrevented, undefined);
});

test('app integration uses the real save guard and honors existing navigation vetoes', async () => {
  const app = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
  const hook = app.slice(app.lastIndexOf('initStorageCopies({beforeChange:'), app.indexOf('\nconst uiViews ='));
  const order = []; let options, allowed = true;
  const context = {initStorageCopies: value => {options = value;},
    document: {dispatchEvent: event => {order.push(event.type); assert.equal(event.detail.source, 'storage-copies'); return allowed;}},
    CustomEvent: class {constructor(type, options) {this.type = type; Object.assign(this, options);}},
    requireSavedEdits: async () => {order.push('flush budget and transaction edits');},
  };
  vm.runInNewContext(hook, context);
  await options.beforeChange();
  assert.deepEqual(order, ['whab:before-navigate', 'flush budget and transaction edits']);
  allowed = false; order.length = 0;
  assert.equal(await options.beforeChange(), false);
  assert.deepEqual(order, ['whab:before-navigate']);
});

test('a new explicit review receives focus on its explanatory section, not the confirm action', async () => {
  const state = domHarness(); await state.opener.emit('click');
  state.status({preview_id: C, destination: 'json', source_generation: 7, supported: true});
  await state.controller.preview('json');
  assert.equal(state.page.activeElement.className, 'storage-copy-review');
  assert.equal(state.page.activeElement.tagName, 'SECTION');
  assert.equal(state.page.activeElement.tabIndex, -1);
  assert.match(state.page.activeElement.textContent, /new empty destination/);
});

test('a rejected change stays visible across successful status polls until another user action', async () => {
  const state = harness(); await state.controller.start(); await preview(state);
  const rejected = new Error('declined'); rejected.status = 400;
  state.reply(rejected); await state.controller.attach();
  assert.match(state.state.error, /host declined/);
  await state.controller.refresh(); assert.match(state.state.error, /host declined/);
});
