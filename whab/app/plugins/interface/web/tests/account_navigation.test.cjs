/* Run with node --test plugins/interface/web/tests/account_navigation.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/account-navigation.js'), 'utf8');
const clientSource = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/client.js'), 'utf8');
const first = 'https://localhost:5173/budgets/home/transactions/2026-10/usd?account=first&all_dates=true';
const second = first.replace('account=first', 'account=second');
const third = first.replace('account=first', 'account=third');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

function element() {
  return {
    attributes: {}, hidden: false, dataset: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    append() {}, remove() {},
    focus() { this.focused = true; },
    querySelector() { return null; },
  };
}

function harness({flush = async () => true, initialContext = {}, href = first} = {}) {
  const requests = [], replacements = [], histories = [], fallbacks = [], notices = [];
  const listeners = {};
  let content = element();
  const accountList = element();
  const accountListReplacements = [];
  accountList.replaceChildren = content => accountListReplacements.push(content);
  const sidebar = {scrollTop: 480, scrollLeft: 0};
  const status = element();
  const location = {href, assign: value => fallbacks.push(['assign', value]),
    replace: value => fallbacks.push(['replace', value]), reload: () => fallbacks.push(['reload'])};
  const window = {
    history: {state: {existing: true},
      pushState(state, _, href) { histories.push(['push', href]); location.href = href; },
      replaceState(state, _, href) { histories.push(['replace', href]); location.href = href; }},
    addEventListener: (type, callback) => {listeners[type] = callback;},
    removeEventListener: type => {delete listeners[type];},
  };
  const pageContext = {textContent: JSON.stringify({budgetUrl: '/budgets/home', page: 'transactions',
    monthToken: '2026-10', currency: 'USD', ...initialContext})};
  const document = {
    body: {append() {}}, createElement: () => status,
    getElementById: id => id === 'whab-context' ? pageContext : {},
    querySelector: selector => selector === '.sidebar' ? sidebar : selector === '.sidebar-accounts' ? accountList : selector === 'main > .page-content' ? content : null,
    querySelectorAll: () => [],
    addEventListener: (type, callback) => {listeners[type] = callback;},
    removeEventListener: type => {delete listeners[type];},
  };
  const context = {
    URL, AbortController, document, window, location,
    fetch(url, options) {const pending = deferred(); requests.push({url, options, ...pending}); return pending.promise;},
    DOMParser: class {
      parseFromString(text) {
        const fresh = element();
        fresh.dataset.pageContext = text;
        const heading = element();
        fresh.querySelector = selector => selector === 'h1' ? heading : null;
        fresh.heading = heading;
        return {querySelector: selector => selector === '.page-content[data-transactions-fragment]' ? fresh :
          selector === 'template[data-account-list]' ? {content: {cloneNode: () => ({refreshedAccountList: true})}} : null};
      }
    },
  };
  function track(node) {node.replaceWith = fresh => {replacements.push(fresh); content = fresh; track(fresh);};}
  track(content);
  vm.runInNewContext(clientSource.replaceAll('export function ', 'function ')
    .replaceAll('export async function ', 'async function '), context);
  vm.runInNewContext(source.replace(/^import .*$/m, '').replaceAll('export function ', 'function '), context);
  Object.defineProperty(context, 'updated', {get: () => context.getContext()});
  const navigator = context.initAccountNavigation({flush,
    onReplace: () => {sidebar.scrollTop = 0;}, notify: message => notices.push(message)});
  function respond(index, {marker = '1', ok = true, override = {}, url} = {}) {
    const entry = requests[index];
    const target = new URL(entry.url);
    const parts = context.parsePageRoute(target);
    const current = context.getContext();
    const selected = target.searchParams.get('account') || Object.keys(current.accountSlugs || {}).find(id => current.accountSlugs[id] === parts.accountSlug) || null;
    entry.resolve({ok, url: url ?? entry.url, headers: {get: () => marker},
      text: async () => JSON.stringify({...current, page: 'transactions', selectedAccountId: selected,
        canonicalUrl: target.href, monthToken: parts.month, currency: parts.currency.toUpperCase(), ...override})});
  }
  return {navigator, requests, replacements, histories, fallbacks, notices, sidebar, status, context,
    location, listeners, respond, accountListReplacements, content: () => content};
}

test('only same-budget HTTPS account-register destinations qualify for partial navigation', () => {
  const state = harness();
  const qualifies = value => state.context.isAccountDestination(new URL(value), new URL(first));
  assert.equal(qualifies(second), true);
  for (const value of [second.replace('/home/', '/other/'), second.replace('/usd', '/eur'),
    second.replace('https:', 'http:'), second.replace('localhost', 'untrusted.invalid'),
    second.replace('/transactions/', '/accounts/'), second + '#settings']) assert.equal(qualifies(value), false);
});

test('partial account switches replace only the content after pending saves finish', async () => {
  const saved = deferred();
  const state = harness({flush: () => saved.promise});
  const pending = state.navigator.navigate(second);
  assert.equal(state.requests.length, 0);
  saved.resolve(true);
  await tick();
  assert.equal(state.requests[0].options.cache, 'no-store');
  assert.equal(state.requests[0].options.headers['X-WHAB-Transactions-Fragment'], '1');
  assert.equal(state.content().attributes['aria-busy'], 'true');
  state.respond(0);
  assert.equal(await pending, true);
  assert.equal(state.replacements.length, 1);
  assert.equal(state.histories[0][0], 'push');
  assert.equal(state.context.updated.page, 'transactions');
  assert.equal(state.sidebar.scrollTop, 480);
  assert.equal(state.content().heading.focused, true);
  assert.equal(state.status.hidden, true);
  assert.equal(state.fallbacks.length, 0);
  assert.equal(state.accountListReplacements.length, 0);
});

test('save failures cancel the navigation and retain the current register', async () => {
  for (const flush of [async () => false, async () => {throw new Error('Memo could not be saved.');}]) {
    const state = harness({flush});
    assert.equal(await state.navigator.navigate(second), true);
    assert.equal(state.requests.length, 0);
    assert.equal(state.replacements.length, 0);
    assert.equal(state.fallbacks.length, 0);
    assert.equal(state.notices.length, 1);
  }
});

test('rapid account switches abort previous fetches and ignore stale responses', async () => {
  const state = harness();
  const previous = state.navigator.navigate(second);
  await tick();
  const latest = state.navigator.navigate(third);
  await tick();
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(1);
  await latest;
  state.respond(0);
  await previous;
  assert.equal(state.replacements.length, 1);
  assert.equal(state.histories[0][1], third);
  assert.equal(state.fallbacks.length, 0);
});

test('selecting the currently displayed account cancels an in-flight switch', async () => {
  const state = harness();
  const previous = state.navigator.navigate(second);
  await tick();
  await state.navigator.navigate(first);
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(0);
  await previous;
  assert.equal(state.replacements.length, 0);
  assert.equal(state.status.hidden, true);
});

test('missing or mismatched fragments and network errors fall back to the full server page', async () => {
  for (const response of [{marker: null}, {ok: false}, {override: {budgetUrl: '/budgets/other'}},
    {override: {monthToken: '2026-09'}}, {override: {currency: 'JPY'}}, {url: first}, null]) {
    const state = harness();
    const pending = state.navigator.navigate(second);
    await tick();
    if (response) state.respond(0, response);
    else state.requests[0].reject(new Error('Offline'));
    await pending;
    assert.deepEqual(state.fallbacks, [['assign', second]]);
    assert.equal(state.replacements.length, 0);
  }
});

test('Back/Forward loads a fresh register without adding a history entry', async () => {
  const state = harness();
  state.location.href = second;
  const pending = state.listeners.popstate();
  await tick();
  state.respond(0);
  await pending;
  assert.equal(state.histories.length, 0);
  assert.equal(state.replacements.length, 1);
  state.location.href = first.replace('/transactions/', '/accounts/');
  await state.listeners.popstate();
  assert.deepEqual(state.fallbacks, [['reload']]);
});

test('a blocked history change restores the displayed URL and disposal aborts background work', async () => {
  const blocked = harness({flush: async () => false});
  blocked.location.href = second;
  await blocked.listeners.popstate();
  assert.deepEqual(blocked.histories, [['replace', first]]);
  const state = harness();
  const pending = state.navigator.navigate(second);
  await tick();
  state.navigator.dispose();
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(0);
  await pending;
  assert.equal(state.replacements.length, 0);
  assert.equal(state.listeners.click, undefined);
  assert.equal(await state.navigator.navigate(second), false);
});

test('known budget aliases and readable account paths normalize without an extra request', async () => {
  const canonical = '/budgets/family-budget/daily-checking/transactions/2026-10/usd?all_dates=true';
  const state = harness({initialContext: {
    budgetUrl: '/budgets/family-budget', budgetAliases: ['home', 'family-budget'],
    apiPrefix: '/budgets/home/api', budgetId: 'default',
    accountSlugs: {first: 'daily-checking', second: 'savings'}, selectedAccountId: 'first',
    canonicalUrl: canonical,
  }});
  assert.equal(state.location.href, 'https://localhost:5173' + canonical);
  assert.equal(state.requests.length, 0);
  assert.equal(state.context.isAccountDestination(new URL(second), new URL(state.location.href), state.context.getContext()), true);
  assert.equal(state.context.isAccountDestination(new URL(second.replace('/home/', '/unrelated/')), new URL(state.location.href), state.context.getContext()), false);
  const pending = state.navigator.navigate(second);
  await tick();
  const next = '/budgets/family-budget/savings/transactions/2026-10/usd?all_dates=true';
  state.respond(0, {override: {canonicalUrl: next}});
  await pending;
  assert.equal(state.replacements.length, 1);
  assert.equal(state.histories.at(-1)[1], 'https://localhost:5173' + next);
  assert.equal(state.context.getContext().selectedAccountId, 'second');
  assert.equal(state.fallbacks.length, 0);
});

test('fragment aliases cannot change the stable API budget or selected known account', async () => {
  for (const override of [{apiPrefix: '/budgets/another/api'}, {budgetId: 'another'}, {selectedAccountId: 'first'}]) {
    const state = harness({initialContext: {apiPrefix: '/budgets/home/api', budgetId: 'default'}});
    const pending = state.navigator.navigate(second);
    await tick();
    state.respond(0, {override});
    await pending;
    assert.deepEqual(state.fallbacks, [['assign', second]]);
    assert.equal(state.replacements.length, 0);
  }
});

test('an explicit same-address refresh loads renamed canonical account and refreshes only its account list', async () => {
  const canonical = '/budgets/family-budget/daily-checking/transactions/2026-10/usd?all_dates=true';
  const state = harness({initialContext: {
    budgetUrl: '/budgets/family-budget', budgetAliases: ['home', 'family-budget'],
    apiPrefix: '/budgets/home/api', budgetId: 'default',
    accountSlugs: {first: 'daily-checking', second: 'savings'}, selectedAccountId: 'first', canonicalUrl: canonical,
  }});
  const pending = state.navigator.navigate(state.location.href, {refresh: true, history: 'replace'});
  await tick();
  assert.equal(state.requests.length, 1);
  const renamed = '/budgets/family-budget/everyday-cash/transactions/2026-10/usd?all_dates=true';
  state.respond(0, {override: {canonicalUrl: renamed, accountSlugs: {first: 'everyday-cash', second: 'savings'}}});
  await pending;
  assert.equal(state.replacements.length, 1);
  assert.equal(state.accountListReplacements.length, 1);
  assert.equal(state.accountListReplacements[0].refreshedAccountList, true);
  assert.equal(state.sidebar.scrollTop, 480);
  assert.equal(state.histories.at(-1)[0], 'replace');
  assert.equal(state.location.href, 'https://localhost:5173' + renamed);
  assert.equal(state.context.getContext().selectedAccountId, 'first');
  assert.equal(state.fallbacks.length, 0);
});
