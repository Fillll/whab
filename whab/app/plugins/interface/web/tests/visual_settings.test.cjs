const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const navigationSource = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/settings-navigation.js'), 'utf8').replaceAll('export function ', 'function ');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/visual-settings.js'), 'utf8');
const P = '11111111-1111-4111-8111-111111111111';
const T = '22222222-2222-4222-8222-222222222222';
const G = 'a'.repeat(64), NEXT = 'b'.repeat(64);
const plain = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
function deferred() {let resolve; const promise = new Promise(yes => {resolve = yes;}); return {promise, resolve};}
const ready = {generation: 1, health: 'ready', admission: 'open'};
function visual({operations = [], pluginId = 'web', generation = G, runtime = ready} = {}) {
  return {active: {plugin_id: pluginId, resource_generation: generation}, configured: {plugin_id: pluginId, resource_generation: generation},
    runtime, operations, selection_revision: G};
}
const operation = (state, pluginId = 'other', error = null) => ({id: T, preview_id: P, state, plugin_id: pluginId, error});
function harness({beforeChange = async () => true, beforeCommit = async () => true} = {}) {
  const context = {setTimeout, clearTimeout};
  vm.runInNewContext(navigationSource + source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function ') + '\nthis.create = createVisualSettings;', context);
  let current = true, visualStatus = visual(), optionalStatus = {runtime: ready, operations: []}, reply = null, timer = null, state;
  const calls = [];
  const controller = context.create({beforeChange, beforeCommit, isCurrent: () => current,
    onState: value => {state = plain(value);}, schedule: callback => {timer = callback; return callback;}, cancel: () => {timer = null;},
    async request(url, body, expected) {
      calls.push({url, body: plain(body), expected});
      if (body === undefined) return plain(url === '/ui/status' ? visualStatus : optionalStatus);
      if (reply instanceof Error) throw reply;
      if (typeof reply === 'function') return reply(url, body);
      return plain(reply);
    }});
  return {controller, calls, get state() {return state;}, get timer() {return timer;},
    mutations: () => calls.filter(row => row.body !== undefined), current: value => {current = value;},
    visual: value => {visualStatus = value;}, optional: value => {optionalStatus = value;}, reply: value => {reply = value;}};
}
async function preview(h, pluginId = 'other') {
  h.reply({preview_id: P, plugin_id: pluginId, selection_revision: G, requires_saved_views: true, code: 'installed'});
  await h.controller.preview(pluginId);
}

test('visual preview never commits; edited/dismissed review cannot be applied', async () => {
  const h = harness(); await h.controller.start(); await preview(h);
  assert.equal(h.state.review.pluginId, 'other');
  assert.deepEqual(h.mutations().map(row => row.url), ['/ui/preview']);
  h.controller.invalidate(); await h.controller.apply(); assert.equal(h.mutations().length, 1);
  assert.deepEqual(h.mutations()[0].body, {pluginId: 'other', useInstalled: true});
});

test('202 is pending through queued/running and requires matching active durable visual generation', async () => {
  const h = harness(); await h.controller.start(); await preview(h);
  h.reply({id: T, accepted: true}); h.visual(visual({operations: [operation('queued')]}));
  await h.controller.apply(); assert.ok(h.state.ticket); assert.match(h.state.message, /queued/);
  h.visual(visual({operations: [operation('running')]})); await h.controller.refresh(); assert.ok(h.state.ticket);
  h.visual(visual({operations: [operation('complete')]})); await h.controller.refresh(); assert.ok(h.state.ticket);
  h.visual(visual({pluginId: 'other', generation: NEXT, operations: [operation('complete')]}));
  await h.controller.refresh(); assert.equal(h.state.ticket, null); assert.match(h.state.message, /host confirmed/);
  assert.equal(h.state.completed.id, T); assert.equal(h.mutations().length, 2);
});

test('headless requires an explicit preview and sends no plugin code selection', async () => {
  const h = harness(); await h.controller.start(); await preview(h, null);
  assert.equal(h.state.review.pluginId, null);
  assert.deepEqual(h.mutations()[0].body, {pluginId: null, useInstalled: false});
  h.reply({id: T, accepted: true}); h.visual(visual({operations: [operation('queued', null)]}));
  await h.controller.apply(); assert.equal(h.state.completed, null);
});

test('lost reply discovers queued host operation by status without another commit', async () => {
  const h = harness(); await h.controller.start(); await preview(h);
  h.reply(new Error('fictional transport loss')); h.visual(visual({operations: [operation('running')]}));
  await h.controller.apply(); assert.equal(h.state.ticket.id, T); assert.equal(h.state.unknown, false);
  await h.controller.apply(); assert.equal(h.mutations().length, 2);
  h.visual(visual({pluginId: 'other', generation: NEXT, operations: [operation('complete')]})); await h.controller.refresh();
  assert.equal(h.state.ticket, null); assert.equal(h.mutations().length, 2);
});

test('unknown result blocks mutations until explicit fresh-status review; never assumes completion', async () => {
  const h = harness(); await h.controller.start(); await preview(h); h.reply(new Error('fictional lost reply'));
  await h.controller.apply(); assert.equal(h.state.unknown, true); assert.match(h.state.error, /unconfirmed/);
  await preview(h, 'third'); await h.controller.apply(); assert.equal(h.mutations().length, 2);
  await h.controller.acknowledgeUnknown(); assert.equal(h.state.unknown, false); assert.equal(h.state.completed, null);
  assert.match(h.state.message, /No completion was assumed/);
  assert.equal(h.mutations().length, 2);
});

test('refused preview and failed operation use authored diagnostics without private error text', async () => {
  const h = harness(); await h.controller.start();
  h.reply(Object.assign(new Error('fictional private plugin exception'), {status: 409, code: 'ui_transition_required'}));
  await h.controller.preview('legacy'); assert.match(h.state.error, /requires restart/); assert.equal(h.state.review, null);
  await preview(h); h.reply({id: T, accepted: true});
  h.visual(visual({operations: [operation('failed', 'other', {code: 'continuation_busy', message: 'fictional private payload'})]}));
  await h.controller.apply(); assert.equal(h.state.ticket, null); assert.match(h.state.error, /Finish or cancel/);
  assert.equal(JSON.stringify(h.state).includes('fictional private'), false);
});

test('pending work, recovery, failed save guard and malformed host metadata block activation', async () => {
  for (const status of [visual({runtime: {...ready, health: 'recovery_required'}}), visual({operations: [operation('running')]}), {bad: true}]) {
    const h = harness(); h.visual(status); await h.controller.start(); await preview(h); await h.controller.apply();
    assert.equal(h.mutations().length, 0);
  }
  const dirty = harness({beforeChange: async () => false}); await dirty.controller.start(); await preview(dirty);
  assert.equal(dirty.mutations().length, 0); assert.match(dirty.state.error, /unfinished edits/);
  const lateDirty = harness({beforeCommit: async () => false}); await lateDirty.controller.start(); await preview(lateDirty);
  await lateDirty.controller.apply(); assert.equal(lateDirty.mutations().length, 1); assert.match(lateDirty.state.error, /unfinished edits/);
});

test('closing and reopening polls retained operation; other budget and late preview are ignored', async () => {
  const h = harness(); await h.controller.start(); await preview(h); h.reply({id: T, accepted: true}); await h.controller.apply();
  h.controller.stop(); assert.equal(h.timer, null); const calls = h.calls.length; await h.controller.refresh(); assert.equal(h.calls.length, calls);
  h.visual(visual({operations: [operation('failed')]})); await h.controller.start(); assert.equal(h.state.ticket, null);
  h.current(false); await h.controller.preview('third'); assert.equal(h.mutations().length, 2);
  const late = harness(); await late.controller.start(); const held = deferred(); late.reply(() => held.promise);
  const waiting = late.controller.preview('other'); await new Promise(resolve => setImmediate(resolve)); late.controller.stop();
  held.resolve({preview_id: P, plugin_id: 'other', selection_revision: G, requires_saved_views: true, code: 'installed'}); await waiting;
  await late.controller.start(); assert.equal(late.state.review, null); assert.equal(late.state.busy, false);
});

test('optional plugins use separate reviewed live route and host terminal operation', async () => {
  const h = harness(); await h.controller.start(); await h.controller.reviewOptional(['example']);
  assert.equal(h.mutations().length, 0); assert.equal(h.state.review.kind, 'optional');
  h.reply({id: T, accepted: true}); h.optional({runtime: ready, operations: [operation('queued')]}); await h.controller.apply();
  assert.deepEqual(h.mutations()[0], {url: '/plugins/management/transition', body: {plugins: ['example']}, expected: 202});
  assert.equal(h.state.completed, null);
  h.optional({runtime: ready, operations: [operation('complete')]}); await h.controller.refresh();
  assert.equal(h.state.completed.kind, 'optional');
});

test('Demo initializer never requests metadata or mutates', () => {
  const context = {getContext: () => {throw Error('Demo must not read personal controls');}};
  vm.runInNewContext(navigationSource + source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function ') + '\nthis.init = initVisualSettings;', context);
  const page = {getElementById: () => ({querySelector: () => ({dataset: {workspace: 'demo'}})})};
  assert.equal(context.init({page}), null);
});

test('explicit cleanup attempts use counts, never resubmit change, and preserve recovery', async () => {
  const h = harness();
  const failed = {...ready, health: 'degraded', cleanup_pending: 1, cleanup_failed: 1};
  h.visual(visual({runtime: failed})); h.optional({runtime: failed, operations: []}); await h.controller.start();
  await preview(h); assert.equal(h.mutations().length, 0);
  h.reply({}); await h.controller.retryCleanup();
  assert.deepEqual(h.mutations()[0], {url: '/plugins/runtime/cleanup', body: {}, expected: 200});
  assert.equal(h.state.visual.runtime.cleanup_failed, 1); assert.equal(h.controller.canChange(), false);
  const cleared = {...failed, cleanup_pending: 0, cleanup_failed: 0};
  h.visual(visual({runtime: cleared})); h.optional({runtime: cleared, operations: []}); await h.controller.refresh();
  assert.equal(h.controller.canChange(), true);
  await h.controller.retryCleanup(); assert.equal(h.mutations().length, 1);
  h.visual(visual({runtime: {...cleared, health: 'recovery_required'}})); await h.controller.refresh();
  assert.equal(h.controller.canChange(), false); assert.equal(h.mutations().length, 1);
});

test('visual Apply closes the single Settings window before host submission so browser ACK can proceed', async () => {
  class Node {
    constructor() {this.dataset = {}; this.events = {}; this.open = true; this.value = ''; this.hidden = false;}
    addEventListener(type, handler) {(this.events[type] ||= []).push(handler);}
    fire(type, event = {}) {for (const handler of this.events[type] || []) handler(event);}
    setAttribute() {}
    focus() {this.focused = true;}
    close() {this.open = false; this.fire('close');}
  }
  const fields = Object.fromEntries(['form', 'mode', 'target', 'apply', 'current', 'runtime', 'message', 'error', 'refresh', 'dismiss', 'unknown', 'cleanup', 'review'].map(key => [key, new Node()]));
  const previewButton = new Node(); fields.form.querySelector = () => previewButton; fields.mode.value = 'installed';
  const pane = new Node(); pane.dataset.workspace = 'personal';
  pane.querySelector = selector => fields[selector.match(/data-visual-([^\]]+)/)[1]];
  const dialog = new Node(), settings = new Node(), selection = new Node();
  delete dialog.open; dialog.dataset.settingsPage = ''; dialog.hidden = true;
  dialog.closest = () => settings;
  settings.addEventListener('close', () => {dialog.hidden = true; dialog.fire('close');});
  dialog.querySelector = selector => selector === '[data-visual-settings]' ? pane : selection;
  dialog.querySelectorAll = () => [];
  const page = new Node(); page.getElementById = id => id === 'settings-dialog' ? settings : dialog;
  page.querySelectorAll = selector => selector === '#settings-dialog[open]' && settings.open ? [settings] : [];
  const context = {getContext: () => ({apiPrefix: '/budgets/fictional/api'}), notify() {}, setTimeout, clearTimeout};
  vm.runInNewContext(navigationSource + source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function ') + '\nthis.init = initVisualSettings;', context);
  const calls = [], timers = [];
  let committed = false;
  const controller = context.init({page, beforeChange: async () => true, beforeCommit: async () => true,
    schedule: handler => {timers.push(handler); return handler;}, cancel() {},
    async request(url, body) {
      calls.push(url);
      if (body === undefined) return url === '/ui/status' ? visual({operations: committed ? [operation('queued')] : []}) : {runtime: ready, operations: []};
      if (url === '/ui/preview') return {preview_id: P, plugin_id: 'other', selection_revision: G, requires_saved_views: true, code: 'installed'};
      assert.equal(url, '/ui/commit'); assert.equal(dialog.hidden, true); assert.equal(settings.open, false);
      committed = true; return {id: T, accepted: true};
    }});
  dialog.hidden = false; const opening = controller.start(); await opening; await controller.preview('other');
  assert.equal(fields.apply.disabled, false); assert.equal(fields.review.focused, true);
  await controller.apply();
  assert.equal(fields.apply.hidden, true); assert.match(fields.message.textContent, /queued|Waiting/);
  assert.equal(calls.filter(url => url === '/ui/commit').length, 1);
  const before = calls.length; await controller.refresh(); assert.ok(calls.length > before); // Accepted work still observed after own close.
  controller.stop();
});


test('lost visual reply cannot adopt another client’s operation for the same plugin', async () => {
  const h = harness(); await h.controller.start(); await preview(h);
  h.reply(new Error('fictional transport loss'));
  h.visual(visual({pluginId: 'other', generation: NEXT, operations: [{...operation('complete'), preview_id: T}]}));
  await h.controller.apply(); assert.equal(h.state.unknown, true); assert.equal(h.state.completed, null);
  assert.equal(h.state.ticket.id, null); assert.equal(h.mutations().length, 2);
  await h.controller.acknowledgeUnknown(); assert.equal(h.state.completed, null);
  assert.match(h.state.message, /No completion was assumed/);
});

test('lost optional reply never adopts another client’s completed selection', async () => {
  const h = harness(); await h.controller.start(); await h.controller.reviewOptional(['example']);
  h.reply(new Error('fictional transport loss')); h.optional({runtime: ready, operations: [operation('complete')]});
  await h.controller.apply(); assert.equal(h.state.unknown, true); assert.equal(h.state.completed, null);
  assert.equal(h.state.ticket.id, null); await h.controller.apply(); assert.equal(h.mutations().length, 1);
  await h.controller.acknowledgeUnknown(); assert.equal(h.state.completed, null);
  assert.match(h.state.message, /No completion was assumed/);
});

test('changing target during an outstanding preview invalidates its late review', async () => {
  const h = harness(); await h.controller.start(); const held = deferred(); h.reply(() => held.promise);
  const waiting = h.controller.preview('other'); await new Promise(resolve => setImmediate(resolve));
  h.controller.invalidate(); held.resolve({preview_id: P, plugin_id: 'other', selection_revision: G, requires_saved_views: true, code: 'installed'});
  await waiting; assert.equal(h.state.review, null); assert.equal(h.state.busy, false);
  await h.controller.apply(); assert.equal(h.mutations().length, 1);
});

test('app guard preserves optional drafts and other dialogs before visual submission', async () => {
  const app = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
  const hook = app.slice(app.lastIndexOf('initVisualSettings({'), app.lastIndexOf('initStorageCopies({beforeChange:'));
  const events = []; let options, dirty = false, allowed = true, activePage = 'plugins-dialog', dialogs = [{id: 'settings-dialog'}];
  const context = {initVisualSettings: value => {options = value;}, optionalSelectionDirty: () => dirty,
    document: {dispatchEvent(event) {events.push(event.detail.source); return allowed;}, querySelectorAll: () => dialogs, querySelector: () => ({id: activePage})},
    CustomEvent: class {constructor(type, options) {this.type = type; Object.assign(this, options);}},
    requireSavedEdits: async () => {events.push('flush');}, location: {reload() {events.push('reload');}},
  };
  vm.runInNewContext(hook, context);
  assert.equal(await options.beforeCommit('visual'), true); assert.deepEqual(events, ['plugin-settings', 'flush']);
  events.length = 0; dirty = true; assert.equal(await options.beforeCommit('visual'), false); assert.deepEqual(events, []);
  assert.equal(await options.beforeCommit('optional'), true); assert.deepEqual(events, ['plugin-settings', 'flush']);
  events.length = 0; dirty = false; activePage = 'plugin-options-web';
  assert.equal(await options.beforeCommit('visual'), true); assert.deepEqual(events, ['plugin-settings', 'flush']);
  events.length = 0; activePage = 'plugin-options-bank-logos';
  assert.equal(await options.beforeCommit('visual'), false); assert.deepEqual(events, ['plugin-settings']);
  events.length = 0; activePage = 'plugins-dialog'; dialogs.push({id: 'transaction-dialog'});
  assert.equal(await options.beforeCommit('visual'), false); assert.deepEqual(events, ['plugin-settings']);
  events.length = 0; allowed = false; assert.equal(await options.beforeChange(), false); assert.deepEqual(events, ['plugin-settings']);
  events.length = 0; options.onOptionalComplete(); assert.deepEqual(events, ['reload']);
});
