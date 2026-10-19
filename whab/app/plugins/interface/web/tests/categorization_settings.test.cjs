const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');

class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; this.hidden = false; this.disabled = false; this.checked = false;}
  append(...items) {this.children.push(...items);}
  replaceChildren(...items) {this.children = items;}
  addEventListener(event, callback) {(this.listeners[event] ||= []).push(callback);}
  removeEventListener() {}
  setAttribute(name, value) {this[name] = value;}
  querySelectorAll(selector) {return this.children.flatMap(child => [...(selector.split(',').includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)]);}
  get elements() {return Object.fromEntries(this.querySelectorAll('input').map(input => [input.name, input]));}
  emit(event) {for (const callback of this.listeners[event] || []) callback({preventDefault() {}});}
}
const defaults = {trees: 300, depth: 20, leaf: 2, vocabulary: 1500, threshold: .8};
async function fixture({pause = false, readOnly = false, fail = false} = {}) {
  const requests = [];
  const frames = [];
  const status = new Element('p');
  const error = new Element('p');
  const content = new Element('section');
  const dialog = new Element('dialog');
  dialog.dataset = {pluginId: 'random-forest-categorization', pluginReadOnly: String(readOnly)};
  dialog.open = true;
  dialog.querySelector = selector => selector.includes('error') ? error : status;
  const api = async (_url, body) => {
    const payload = body.payload; requests.push(payload);
    if (payload.action === 'status') return {defaults, models: [], eligible: 80};
    if (fail) throw new Error('Training could not start');
    if (payload.action === 'train') return {session: 'view-session', stage: 'Preparing reviewed transactions', progress: 0};
    if (payload.action === 'step') return {complete: true, stage: 'Training complete', progress: 100};
    return {ok: true};
  };
  globalThis.__categorizationTest = {api, apiUrl: value => value, getContext: () => ({workspace: 'personal'})};
  globalThis.document = {createElement: tag => new Element(tag)};
  globalThis.window = new Element('window');
  globalThis.requestAnimationFrame = callback => pause ? frames.push(callback) : queueMicrotask(callback);
  const source = readFileSync(resolve(__dirname, '../src/whab_plugin_web/static/categorization-settings.js'), 'utf8')
    .replace(/import .* from '.\/client.js';/, 'const {api, apiUrl, getContext} = globalThis.__categorizationTest;');
  const module = await import(`data:text/javascript;base64,${Buffer.from(source + `\n// ${Math.random()}`).toString('base64')}`);
  await module.initCategorizationSettings(dialog, content);
  return {requests, frames, status, error, content, dialog};
}
const settle = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));};

test('training sends bounded parameters, advances only from the view, and reports completion', async () => {
  const ui = await fixture();
  const form = ui.content.children.find(child => child.tag === 'form');
  form.elements.name.value = 'Reviewed-only model';
  form.emit('submit');
  await settle();
  const train = ui.requests.find(request => request.action === 'train');
  assert.equal(train.name, 'Reviewed-only model');
  assert.equal(train.parameters.trees, 300);
  assert.equal(train.parameters.balanced, false);
  assert.deepEqual(ui.requests.filter(request => request.action === 'step'), [{action: 'step', pluginId: 'random-forest-categorization', session: 'view-session'}]);
  assert.equal(ui.status.textContent, 'Training complete');
  assert.equal(ui.requests.some(request => request.action === 'cancel'), false);
});

test('leaving settings cancels the session and never advances the next block of trees', async () => {
  const ui = await fixture({pause: true});
  ui.content.children.find(child => child.tag === 'form').emit('submit');
  await settle();
  assert.equal(ui.frames.length, 1);
  ui.dialog.open = false;
  ui.dialog.emit('close');
  ui.frames.shift()();
  await settle();
  assert.equal(ui.requests.filter(request => request.action === 'cancel').length, 1);
  assert.equal(ui.requests.some(request => request.action === 'step'), false);
  assert.equal(ui.requests.some(request => request.action === 'select'), false);
});

test('read-only settings cannot start training and failures remain visible', async () => {
  const locked = await fixture({readOnly: true});
  locked.content.children.find(child => child.tag === 'form').emit('submit');
  await settle();
  assert.equal(locked.requests.length, 1);
  assert(locked.content.querySelectorAll('input,button').every(control => control.disabled));
  const failed = await fixture({fail: true});
  failed.content.children.find(child => child.tag === 'form').emit('submit');
  await settle();
  assert.equal(failed.error.hidden, false);
  assert.equal(failed.error.textContent, 'Training could not start');
  assert.equal(failed.requests.some(request => request.action === 'step'), false);
});
