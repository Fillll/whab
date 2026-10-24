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
    '[data-plaid-check-link]': [new Target()],
    '[data-plaid-cancel-link]': [new Target()],
    '[data-plaid-finish-link]': [new Target({hidden: true})],
    '[data-plaid-disconnect]': [new Target({dataset: {plaidDisconnect: 'fictional/item', institution: 'Fictional Bank'}})],
    '#plaid-confirm-disconnect': [new Target()],
    '[data-plaid-close-dialog]': [new Target({closest: () => dialog})],
  };
  return {buttons, dialog, heading: {textContent: ''}, provider: {dataset: {bankEnvironment: 'sandbox'}}, pending: {hidden: true}};
}
function harness({initialPanel = false, sdk = true, href = 'https://localhost:5173/budgets/fictional/budget/2026-10/usd', session = {}} = {}) {
  const requests = [], notices = [], redirects = [], scripts = [], timers = [], links = [];
  let current = initialPanel ? panel() : null, reloads = 0;
  const destination = new URL(href);
  const location = {href, pathname: destination.pathname, origin: destination.origin,
    assign(value) {redirects.push(String(value));}, reload() {reloads += 1;}};
  const document = new Target({
    querySelectorAll: selector => current?.buttons[selector] || [],
    querySelector: selector => selector === '#plaid-disconnect-dialog h2' ? current?.heading : selector === '[data-bank-provider="plaid"]' ? current?.provider : selector === '[data-plaid-continuation]' ? current?.pending : selector === '[data-plaid-finish-link]' ? current?.buttons[selector][0] : null,
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
  await state.links[0].options.onExit(null); state.runTimers();
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
  state.requests[1].reply(); await tick(); state.requests[2].reply({items: []}); await completing; state.runTimers();
  assert.deepEqual(state.redirects, ['https://localhost:5173/budgets/fictional/accounts/2026-10/usd']);
  assert.equal(state.canNavigate(), true);
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
  const retry = connect.emit('click'); await tick();
  assert.equal(state.requests.length, 1);
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
  state.requests[1].reply(); await tick(); state.requests[2].reply({items: []}); await completing; state.runTimers();
  assert.equal(state.canNavigate(), true);
  assert.equal(state.redirects.length, 1);
});

test('OAuth resume keeps its original scope checks and never requests a replacement Link token', async () => {
  const href = 'https://localhost:5173/budgets/fictional/accounts/2026-10/usd?oauth_state_id=fictional';
  const invalid = harness({initialPanel: true, href, session: {'whab-link-token': 'fictional-token', 'whab-link-budget': '/budgets/other'}});
  await tick();
  assert.equal(invalid.requests.length, 0);
  assert.equal(invalid.links.length, 0);
  assert.equal(invalid.canNavigate(), true);
  assert.match(invalid.notices[0], /expired/);
  const valid = harness({initialPanel: true, href, session: {'whab-link-token': 'fictional-token', 'whab-link-budget': '/budgets/fictional', 'whab-link-context': JSON.stringify({formatVersion: 1, provider: 'plaid', environment: 'sandbox', legacy: true, phase: 'ready'})}});
  await tick();
  assert.equal(valid.requests.length, 0);
  assert.equal(valid.links[0].options.receivedRedirectUri, href);
  assert.equal(valid.canNavigate(), false);
  await valid.links[0].options.onExit(null); valid.runTimers();
  assert.equal(valid.canNavigate(), true);
});

const flowId = 'a'.repeat(32);
const flow = (stage = 'ready', kind = 'link', changes = {}) => ({flow_id: flowId, generation: 7, kind, stage, deadline_ms: 4000000000000, ...changes});
const beginFlow = (kind = 'link') => ({...flow('ready', kind), link_token: 'fictional-token'});
function pendingSession({kind = 'link', phase = 'ready', environment = 'sandbox', ...changes} = {}) {
  return {'whab-link-token': 'fictional-token', 'whab-link-budget': '/budgets/fictional',
    'whab-link-context': JSON.stringify({formatVersion: 1, provider: 'plaid', environment, legacy: false, phase, ...flow('ready', kind), ...changes}),
    ...(kind === 'update' ? {'whab-link-item': 'fictional-item'} : {})};
}
async function startFlow(state, kind = 'link') {
  const starting = state.current.buttons['[data-plaid-connect]'][kind === 'link' ? 0 : 1].emit('click');
  state.requests.at(-1).reply(beginFlow(kind)); await starting;
}

test('a tracked update completes its original flow without a public-token exchange, then reads Items separately', async () => {
  const state = harness({initialPanel: true}); await startFlow(state, 'update');
  const completing = state.links[0].options.onSuccess('must-not-be-exchanged', {});
  await state.links[0].options.onExit(null);
  await state.links[0].options.onSuccess('duplicate-callback', {});
  assert.equal(state.requests.length, 2);
  assert.deepEqual(JSON.parse(state.requests[1].options.body), {flowId});
  assert.equal(state.canNavigate(), false);
  state.requests[1].reply(flow('complete', 'update')); await tick();
  assert.equal(state.requests[2].url, '/budgets/fictional/api/plaid/sync');
  assert.deepEqual(JSON.parse(state.requests[2].options.body), {itemId: 'fictional-item'});
  state.requests[2].reply(); await tick();
  assert.equal(state.requests[3].url, '/budgets/fictional/api/plaid/items');
  state.requests[3].reply({items: []}); await completing; state.runTimers();
  assert.equal(state.values.size, 0);
  assert.equal(state.links[0].handler.destroys, 1);
  assert.equal(state.canNavigate(), true);
});

test('lost exchange reply checks status and preserves the same submitted flow without another exchange', async () => {
  const state = harness({initialPanel: true}); await startFlow(state);
  const completing = state.links[0].options.onSuccess('fictional-public-token', {institution: {institution_id: 'fictional-bank', name: 'Fictional Bank'}});
  state.requests[1].reply({accounts: []}); await tick();
  assert.deepEqual(JSON.parse(state.requests[2].options.body), {flowId, publicToken: 'fictional-public-token', institutionId: 'fictional-bank', institutionName: 'Fictional Bank'});
  const persisted = state.values.get('whab-link-context');
  assert.equal(JSON.parse(persisted).phase, 'submitted');
  assert.equal(persisted.includes('public-token'), false);
  state.requests[2].reject(new Error('Connection lost')); await tick();
  assert.equal(state.requests[3].url, `/budgets/fictional/api/plaid/continuations/${flowId}`);
  state.requests[3].reply(flow('exchange_uncertain')); await completing;
  assert.equal(state.values.get('whab-link-token'), 'fictional-token');
  assert.equal(state.current.pending.hidden, false);
  const retry = state.current.buttons['[data-plaid-connect]'][0].emit('click');
  state.requests[4].reply(flow('exchange_uncertain')); await retry;
  assert.equal(state.requests.length, 5);
  assert.equal(state.links.length, 1);
  assert.match(state.notices.at(-1), /do not connect or exchange again/);
  const cancelling = state.current.buttons['[data-plaid-cancel-link]'][0].emit('click');
  assert.equal(state.requests[5].options.method, 'DELETE');
  state.requests[5].reply({error: 'The exchange is uncertain.', code: 'exchange_uncertain'}, false); await cancelling;
  assert.equal(state.values.has('whab-link-context'), true);
});

test('durable completion after a lost reply refreshes without submitting again', async () => {
  const state = harness({initialPanel: true}); await startFlow(state, 'update');
  const completing = state.links[0].options.onSuccess('ignored', {});
  state.requests[1].reject(new Error('Reply lost')); await tick();
  state.requests[2].reply(flow('complete', 'update')); await tick();
  assert.equal(state.requests[3].url, '/budgets/fictional/api/plaid/items');
  state.requests[3].reply({items: []}); await completing;
  assert.equal(state.requests.filter(request => request.url.endsWith('/exchange')).length, 1);
  assert.equal(state.values.size, 0);
  assert.equal(state.redirects.length, 1);
});

test('ready Link exit waits for cancellation and keeps an unconfirmed cancellation available', async () => {
  const state = harness({initialPanel: true}); await startFlow(state);
  const exiting = state.links[0].options.onExit({display_message: 'must-not-render-provider-payload'});
  assert.equal(state.requests[1].url, `/budgets/fictional/api/plaid/continuations/${flowId}`);
  assert.equal(state.canNavigate(), false);
  state.requests[1].reject(new Error('Lost cancellation reply')); await exiting;
  assert.equal(state.values.has('whab-link-context'), true);
  assert.equal(state.canNavigate(), true);
  assert.equal(state.notices.some(message => message.includes('must-not-render')), false);
  const cancelling = state.current.buttons['[data-plaid-cancel-link]'][0].emit('click');
  assert.equal(state.canNavigate(), false);
  state.requests[2].reply(flow('cancelled')); await cancelling;
  assert.equal(state.values.size, 0);
  assert.equal(state.current.pending.hidden, true);
});

test('a known duplicate cancels its ready flow and never exchanges another connection', async () => {
  const state = harness({initialPanel: true}); await startFlow(state);
  const completing = state.links[0].options.onSuccess('fictional-public-token', {accounts: [{mask: '1234', name: 'Checking'}], institution: {name: 'Fictional Bank'}});
  state.requests[1].reply({accounts: [{source: 'plaid', institution: 'Fictional Bank', mask: '1234', name: 'Checking', stale: false}]}); await tick();
  assert.equal(state.requests[2].options.method, 'DELETE');
  state.requests[2].reply(flow('cancelled')); await completing;
  assert.equal(state.requests.some(request => request.url.endsWith('/exchange')), false);
  assert.equal(state.values.size, 0);
  assert.match(state.notices[0], /already connected/);
});

test('OAuth checks the original flow status before loading Link and never requests a replacement token', async () => {
  const href = 'https://localhost:5173/budgets/fictional/accounts/2026-10/usd?oauth_state_id=fictional';
  const state = harness({initialPanel: true, href, session: pendingSession()});
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, `/budgets/fictional/api/plaid/continuations/${flowId}`);
  assert.equal(state.links.length, 0);
  state.requests[0].reply(flow()); await tick();
  assert.equal(state.links[0].options.receivedRedirectUri, href);
  assert.equal(state.links[0].options.token, 'fictional-token');
  const exiting = state.links[0].options.onExit(null);
  state.requests[1].reply(flow('cancelled')); await exiting;
  assert.equal(state.requests.some(request => request.url.endsWith('/link-token')), false);
});

test('ready flow survives SDK failure and resumes only after matching status', async () => {
  const state = harness({initialPanel: true, sdk: false});
  const connecting = state.current.buttons['[data-plaid-connect]'][0].emit('click');
  state.requests[0].reply(beginFlow()); await tick(); state.scripts[0].onerror(); await connecting;
  assert.equal(state.values.has('whab-link-context'), true);
  const retry = state.current.buttons['[data-plaid-connect]'][0].emit('click');
  assert.equal(state.requests[1].url, `/budgets/fictional/api/plaid/continuations/${flowId}`);
  state.requests[1].reply(flow()); await tick(); state.scripts[1].onerror(); await retry;
  assert.equal(state.requests.filter(request => request.url.endsWith('/link-token')).length, 1);
});

test('a lost begin response can only be explicitly retried for the same Item', async () => {
  const state = harness({initialPanel: true});
  const starting = state.current.buttons['[data-plaid-connect]'][1].emit('click');
  state.requests[0].reject(new Error('Reply lost')); await starting;
  assert.equal(state.requests.length, 1);
  const retry = state.current.buttons['[data-plaid-connect]'][1].emit('click');
  assert.deepEqual(JSON.parse(state.requests[1].options.body), {itemId: 'fictional-item'});
  // The facade recovers its original ready token; browser never submits an exchange here.
  state.requests[1].reply(beginFlow('update')); await retry;
  assert.equal(state.links[0].options.token, 'fictional-token');
  assert.equal(state.requests.length, 2);
});

test('a submitted flow remains blocked after reload even if status is ready', async () => {
  const state = harness({initialPanel: true, session: pendingSession({phase: 'submitted'})});
  const retry = state.current.buttons['[data-plaid-connect]'][0].emit('click');
  state.requests[0].reply(flow()); await retry;
  assert.equal(state.links.length, 0);
  assert.equal(state.values.has('whab-link-context'), true);
  assert.match(state.notices[0], /do not connect or exchange again/);
});

test('scope, kind, generation, deadline and response shape cannot be replaced by browser retries', async () => {
  for (const changes of [{environment: 'production'}, {provider: 'other'}, {generation: true}, {flow_id: 'b'.repeat(31)}]) {
    const state = harness({initialPanel: true, session: pendingSession(changes)});
    await state.current.buttons['[data-plaid-connect]'][0].emit('click');
    assert.equal(state.requests.length, 0);
    assert.equal(state.links.length, 0);
    assert.equal(state.values.has('whab-link-context'), true);
  }
  for (const changes of [{generation: 8}, {deadline_ms: 4000000000001}, {flow_id: 'b'.repeat(32)}, {secret: 'private'}, {stage: 'bad'}]) {
    const state = harness({initialPanel: true, session: pendingSession()});
    const checking = state.current.buttons['[data-plaid-connect]'][0].emit('click');
    state.requests[0].reply(flow('ready', 'link', changes)); await checking;
    assert.equal(state.links.length, 0);
    assert.equal(JSON.parse(state.values.get('whab-link-context')).generation, 7);
    assert.equal(state.requests.length, 1);
  }
});

test('malformed, expired and wrong-kind begin responses cannot load Link', async () => {
  for (const response of [beginFlow('update'), {...beginFlow(), secret: 'private'}, {...beginFlow(), generation: true}, {...beginFlow(), deadline_ms: 1}, {...beginFlow(), link_token: ''}]) {
    const state = harness({initialPanel: true});
    const starting = state.current.buttons['[data-plaid-connect]'][0].emit('click');
    state.requests[0].reply(response); await starting;
    assert.equal(state.links.length, 0);
    assert.equal(state.requests.length, 1);
    assert.equal(state.canNavigate(), true);
  }
});

test('status control refreshes completed flows and cancellation cannot erase an uncertain stage', async () => {
  const state = harness({initialPanel: true, session: pendingSession()});
  const checking = state.current.buttons['[data-plaid-check-link]'][0].emit('click');
  await state.current.buttons['[data-plaid-cancel-link]'][0].emit('click');
  assert.equal(state.requests.length, 1);
  state.requests[0].reply(flow('complete')); await tick();
  state.requests[1].reply({items: []}); await checking;
  assert.equal(state.values.size, 0);
  const uncertain = harness({initialPanel: true, session: pendingSession()});
  const cancelling = uncertain.current.buttons['[data-plaid-cancel-link]'][0].emit('click');
  uncertain.requests[0].reply(flow('apply_uncertain')); await cancelling;
  assert.equal(uncertain.values.has('whab-link-context'), true);
  assert.match(uncertain.notices[0], /unfinished operation/);
});


test('stored update kind requires its original Item before any status or exchange', async () => {
  const session = pendingSession({kind: 'update'});
  delete session['whab-link-item'];
  const state = harness({initialPanel: true, session});
  await state.current.buttons['[data-plaid-connect]'][0].emit('click');
  assert.equal(state.requests.length, 0);
  assert.equal(state.links.length, 0);
  assert.equal(state.values.has('whab-link-context'), true);
});


test('Finish saving is offered only after captured status and resumes without a public token once', async () => {
  const state = harness({initialPanel: true, session: pendingSession({phase: 'submitted'})});
  const finish = state.current.buttons['[data-plaid-finish-link]'][0];
  await finish.emit('click');
  assert.equal(finish.hidden, true);
  assert.equal(state.requests.length, 0);
  const checking = state.current.buttons['[data-plaid-check-link]'][0].emit('click');
  state.requests[0].reply(flow('captured')); await checking;
  assert.equal(finish.hidden, false);
  const finishing = finish.emit('click'); void finish.emit('click');
  assert.equal(state.requests.length, 2);
  state.requests[1].reply(flow('apply_uncertain')); await tick();
  assert.deepEqual(JSON.parse(state.requests[2].options.body), {flowId});
  assert.equal(state.canNavigate(), false);
  state.requests[2].reply(flow('complete')); await tick();
  state.requests[3].reply({items: []}); await finishing;
  assert.equal(state.requests.filter(request => request.url.endsWith('/exchange')).length, 1);
  assert.equal(state.values.size, 0);
  assert.equal(finish.hidden, true);
});

test('Finish saving retains its intent on unknown replies and never exchanges a ready or externally uncertain flow', async () => {
  for (const nextStage of ['ready', 'exchange_uncertain']) {
    const state = harness({initialPanel: true, session: pendingSession({phase: 'submitted'})});
    const checking = state.current.buttons['[data-plaid-check-link]'][0].emit('click');
    state.requests[0].reply(flow('captured')); await checking;
    const finishing = state.current.buttons['[data-plaid-finish-link]'][0].emit('click');
    state.requests[1].reply(flow(nextStage)); await tick();
    state.requests[2].reply(flow(nextStage)); await finishing;
    assert.equal(state.requests.some(request => request.url.endsWith('/exchange')), false);
    assert.equal(state.values.has('whab-link-context'), true);
  }
  const state = harness({initialPanel: true, session: pendingSession({phase: 'submitted'})});
  const checking = state.current.buttons['[data-plaid-check-link]'][0].emit('click');
  state.requests[0].reply(flow('apply_uncertain')); await checking;
  const finishing = state.current.buttons['[data-plaid-finish-link]'][0].emit('click');
  state.requests[1].reply(flow('captured')); await tick();
  state.requests[2].reject(new Error('Lost native reply')); await tick();
  state.requests[3].reject(new Error('Status unavailable')); await finishing;
  assert.equal(state.values.has('whab-link-context'), true);
  assert.equal(state.requests.filter(request => request.url.endsWith('/exchange')).length, 1);
  assert.equal(state.canNavigate(), true);
});
