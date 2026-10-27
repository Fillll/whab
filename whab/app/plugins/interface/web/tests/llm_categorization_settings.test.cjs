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
  reportValidity() { return this.valid !== false; }
  emit(event) { for (const handler of this.listeners[event] || []) handler({preventDefault() {}}); }
}
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };
async function fixture({readOnly = false, fail = '', saved = false, legacy = false} = {}) {
  const content = new Element('section'), status = new Element('p'), error = new Element('p'), dialog = new Element('dialog');
  dialog.dataset.pluginReadOnly = String(readOnly); dialog.open = true;
  dialog.querySelector = selector => selector.includes('error') ? error : status;
  const requests = [];
  const categories = [{id: 'food', name: 'Food', currency: 'USD'}];
  const api = async (path, body) => {
    const payload = body.payload || body; requests.push({path, ...payload});
    if (payload.action === fail) throw new Error('Fictional provider failure');
    if (path.includes('plugin-settings') && payload.action === 'status') return {
      categories, promptRules: 'Fixed category and output rules.', efforts: legacy ? undefined : {openai: ['default','low','medium','high'], chatgpt: ['default','low','medium','high']}, providers: [{id: 'openai', name: 'OpenAI API key', connected: false}, {id: 'chatgpt', name: 'OpenAI subscription', connected: false}]
    };
    if (payload.action === 'status') return {defaults: {provider:'openai', prompt:'Suggested prompt.', effort:'default', examples:20, threshold:.8}, models: saved ? [{id: 'profile', name: 'Saved profile', parameters: {provider: 'chatgpt', model: 'test-model', prompt:'Saved prompt.', effort:'medium', threshold: .9, examples:0, automatic: false, consent:true}}] : [], active: 'profile'};
    if (payload.action === 'train') return {session: 'session'};
    if (payload.action === 'step') return {complete: true, model:{id:'new-profile'}};
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
  const controls = () => content.querySelectorAll('input,select,textarea,button');
  return {content, dialog, status, error, requests, controls, categories,
    field: name => controls().find(node => node.name === name),
    button: label => controls().find(node => node.textContent === label)};
}

test('new profile saves the visible prompt and effort, activates it and keeps keys outside ledger commands', async () => {
  const ui = await fixture();
  assert.equal(ui.field('automatic').checked, false);
  assert.equal(ui.field('consent').checked, false);
  assert.equal(ui.field('prompt').value, 'Suggested prompt.');
  ui.field('api_key').value = 'fictional-secret';
  ui.button('Save API key').emit('click'); await settle();
  assert.equal(ui.field('api_key').value, '');
  assert.match(ui.requests.find(row => row.action === 'save-key').path, /plugin-settings/);
  ui.field('model').value = 'test-model'; ui.field('consent').checked = true;
  ui.field('prompt').value = 'Custom prompt.'; ui.field('prompt').emit('input');
  ui.field('effort').value = 'high';
  assert.equal(ui.content.querySelectorAll('pre')[0].textContent, 'Custom prompt.\n\nFixed category and output rules.');
  ui.content.children.find(node => node.tag === 'form').emit('submit'); await settle();
  const train = ui.requests.find(row => row.action === 'train');
  assert.equal(train.parameters.automatic, false);
  assert.equal(train.parameters.consent, true);
  assert.equal(train.parameters.prompt, 'Custom prompt.');
  assert.equal(train.parameters.effort, 'high');
  assert.deepEqual(train.parameters.categories, ui.categories);
  assert.equal(JSON.stringify(train).includes('fictional-secret'), false);
  assert.equal(train.parameters.replaceProfile, undefined);
  assert.equal(ui.requests.find(row => row.action === 'select').modelId, 'new-profile');
  assert.match(ui.status.textContent, /Profile saved and active/);
  assert.equal(ui.requests.some(row => row.action === 'preview'), false);
});

test('active profile loads its actual provider, prompt, effort, examples and consent', async () => {
  const ui = await fixture({saved:true});
  assert.equal(ui.field('provider').value, 'chatgpt');
  assert.equal(ui.field('model').value, 'test-model');
  assert.equal(ui.field('prompt').value, 'Saved prompt.');
  assert.equal(ui.field('effort').value, 'medium');
  assert.equal(ui.field('examples').value, 0);
  assert.equal(ui.field('threshold').value, .9);
  assert.equal(ui.field('consent').checked, true);
  assert.equal(ui.field('rename'), undefined);
  ui.button('Restore suggested prompt').emit('click'); await settle();
  assert.equal(ui.field('prompt').value, 'Suggested prompt.');
  ui.button('Suggest for uncategorized').emit('click'); await settle();
  const train = ui.requests.find(row => row.action === 'train');
  assert.equal(train.parameters.replaceProfile, 'profile');
  const actions = ui.requests.map(row => row.action);
  assert(actions.indexOf('train') < actions.indexOf('select'));
  assert(actions.indexOf('select') < actions.indexOf('preview'));
  assert.match(ui.status.textContent, /2 category suggestions/);
});

test('provider change clears model and consent, sign-in controls and models remain available', async () => {
  const ui = await fixture();
  ui.button('Load available models').emit('click'); await settle();
  assert.match(ui.status.textContent, /1 models loaded/);
  ui.field('consent').checked = true;
  const provider = ui.field('provider'); provider.value = 'chatgpt'; provider.emit('change');
  assert.equal(ui.field('model').value, '');
  assert.equal(ui.field('consent').checked, false);
  assert.equal(ui.field('api_key'), undefined);
  ui.button('Continue with ChatGPT').emit('click'); await settle();
  assert(ui.content.querySelectorAll('a').some(link => link.href.startsWith('https://auth.openai.com/')));
  ui.button('Check sign-in').emit('click'); await settle();
  assert.match(ui.status.textContent, /ChatGPT connected/);
  ui.button('Disconnect provider').emit('click'); await settle();
  assert.equal(ui.status.textContent, 'Disconnected.');
  ui.dialog.emit('close'); await settle();
  assert.equal(ui.requests.at(-1).action, 'cancel-sign-in');
});

test('new profile and deletion controls are separate from the editor', async () => {
  const ui = await fixture({saved:true});
  ui.button('Save as a new profile').emit('click'); await settle();
  assert.equal(ui.requests.find(row => row.action === 'train').parameters.replaceProfile, undefined);
  ui.field('profile').value = ''; ui.field('profile').emit('change'); await settle();
  assert.equal(ui.field('model').value, '');
  assert.equal(ui.field('consent').checked, false);
  const second = await fixture({saved:true});
  second.button('Delete profile').emit('click'); await settle();
  assert.equal(second.requests.find(row => row.action === 'delete').modelId, 'profile');
});

test('validation stops sending, failures are visible, sessions cancel, and read-only disables every editor', async () => {
  const invalid = await fixture();
  invalid.content.children.find(node => node.tag === 'form').valid = false;
  invalid.button('Suggest for uncategorized').emit('click'); await settle();
  assert.equal(invalid.requests.length, 2);
  const failed = await fixture({fail:'step'});
  failed.content.children.find(node => node.tag === 'form').emit('submit'); await settle();
  assert.equal(failed.error.textContent, 'Fictional provider failure');
  assert.equal(failed.error.hidden, false);
  assert(failed.requests.some(row => row.action === 'cancel'));
  const locked = await fixture({readOnly:true});
  assert(locked.controls().every(node => node.disabled));
  locked.button('Save API key').emit('click'); await settle();
  assert.equal(locked.requests.length, 2);
});

test('independent Web upgrade explains when the installed LLM plugin needs updating', async () => {
  const ui = await fixture({legacy:true});
  assert.match(ui.content.children[0].textContent, /Update the LLM categorization plugin to 1.1.0/);
  assert.equal(ui.controls().length, 0);
  assert.equal(ui.requests.length, 2);
});
