/* Main-page navigation exercises the actual shared controller without replacing the shell. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const directory = path.join(__dirname, '../src/whab_plugin_web/static');
const source = fs.readFileSync(path.join(directory, 'account-navigation.js'), 'utf8');
const clientSource = fs.readFileSync(path.join(directory, 'client.js'), 'utf8');
const origin = 'https://localhost:5173';
const address = (page, month = 'current-month', query = '') => `${origin}/budgets/household/${page}/${month}/usd${query}`;
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

function element(classes = []) {
  const names = new Set(classes);
  return {
    attributes: {}, dataset: {}, hidden: false, textContent: '',
    classList: {contains: name => names.has(name),
      toggle(name, enabled) {if (enabled) names.add(name); else names.delete(name);},
      add: name => names.add(name), remove: name => names.delete(name)},
    setAttribute(name, value) {this.attributes[name] = value;},
    getAttribute(name) {return this.attributes[name] ?? null;},
    hasAttribute(name) {return name in this.attributes;},
    removeAttribute(name) {delete this.attributes[name];},
    append() {}, remove() {this.removed = true;},
    focus(options) {this.focused = true; this.focusOptions = options;},
    querySelector() {return null;}, querySelectorAll() {return [];},
    closest() {return null;},
  };
}

function link(href, classes = ['nav-item']) {
  const node = element(classes);
  node.href = href;
  node.target = '';
  node.closest = selector => selector.includes('a') ? node : null;
  return node;
}

function harness({page = 'budget', href = address(page), initial = {}, flush = async () => true,
  beforeReplace = () => true} = {}) {
  const requests = [], replacements = [], histories = [], fallbacks = [], notices = [], mounts = [];
  const listeners = new Map();
  const on = (name, callback) => {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(callback);
  };
  const off = (name, callback) => listeners.get(name)?.delete(callback);
  const sidebar = {...element(), scrollTop: 515, scrollLeft: 9};
  const shell = element();
  const topbar = element();
  const main = element();
  main.dataset.pageAssets = 'fixture-assets';
  const breadcrumb = element();
  const currency = {...element(), value: 'USD'};
  const budgetSwitch = element();
  budgetSwitch.dataset.personalUrl = `${origin}/budgets/personal-alias/budget/current-month/usd?category=old#old`;
  budgetSwitch.dataset.demoUrl = `${origin}/budgets/demo/budget/current-month/usd?account=old#old`;
  const accountList = element();
  const accountUpdates = [];
  accountList.replaceChildren = value => accountUpdates.push(value);
  const navigationLinks = ['budget', 'transactions', 'insights', 'accounts'].map(name => link(address(name)));
  navigationLinks[0].setAttribute('aria-current', 'page');
  const account = link(`${address('transactions')}?account=first`, ['sidebar-account']);
  account.dataset.accountId = 'first';
  navigationLinks.push(account);
  const status = element();
  let content = element();
  function track(node) {
    node.replaceWith = fresh => {replacements.push(fresh); content = fresh; track(fresh);};
  }
  track(content);
  const original = content;
  for (const node of [shell, sidebar, topbar]) node.replaceWith = () => assert.fail('Navigation must preserve the shell.');
  const location = {href, assign: value => fallbacks.push(['assign', String(value)]),
    replace: value => fallbacks.push(['replace', String(value)]), reload: () => fallbacks.push(['reload'])};
  Object.defineProperties(location, {
    origin: {get: () => new URL(location.href).origin},
    hash: {get: () => new URL(location.href).hash},
  });
  const window = {scrollX: 0, scrollY: 27, scrollCalls: [],
    history: {state: {existing: true},
      pushState(state, _, href) {histories.push(['push', String(href), state]); location.href = String(href);},
      replaceState(state, _, href) {histories.push(['replace', String(href), state]); location.href = String(href);}},
    addEventListener: on, removeEventListener: off,
    scrollTo(value) {
      this.scrollCalls.push(value);
      this.scrollX = value.left || 0;
      this.scrollY = value.top || 0;
    },
  };
  const pageContext = {textContent: JSON.stringify({
    budgetId: 'fictional-budget', apiPrefix: '/budgets/fictional-budget/api',
    budgetUrl: '/budgets/household', budgetAliases: ['household', 'home'], workspace: 'personal',
    page, monthToken: 'current-month', month: '2026-10', currency: 'USD', pageFragments: true,
    accountSlugs: {first: 'checking'}, selectedAccountId: null, selectedAccountIds: [],
    transactionPeriod: null, startDate: null, endDate: null, reviewFilter: 'all',
    canonicalUrl: href, ...initial,
  })};
  const document = {
    body: {append() {}}, createElement: () => status,
    getElementById: id => id === 'whab-context' ? pageContext : null,
    querySelector(selector) {
      if (selector === '.sidebar') return sidebar;
      if (selector === '.sidebar-accounts') return accountList;
      if (selector === 'main > .page-content') return content;
      if (selector === '.breadcrumb > span') return breadcrumb;
      if (selector.includes('data-navigation="currency"')) return currency;
      if (selector === '.app-shell') return shell;
      if (selector === 'main') return main;
      if (selector === '[data-budget-switch]') return budgetSwitch;
      return null;
    },
    querySelectorAll: selector => selector.includes('sidebar') ? navigationLinks : [],
    addEventListener: on, removeEventListener: off,
  };
  const runtime = {URL, AbortController, document, window, location,
    fetch(url, options) {const pending = deferred(); requests.push({url: String(url), options, ...pending}); return pending.promise;},
    DOMParser: class {
      parseFromString(text) {
        const payload = JSON.parse(text);
        const fresh = element();
        fresh.dataset.pageContext = JSON.stringify(payload.context);
        fresh.dataset.pageAssets = payload.assets;
        const heading = element();
        fresh.heading = heading;
        const count = {content: {querySelector: () => null}};
        fresh.querySelector = selector => selector === 'h1' ? heading :
          selector === 'template[data-transaction-navigation-count]' ? count : null;
        const accounts = payload.accounts ? {content: {cloneNode: () => ({kind: 'accounts'})}} : null;
        const fields = {
          '[data-page-account-options]': {childNodes: []},
          '[data-page-category-options]': {childNodes: []},
          '[data-page-group-options]': {childNodes: []},
        };
        if (payload.missingOption) delete fields[payload.missingOption];
        const options = payload.options ? {content: {querySelector: selector => fields[selector] || null,
          querySelectorAll: () => Object.values(fields), cloneNode: () => ({kind: 'options'})}} : null;
        return {querySelector(selector) {
          if (selector === '.page-content[data-page-fragment]') return payload.fragment === 'page' ? fresh : null;
          if (selector === '.page-content[data-transactions-fragment]') return payload.fragment === 'transactions' ? fresh : null;
          if (selector === '[data-transactions-summary]') return payload.fragment === 'summary' ? fresh : null;
          if (selector === 'template[data-account-list]') return accounts;
          if (selector === 'template[data-page-options]') return options;
          return null;
        }};
      }
    },
  };
  vm.runInNewContext(clientSource.replaceAll('export function ', 'function ')
    .replaceAll('export async function ', 'async function '), runtime);
  vm.runInNewContext(source.replace(/^import .*$/gm, '').replaceAll('export function ', 'function '), runtime);
  const controller = runtime.initAccountNavigation({flush, beforeReplace,
    onReplace(value) {mounts.push(value); sidebar.scrollTop = 0; sidebar.scrollLeft = 0;},
    notify: message => notices.push(message)});

  function respond(index, {ok = true, header, marker = '1', url, override = {}, fragment,
    accounts = true, options = true, assets = 'fixture-assets', missingOption, omit = [], textBarrier} = {}) {
    const request = requests[index];
    const target = new URL(request.url);
    const route = runtime.parsePageRoute(target);
    const generic = request.options.headers['X-WHAB-Page-Fragment'] === '1';
    const summary = request.options.headers['X-WHAB-Transactions-Fragment'] === 'summary';
    const expectedHeader = generic ? 'X-WHAB-Page-Fragment' : 'X-WHAB-Transactions-Fragment';
    const context = {...runtime.getContext(), page: route.page, monthToken: route.month,
      currency: route.currency.toUpperCase(), canonicalUrl: target.href,
      selectedAccountId: target.searchParams.get('account'),
      selectedAccountIds: target.searchParams.getAll('accounts'),
      transactionSearch: target.searchParams.get('q') || '',
      transactionRequestedPage: target.searchParams.get('tx_page') || '1', ...override};
    for (const key of omit) delete context[key];
    const serialized = JSON.stringify({context, accounts, options, assets, missingOption,
      fragment: fragment || (generic ? 'page' : summary ? 'summary' : 'transactions')});
    request.resolve({ok, url: url ?? request.url,
      headers: {get: name => name === (header || expectedHeader) ? (summary && marker === '1' ? 'summary' : marker) : null},
      text: () => textBarrier ? textBarrier.promise : Promise.resolve(serialized)});
    return serialized;
  }
  function click(node, values = {}) {
    const event = {target: node, button: 0, defaultPrevented: false,
      preventDefault() {this.defaultPrevented = true;}, ...values};
    for (const listener of listeners.get('click') || []) listener(event);
    return event;
  }
  async function pop(url) {
    location.href = url;
    await Promise.all([...(listeners.get('popstate') || [])].map(listener => listener({state: window.history.state})));
  }
  return {controller, requests, replacements, histories, fallbacks, notices, mounts, sidebar,
    shell, topbar, breadcrumb, currency, budgetSwitch, status, location, window, document, runtime,
    navigationLinks, accountUpdates, original, content: () => content, respond, click, pop};
}

for (const sourcePage of ['budget', 'transactions', 'insights', 'accounts']) {
  for (const targetPage of ['budget', 'transactions', 'insights', 'accounts']) {
    test(`${sourcePage} to ${targetPage} changes only page content and retains the current shell`, async () => {
      const state = harness({page: sourcePage});
      const target = address(targetPage, '2026-11');
      const pending = state.controller.navigate(target);
      await tick();
      assert.equal(state.requests.length, 1);
      const legacy = sourcePage === 'transactions' && targetPage === 'transactions';
      const headers = state.requests[0].options.headers;
      assert.equal(headers[legacy ? 'X-WHAB-Transactions-Fragment' : 'X-WHAB-Page-Fragment'], '1');
      assert.equal(state.requests[0].options.cache, 'no-store');
      state.respond(0); await pending;
      assert.equal(state.replacements.length, 1);
      assert.equal(state.mounts.length, 1);
      assert.equal(state.runtime.getContext().page, targetPage);
      assert.equal(state.runtime.getContext().monthToken, '2026-11');
      assert.equal(state.breadcrumb.textContent, targetPage[0].toUpperCase() + targetPage.slice(1));
      for (const navigationLink of state.navigationLinks.filter(node => node.classList.contains('nav-item'))) {
        const selected = state.runtime.parsePageRoute(new URL(navigationLink.href)).page === targetPage;
        assert.equal(navigationLink.attributes['aria-current'], selected ? 'page' : undefined);
        assert.equal(navigationLink.classList.contains('active'), selected);
      }
      assert.equal(state.sidebar.scrollTop, 515);
      assert.equal(state.sidebar.scrollLeft, 9);
      assert.equal(state.content().heading.focused, true);
      assert.equal(state.content().heading.attributes.tabindex, '-1');
      assert.equal(state.location.href, target);
      assert.deepEqual(state.histories.map(([action, href]) => [action, href]), [['push', target]]);
      assert.equal(state.histories[0][2].existing, true);
      assert.deepEqual(state.fallbacks, []);
      assert.equal(state.status.hidden, true);
      state.controller.dispose();
    });
  }
}

test('retained budget-switch destinations follow the mounted view and month without carrying old scope', async () => {
  const state = harness();
  const pending = state.controller.navigate(address('insights', '2026-11', '?category=selected'));
  await tick();
  state.respond(0); await pending;
  assert.equal(state.budgetSwitch.dataset.personalUrl,
    `${origin}/budgets/personal-alias/insights/2026-11/usd`);
  assert.equal(state.budgetSwitch.dataset.demoUrl,
    `${origin}/budgets/demo/insights/2026-11/usd`);
  assert.equal(state.runtime.getContext().canonicalUrl,
    address('insights', '2026-11', '?category=selected'));
});

test('ordinary same-scope main/content links are intercepted while browser-owned destinations remain native', async () => {
  const state = harness();
  const target = link(address('insights'));
  assert.equal(state.click(target).defaultPrevented, true);
  await tick(); state.respond(0); await tick();
  assert.equal(state.replacements.length, 1);
  for (const [href, attributes, values] of [
    [address('accounts'), {}, {button: 1}], [address('accounts'), {}, {metaKey: true}],
    [address('accounts'), {}, {ctrlKey: true}], [address('accounts'), {}, {shiftKey: true}],
    [address('accounts'), {}, {altKey: true}], [address('accounts'), {download: ''}, {}],
    [address('accounts'), {target: '_blank'}, {}], [address('accounts') + '#settings', {}, {}],
    [address('accounts').replace('/usd', '/eur'), {}, {}],
    [address('accounts').replace('/household/', '/foreign/'), {}, {}],
    [address('accounts').replace(origin, 'https://foreign.invalid'), {}, {}],
    [`${origin}/budgets/household/api/export?workspace=personal`, {}, {}],
  ]) {
    const node = link(href);
    for (const [name, value] of Object.entries(attributes)) {
      if (name === 'target') node.target = value;
      else node.setAttribute(name, value);
    }
    assert.equal(state.click(node, values).defaultPrevented, false, href);
  }
  await tick();
  assert.equal(state.requests.length, 1);
  state.controller.dispose();
});

test('the generic route is disabled unless the host advertises the exact capability', async () => {
  for (const flag of [undefined, false, 'true']) {
    const state = harness({initial: {pageFragments: flag}});
    assert.equal(await state.controller.navigate(address('accounts')), false);
    assert.equal(state.click(link(address('insights'))).defaultPrevented, false);
    assert.equal(state.requests.length, 0);
    state.controller.dispose();
  }
});

test('category query links replace the planner without changing the shell or transaction scope', async () => {
  const state = harness();
  const target = address('budget', 'current-month', '?category=food%26home');
  const event = state.click(link(target, ['budget-category-link']));
  assert.equal(event.defaultPrevented, true);
  await tick(); state.respond(0); await tick();
  assert.equal(state.replacements.length, 1);
  assert.equal(state.location.href, target);
  assert.equal(state.runtime.getContext().page, 'budget');
  assert.equal(state.runtime.getContext().selectedAccountId, null);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('generic mounts replace stale route-specific context rather than retaining old register selections', async () => {
  const state = harness({page: 'transactions', initial: {
    selectedAccountId: 'first', selectedAccountIds: ['first'], transactionSearch: 'old query',
    transactionRequestedPage: '9', obsoleteRouteField: 'must disappear',
  }});
  const pending = state.controller.navigate(address('budget'));
  await tick(); state.respond(0, {omit: ['transactionSearch', 'transactionRequestedPage', 'obsoleteRouteField']}); await pending;
  const context = state.runtime.getContext();
  assert.equal(context.page, 'budget');
  assert.equal(context.obsoleteRouteField, undefined);
  assert.equal(context.transactionSearch, undefined);
  assert.equal(context.selectedAccountId, null);
  assert.deepEqual(plain(context.selectedAccountIds), []);
  assert.equal(state.runtime.pageUrl({page: 'transactions'}).searchParams.has('account'), false);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('page navigation waits for financial saves and never fetches after a failed barrier', async () => {
  const barrier = deferred();
  const state = harness({flush: () => barrier.promise});
  const pending = state.controller.navigate(address('accounts'));
  assert.equal(state.requests.length, 0);
  barrier.resolve(false); await pending;
  assert.equal(state.requests.length, 0);
  assert.equal(state.content(), state.original);
  assert.equal(state.histories.length, 0);
  assert.equal(state.notices.length, 1);
  assert.equal(state.controller.pendingDestination(), null);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('newer pages cancel earlier fetch and text responses before either can replace the current view', async () => {
  const state = harness();
  const first = state.controller.navigate(address('insights'));
  await tick();
  const text = deferred();
  const old = state.respond(0, {textBarrier: text});
  await tick();
  const second = state.controller.navigate(address('accounts'));
  await tick();
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(1); await second;
  text.resolve(old); await first;
  assert.equal(state.replacements.length, 1);
  assert.equal(state.runtime.getContext().page, 'accounts');
  assert.deepEqual(state.histories.map(([action, href]) => [action, href]), [['push', address('accounts')]]);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('canceling a pending page request for a new draft keeps the old view, context and address', async () => {
  const state = harness();
  const pending = state.controller.navigate(address('accounts'));
  await tick();
  state.controller.cancelPending();
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(0); await pending;
  assert.equal(state.content(), state.original);
  assert.equal(state.runtime.getContext().page, 'budget');
  assert.equal(state.location.href, address('budget'));
  assert.equal(state.histories.length, 0);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('a late draft veto retains content and history without a full-page fallback', async () => {
  let releases = 0;
  const state = harness({beforeReplace: () => {releases += 1; return false;}});
  const pending = state.controller.navigate(address('accounts'));
  await tick(); state.respond(0); await pending;
  assert.equal(releases, 1);
  assert.equal(state.content(), state.original);
  assert.equal(state.runtime.getContext().page, 'budget');
  assert.equal(state.histories.length, 0);
  assert.equal(state.status.hidden, true);
  assert.equal(state.controller.pendingDestination(), null);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('Back and Forward replace content without creating an additional history entry', async () => {
  const state = harness({page: 'accounts'});
  for (const target of [address('budget', '2026-09'), address('insights', '2026-11')]) {
    const pending = state.pop(target);
    await tick(); state.respond(state.requests.length - 1); await pending;
    assert.equal(state.location.href, target);
  }
  assert.equal(state.replacements.length, 2);
  assert.deepEqual(state.histories, []);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('a rejected Back navigation restores the address of the still-visible page', async () => {
  const state = harness({page: 'accounts', beforeReplace: () => false});
  const pending = state.pop(address('budget', '2026-09'));
  await tick(); state.respond(0); await pending;
  assert.equal(state.content(), state.original);
  assert.equal(state.location.href, address('accounts'));
  assert.deepEqual(state.histories.map(([action, href]) => [action, href]), [['replace', address('accounts')]]);
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('main-view changes start at the top while Back and Forward restore saved content positions', async () => {
  const state = harness();
  state.window.scrollX = 12; state.window.scrollY = 680;
  const next = state.controller.navigate(address('accounts'));
  await tick(); state.respond(0); await next;
  assert.deepEqual(plain(state.window.scrollCalls.at(-1)), {left: 0, top: 0, behavior: 'instant'});
  state.window.scrollX = 4; state.window.scrollY = 140;
  const back = state.pop(address('budget'));
  await tick(); state.respond(1); await back;
  assert.deepEqual(plain(state.window.scrollCalls.at(-1)), {left: 12, top: 680, behavior: 'instant'});
  const forward = state.pop(address('accounts'));
  await tick(); state.respond(2); await forward;
  assert.deepEqual(plain(state.window.scrollCalls.at(-1)), {left: 4, top: 140, behavior: 'instant'});
  const calls = state.window.scrollCalls.length;
  const refresh = state.controller.navigate(address('accounts'), {refresh: true, history: 'replace', focus: false});
  await tick(); state.respond(3); await refresh;
  assert.equal(state.window.scrollCalls.length, calls);
  assert.equal(state.window.scrollY, 140);
  state.controller.dispose();
});

test('same-document anchor history remains browser-owned and cancels a stale pending page change', async () => {
  const state = harness();
  const pending = state.controller.navigate(address('accounts'));
  await tick();
  await state.pop(address('budget') + '#assigned-total');
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(0); await pending;
  await state.pop(address('budget'));
  assert.equal(state.requests.length, 1);
  assert.equal(state.content(), state.original);
  assert.equal(state.histories.length, 0);
  assert.equal(state.location.href, address('budget'));
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});

test('selector cancellation runs once even when the old response later resolves', async () => {
  let canceled = 0;
  const state = harness();
  const pending = state.controller.navigate(address('insights', '2026-11'), {onCancel: () => {canceled += 1;}});
  await tick(); state.controller.cancelPending();
  assert.equal(canceled, 1);
  state.respond(0); await pending;
  assert.equal(canceled, 1);
  assert.equal(state.replacements.length, 0);
  assert.equal(state.controller.pendingDestination(), null);
  state.controller.dispose();
  assert.equal(canceled, 1);
});

test('network failure falls back to the addressed server page without disposing the current editor', async () => {
  let releases = 0;
  const state = harness({beforeReplace: () => {releases += 1; return true;}});
  const target = address('insights');
  const pending = state.controller.navigate(target);
  await tick(); state.requests[0].reject(new Error('Synthetic network interruption.')); await pending;
  assert.equal(releases, 0);
  assert.equal(state.content(), state.original);
  assert.deepEqual(state.fallbacks, [['assign', target]]);
  assert.equal(state.controller.pendingDestination(), null);
  assert.equal(state.status.hidden, true);
  state.controller.dispose();
});

for (const change of [
  {header: 'X-WHAB-Transactions-Fragment'}, {marker: 'unknown'}, {fragment: 'full-document'}, {ok: false},
  {url: address('insights')}, {override: {budgetId: 'other-budget'}},
  {override: {apiPrefix: '/budgets/other-budget/api'}}, {override: {workspace: 'demo'}},
  {omit: ['workspace']},
  {override: {budgetUrl: '/budgets/other-budget'}},
  {override: {page: 'budget'}}, {override: {monthToken: '2026-09'}},
  {override: {currency: 'EUR'}}, {override: {canonicalUrl: address('accounts').replace('/household/', '/other/')}},
  {override: {canonicalUrl: address('accounts', 'current-month', '?category=unrequested')}},
  {override: {pageFragments: false}},
  {assets: ''}, {assets: 'other-plugin-assets'}, {options: false}, {accounts: false},
  {missingOption: '[data-page-account-options]'}, {missingOption: '[data-page-category-options]'},
  {missingOption: '[data-page-group-options]'},
]) test(`invalid generic fragments never dispose or mount a page: ${JSON.stringify(change)}`, async () => {
  let releases = 0;
  const state = harness({beforeReplace: () => {releases += 1; return true;}});
  const target = address('accounts');
  const pending = state.controller.navigate(target);
  await tick(); state.respond(0, change); await pending;
  assert.equal(state.content(), state.original);
  assert.equal(releases, 0);
  assert.equal(state.mounts.length, 0);
  assert.equal(state.histories.length, 0);
  assert.deepEqual(state.fallbacks, [['assign', target]]);
  state.controller.dispose();
});

test('register summaries and generic page changes share one epoch', async () => {
  const state = harness({page: 'transactions'});
  const summary = state.controller.navigate(address('transactions'), {refresh: true, summary: true});
  await tick();
  const next = state.controller.navigate(address('budget'));
  await tick();
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(1); await next;
  state.respond(0); assert.equal(await summary, false);
  assert.equal(state.replacements.length, 1);
  assert.equal(state.runtime.getContext().page, 'budget');
  assert.deepEqual(state.fallbacks, []);
  state.controller.dispose();
});
