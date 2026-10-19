const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_plaid/static/connect.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
class Event {
  constructor(type, options = {}) {Object.assign(this, {type, cancelable: true, defaultPrevented: false}, options);}
  preventDefault() {if (this.cancelable) this.defaultPrevented = true;}
}
class Target {
  constructor(properties = {}) {this.listeners = new Map(); Object.assign(this, properties);}
  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
  }
  dispatchEvent(event) {for (const callback of this.listeners.get(event.type) || []) callback(event); return !event.defaultPrevented;}
  emit(type) {return Promise.all([...(this.listeners.get(type) || [])].map(callback => callback(new Event(type, {target: this, currentTarget: this}))));}
}
function panel() {
  const dialog = new Target({open: false, showModal() {this.open = true;}, close() {this.open = false;}});
  const buttons = {
    '[data-plaid-connect]': [new Target({dataset: {}}), new Target({dataset: {plaidConnect: 'fictional-item'}})],
    '[data-plaid-sync]': [new Target()],
    '[data-plaid-disconnect]': [new Target({dataset: {plaidDisconnect: 'fictional/item', institution: 'Fictional Bank'}})],
    '#plaid-confirm-disconnect': [new Target()],
    '[data-plaid-close-dialog]': [new Target({closest: () => dialog})],
  };
  return {buttons, dialog, heading: {textContent: ''}};
}
function harness({initialPanel = false, sdk = true, href = 'https://localhost:5173/budgets/fictional/budget/2026-10/usd', session = {}} = {}) {
  const requests = [], notices = [], redirects = [], scripts = [], timers = [], links = [];
  let current = initialPanel ? panel() : null, reloads = 0;
  const destination = new URL(href);
  const location = {href, pathname: destination.pathname, origin: destination.origin,
    assign(value) {redirects.push(String(value));}, reload() {reloads += 1;}};
  const document = new Target({
    querySelectorAll: selector => current?.buttons[selector] || [],
    querySelector: selector => selector === '#plaid-disconnect-dialog h2' ? current?.heading : null,
    getElementById: id => id === 'plaid-disconnect-dialog' ? current?.dialog : null,
    createElement: () => new Target({remove() {this.removed = true;}}),
    head: {appendChild: script => scripts.push(script)},
  });
  document.addEventListener('whab:notice', event => {notices.push(event.detail.message); event.preventDefault();});
  const values = new Map(Object.entries(session));
  const window = {alert() {throw new Error('The host notice listener should handle provider messages.');}};
  if (sdk) window.Plaid = {create(options) {
    const handler = {opens: 0, destroys: 0, open() {this.opens += 1;}, destroy() {this.destroys += 1;}};
    links.push({options, handler}); return handler;
  }};
  const context = {document, window, location, URL, CustomEvent: Event,
    setTimeout: callback => timers.push(callback),
    sessionStorage: {getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)},
    fetch(url, options) {
      let resolve, reject;
      const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
      requests.push({url, options, reject, reply: (result = {}, ok = true) => resolve({ok, json: async () => result})});
      return promise;
    },
  };
  vm.runInNewContext(source, context);
  return {document, location, requests, notices, redirects, scripts, links, values,
    get current() {return current;}, get reloads() {return reloads;},
    replace() {current = panel(); location.href = 'https://localhost:5173/budgets/fictional/accounts/2026-10/usd'; document.dispatchEvent(new Event('whab:page-ready')); return current;},
    ready() {document.dispatchEvent(new Event('whab:page-ready'));},
    canNavigate() {return document.dispatchEvent(new Event('whab:before-navigate'));},
    runTimers() {for (const timer of timers.splice(0)) timer();},
  };
}

test('Accounts re-entry binds fresh provider controls once without repeating module startup', async () => {
  const state = harness();
  assert.equal(state.canNavigate(), true);
  assert.equal(state.requests.length, 0);
  const first = state.replace(); state.ready(); state.ready();
  for (const buttons of Object.values(first.buttons)) for (const button of buttons) assert.equal(button.listeners.get('click').size, 1);
  await first.buttons['[data-plaid-disconnect]'][0].emit('click');
  assert.equal(first.dialog.open, true);
  assert.equal(first.heading.textContent, 'Disconnect Fictional Bank?');
  await first.buttons['[data-plaid-close-dialog]'][0].emit('click');
  assert.equal(first.dialog.open, false);
  const second = state.replace(); state.ready();
  for (const buttons of Object.values(second.buttons)) for (const button of buttons) assert.equal(button.listeners.get('click').size, 1);
  const syncing = second.buttons['[data-plaid-sync]'][0].emit('click');
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, '/budgets/fictional/api/plaid/sync');
  state.requests[0].reply(); await syncing;
  assert.equal(state.reloads, 1);
  assert.equal(state.document.listeners.get('whab:page-ready').size, 1);
  assert.equal(state.document.listeners.get('whab:before-navigate').size, 1);
});

test('sync and disconnect veto navigation until all active provider requests settle', async () => {
  const state = harness({initialPanel: true});
  const sync = state.current.buttons['[data-plaid-sync]'][0];
  const syncing = sync.emit('click'); void sync.emit('click');
  assert.equal(state.requests.length, 1);
  assert.equal(state.canNavigate(), false);
  await state.current.buttons['[data-plaid-disconnect]'][0].emit('click');
  const confirm = state.current.buttons['#plaid-confirm-disconnect'][0];
  const disconnecting = confirm.emit('click'); void confirm.emit('click');
  assert.equal(state.requests.length, 2);
  assert.equal(state.requests[1].url, '/budgets/fictional/api/plaid/items/fictional%2Fitem');
  assert.equal(state.requests[1].options.method, 'DELETE');
  state.requests[0].reply({error: 'Sync failed.'}, false); await syncing;
  assert.equal(sync.disabled, false);
  assert.equal(state.canNavigate(), false);
  state.requests[1].reply({error: 'Disconnect failed.'}, false); await disconnecting;
  assert.equal(confirm.disabled, false);
  assert.equal(state.canNavigate(), true);
  assert.deepEqual(state.notices, ['Sync failed.', 'Disconnect failed.']);
});

test('Link blocks navigation through token loading and its open session, then releases on exit', async () => {
  const state = harness(); state.replace();
  const manage = state.current.buttons['[data-plaid-connect]'][1];
  const connecting = manage.emit('click'); void manage.emit('click');
  assert.equal(state.canNavigate(), false);
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, '/budgets/fictional/api/plaid/link-token');
  assert.deepEqual(JSON.parse(state.requests[0].options.body), {itemId: 'fictional-item'});
  state.requests[0].reply({link_token: 'fictional-token'}); await connecting;
  assert.equal(state.links[0].handler.opens, 1);
  assert.equal(state.canNavigate(), false);
  state.links[0].options.onExit(null); state.runTimers();
  assert.equal(state.canNavigate(), true);
  assert.equal(state.links[0].handler.destroys, 1);
  assert.equal(state.current.buttons['[data-plaid-connect]'].every(button => !button.disabled), true);
  assert.equal(state.values.size, 0);
});

test('Link completion retains the addressed budget and remains guarded through its existing sync and redirect', async () => {
  const state = harness(); state.replace();
  const connecting = state.current.buttons['[data-plaid-connect]'][1].emit('click');
  state.requests[0].reply({link_token: 'fictional-token'}); await connecting;
  const completing = state.links[0].options.onSuccess('fictional-public-token', {});
  assert.equal(state.requests[1].url, '/budgets/fictional/api/plaid/sync');
  assert.deepEqual(JSON.parse(state.requests[1].options.body), {itemId: 'fictional-item'});
  assert.equal(state.canNavigate(), false);
  state.requests[1].reply(); await completing; state.runTimers();
  assert.deepEqual(state.redirects, ['https://localhost:5173/budgets/fictional/accounts/2026-10/usd']);
  assert.equal(state.canNavigate(), false);
  assert.equal(state.values.size, 0);
  assert.equal(state.links[0].handler.destroys, 1);
});

test('failed SDK loading releases the navigation guard and supports a fresh later attempt', async () => {
  const state = harness({sdk: false}); state.replace();
  const connect = state.current.buttons['[data-plaid-connect]'][0];
  const first = connect.emit('click'); state.requests[0].reply({link_token: 'fictional-token'}); await tick();
  assert.equal(state.scripts.length, 1);
  assert.equal(state.canNavigate(), false);
  state.scripts[0].onerror(); await first;
  assert.equal(state.canNavigate(), true);
  assert.equal(state.scripts[0].removed, true);
  const retry = connect.emit('click'); state.requests[1].reply({link_token: 'fictional-retry'}); await tick();
  assert.equal(state.scripts.length, 2);
  state.scripts[1].onerror(); await retry;
  assert.equal(state.canNavigate(), true);
});

test('a Link exit cannot release navigation while its successful connection is still being saved', async () => {
  const state = harness(); state.replace();
  const connecting = state.current.buttons['[data-plaid-connect]'][1].emit('click');
  state.requests[0].reply({link_token: 'fictional-token'}); await connecting;
  const completing = state.links[0].options.onSuccess('fictional-public-token', {});
  state.links[0].options.onExit(null);
  assert.equal(state.canNavigate(), false);
  state.requests[1].reply(); await completing; state.runTimers();
  assert.equal(state.canNavigate(), true);
  assert.equal(state.redirects.length, 1);
});

test('OAuth resume keeps its original scope checks and never requests a replacement Link token', async () => {
  const href = 'https://localhost:5173/budgets/fictional/accounts/2026-10/usd?oauth_state_id=fictional';
  const invalid = harness({href, session: {'whab-link-token': 'fictional-token', 'whab-link-budget': '/budgets/other'}});
  await tick();
  assert.equal(invalid.requests.length, 0);
  assert.equal(invalid.links.length, 0);
  assert.equal(invalid.canNavigate(), true);
  assert.match(invalid.notices[0], /different budget/);
  const valid = harness({href, session: {'whab-link-token': 'fictional-token', 'whab-link-budget': '/budgets/fictional'}});
  await tick();
  assert.equal(valid.requests.length, 0);
  assert.equal(valid.links[0].options.receivedRedirectUri, href);
  assert.equal(valid.canNavigate(), false);
  valid.links[0].options.onExit(null); valid.runTimers();
  assert.equal(valid.canNavigate(), true);
});
