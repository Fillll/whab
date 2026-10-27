const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; this.checked = false; this.disabled = false; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(event, handler) { (this.listeners[event] ||= []).push(handler); }
  setAttribute(name, value) { this[name] = value; }
  querySelectorAll(selector) { return this.children.flatMap(child => [...(selector.split(',').includes(child.tag) ? [child] : []), ...child.querySelectorAll(selector)]); }
  emit(event) { for (const handler of this.listeners[event] || []) handler({preventDefault() {}}); }
}
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };
async function fixture({readOnly = false, fail = '', saved = false} = {}) {
  const content = new Element('section'), status = new Element('p'), error = new Element('p'), dialog = new Element('dialog');
  dialog.dataset.pluginReadOnly = String(readOnly); dialog.open = true;
  dialog.querySelector = selector => selector.includes('error') ? error : status;
  const requests = [];
  const categories = [{id: 'food', name: 'Food', currency: 'USD'}];
  const api = async (path, body) => {
    const payload = body.payload || body; requests.push({path, ...payload});
    if (payload.action === fail) throw new Error('Fictional provider failure');
    if (path.includes('plugin-settings') && payload.action === 'status') return {
      categories, providers: [{id: 'openai', name: 'OpenAI API key', connected: false}, {id: 'chatgpt', name: 'OpenAI subscription', connected: false}]
    };
    if (payload.action === 'status') return {models: saved ? [{id: 'profile', name: 'Saved profile', parameters: {provider: 'openai', model: 'test-model', threshold: .8, automatic: false}}] : [], active: 'profile'};
    if (payload.action === 'train') return {session: 'session'};
    if (payload.action === 'step') return {complete: true};
    if (payload.action === 'models') return {models: [{id: 'test-model', name: 'Test model'}]};
    if (payload.action === 'connect') return {url: 'https://auth.openai.com/authorize?state=fictional'};
    if (payload.action === 'auth-status') return {status: 'connected'};
    if (payload.action === 'preview') return {suggested: 2};
    return {message: 'Disconnected.', ok: true};
  };
  globalThis.__llmSettings = {api, getContext: () => ({workspace: 'personal'}), isSettingsPageOpen: value => value.open};
  globalThis.document = {createElement: tag => new Element(tag)};
  const source = readFileSync(resolve(__dirname, '../src/whab_plugin_web/static/llm-categorization-settings.js'), 'utf8')
    .replace(/import .* from '.\/client.js';/, 'const {api, getContext} = globalThis.__llmSettings;')
    .replace(/import .* from '.\/settings-navigation.js';/, 'const {isSettingsPageOpen} = globalThis.__llmSettings;');
  const module = await import(`data:text/javascript;base64,${Buffer.from(source + `\n// ${Math.random()}`).toString('base64')}`);
  await module.initLlmSettings(dialog, content);
  const controls = () => content.querySelectorAll('input,select,button');
  return {content, dialog, status, error, requests, controls, categories,
    field: name => controls().find(node => node.name === name),
    button: label => controls().find(node => node.textContent === label)};
}

test('LLM profile explicitly opts into sending, preserves automatic-off default and sends no key to ledger', async () => {
  const ui = await fixture();
  assert.equal(ui.field('automatic').checked, false);
  assert.equal(ui.field('consent').checked, false);
  ui.field('api_key').value = 'fictional-secret';
  ui.button('Save API key').emit('click'); await settle();
  assert.equal(ui.field('api_key').value, '');
  const keyRequest = ui.requests.find(row => row.action === 'save-key');
  assert.match(keyRequest.path, /plugin-settings/);
  assert.equal(keyRequest.api_key, 'fictional-secret');
  ui.field('model').value = 'test-model'; ui.field('consent').checked = true;
  ui.content.children.find(node => node.tag === 'form').emit('submit'); await settle();
  const train = ui.requests.find(row => row.action === 'train');
  assert.equal(train.parameters.automatic, false);
  assert.equal(train.parameters.consent, true);
  assert.deepEqual(train.parameters.categories, ui.categories);
  assert.equal(train.parameters.model, 'test-model');
  assert.equal(JSON.stringify(train).includes('fictional-secret'), false);
  assert.match(ui.status.textContent, /Profile saved/);
  assert.equal(ui.requests.some(row => row.action === 'select' || row.action === 'preview'), false);
});

test('provider changes clear old models, subscription uses explicit sign-in and model choices stay editable', async () => {
  const ui = await fixture();
  ui.button('Load available models').emit('click'); await settle();
  assert.match(ui.status.textContent, /1 models loaded/);
  const provider = ui.field('provider'); provider.value = 'chatgpt'; provider.emit('change');
  assert.equal(ui.field('model').value, '');
  assert.equal(ui.field('api_key'), undefined);
  ui.button('Continue with ChatGPT').emit('click'); await settle();
  const links = ui.content.querySelectorAll('a');
  assert(links.some(link => link.href.startsWith('https://auth.openai.com/')));
  ui.button('Check sign-in').emit('click'); await settle();
  assert.match(ui.status.textContent, /ChatGPT connected/);
  ui.button('Disconnect provider').emit('click'); await settle();
  assert.equal(ui.status.textContent, 'Disconnected.');
  ui.dialog.emit('close'); await settle();
  assert.equal(ui.requests.at(-1).action, 'cancel-sign-in');
});

test('saved profiles and manual suggestions use existing categorization commands', async () => {
  const ui = await fixture({saved: true});
  for (const label of ['Rename', 'Use profile', 'Delete', 'Suggest for existing transactions']) {
    ui.button(label).emit('click'); await settle();
  }
  for (const action of ['rename', 'select', 'delete', 'preview']) assert(ui.requests.some(row => row.action === action));
  assert.match(ui.status.textContent, /2 category suggestions/);
});

test('failed requests are visible, failed sessions cancel and read-only forms cannot send', async () => {
  const failed = await fixture({fail: 'step'});
  failed.content.children.find(node => node.tag === 'form').emit('submit'); await settle();
  assert.equal(failed.error.textContent, 'Fictional provider failure');
  assert.equal(failed.error.hidden, false);
  assert(failed.requests.some(row => row.action === 'cancel'));
  const locked = await fixture({readOnly: true});
  assert(locked.controls().every(node => node.disabled));
  locked.button('Save API key').emit('click'); await settle();
  assert.equal(locked.requests.length, 2);
});
