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

function badge(text) {
  return {...element(), textContent: text, cloneNode() {return badge(this.textContent);}};
}

function harness({flush = async () => true, beforeReplace = () => true, initialContext = {}, href = first} = {}) {
  const requests = [], replacements = [], histories = [], fallbacks = [], notices = [];
  const listeners = {};
  const navigationLinks = [];
  let nextBadge, nextAccountList = true;
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
    querySelectorAll: () => navigationLinks,
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
        const countTemplate = nextBadge === undefined ? null : {content: {querySelector: () => nextBadge === null ? null : badge(nextBadge)}};
        fresh.querySelector = selector => selector === 'h1' ? heading : selector === 'template[data-transaction-navigation-count]' ? countTemplate : null;
        fresh.heading = heading;
        return {querySelector: selector => ['.page-content[data-transactions-fragment]', '[data-transactions-summary]'].includes(selector) ? fresh :
          selector === 'template[data-account-list]' && nextAccountList ? {content: {cloneNode: () => ({refreshedAccountList: true})}} : null};
      }
    },
  };
  function track(node) {node.replaceWith = fresh => {replacements.push(fresh); content = fresh; track(fresh);};}
  track(content);
  vm.runInNewContext(clientSource.replaceAll('export function ', 'function ')
    .replaceAll('export async function ', 'async function '), context);
  vm.runInNewContext(source.replace(/^import .*$/m, '').replaceAll('export function ', 'function '), context);
  Object.defineProperty(context, 'updated', {get: () => context.getContext()});
  const navigator = context.initAccountNavigation({flush, beforeReplace,
    onReplace: () => {sidebar.scrollTop = 0;}, notify: message => notices.push(message)});
  function respond(index, {marker = '1', ok = true, override = {}, url, reviewBadge} = {}) {
    nextBadge = reviewBadge;
    const entry = requests[index];
    nextAccountList = entry.options.headers['X-WHAB-Transactions-Sidebar'] !== 'omit';
    const target = new URL(entry.url);
    const parts = context.parsePageRoute(target);
    const current = context.getContext();
    const selected = target.searchParams.get('account') || Object.keys(current.accountSlugs || {}).find(id => current.accountSlugs[id] === parts.accountSlug) || null;
    entry.resolve({ok, url: url ?? entry.url, headers: {get: () => marker},
      text: async () => JSON.stringify({...current, page: 'transactions', selectedAccountId: selected,
        canonicalUrl: target.href, monthToken: parts.month, currency: parts.currency.toUpperCase(), ...override})});
  }
  return {navigator, requests, replacements, histories, fallbacks, notices, sidebar, status, context,
    location, listeners, respond, accountListReplacements, content: () => content, navigationLinks};
}

test('ordinary account, search and page navigation omits the unused sidebar fragment', async () => {
  const state = harness();
  for (const target of [second, second + '&q=older', second + '&q=older&tx_page=2']) {
    const pending = state.navigator.navigate(target, {focus: false});
    await tick();
    const index = state.requests.length - 1;
    assert.deepEqual(JSON.parse(JSON.stringify(state.requests[index].options.headers)), {
      'X-WHAB-Transactions-Fragment': '1', 'X-WHAB-Transactions-Sidebar': 'omit',
    });
    const params = new URL(target).searchParams;
    state.respond(index, {override: {transactionSearch: params.get('q') || '', transactionRequestedPage: params.get('tx_page') || '1'}});
    await pending;
  }
  assert.equal(state.replacements.length, 3);
  assert.equal(state.accountListReplacements.length, 0);
  assert.equal(state.sidebar.scrollTop, 480);
  assert.equal(state.fallbacks.length, 0);
});

test('a validated replacement keeps the original page when its editor cannot be released', async () => {
  let allowed = false, releases = 0;
  const budget = 'https://localhost:5173/budgets/home/budget/2026-10/usd';
  const state = harness({href: budget, initialContext: {page: 'budget'},
    beforeReplace: () => {releases += 1; return allowed;}});
  const original = state.content();
  const refused = state.navigator.navigate(second);
  await tick(); state.respond(0); await refused;
  assert.equal(releases, 1);
  assert.equal(state.content(), original);
  assert.equal(state.replacements.length, 0);
  assert.equal(state.histories.length, 0);
  assert.equal(state.location.href, budget);
  assert.equal(state.navigator.pendingDestination(), null);
  assert.equal(state.status.hidden, true);
  assert.deepEqual(state.fallbacks, []);
  assert.deepEqual(state.notices, ['Save the current edit before continuing.']);
  allowed = true;
  const accepted = state.navigator.navigate(second);
  await tick(); state.respond(1); await accepted;
  assert.equal(releases, 2);
  assert.equal(state.replacements.length, 1);
  assert.deepEqual(state.histories, [['push', second]]);
});

test('a declined history replacement restores the visible budget address and leaves its editor mounted', async () => {
  const budget = 'https://localhost:5173/budgets/home/budget/2026-10/usd';
  const state = harness({href: budget, initialContext: {page: 'budget'}, beforeReplace: () => false});
  state.location.href = second;
  const pending = state.navigator.navigate(second, {history: 'none'});
  await tick(); state.respond(0); await pending;
  assert.deepEqual(state.histories, [['replace', budget]]);
  assert.equal(state.location.href, budget);
  assert.equal(state.replacements.length, 0);
  assert.equal(state.navigator.pendingDestination(), null);
  assert.deepEqual(state.fallbacks, []);
});

test('failed saves, rejected contexts and count-only summaries never release the current editor', async () => {
  let releases = 0;
  const beforeReplace = () => {releases += 1; return true;};
  const unsaved = harness({flush: async () => false, beforeReplace});
  await unsaved.navigator.navigate(second);
  assert.equal(unsaved.requests.length, 0);
  assert.equal(releases, 0);
  const changed = harness({beforeReplace});
  const invalid = changed.navigator.navigate(second);
  await tick(); changed.respond(0, {override: {currency: 'EUR'}}); await invalid;
  assert.equal(releases, 0);
  assert.equal(changed.replacements.length, 0);
  const summary = harness({beforeReplace});
  const refreshing = summary.navigator.navigate(first, {summary: true, refresh: true});
  await tick(); summary.respond(0, {marker: 'summary', reviewBadge: '2'}); await refreshing;
  assert.equal(releases, 0);
  assert.equal(summary.replacements.length, 0);
  assert.deepEqual(summary.fallbacks, []);
});

test('partial review refresh updates, removes and creates the server-rendered navigation badge', async () => {
  const state = harness();
  let current;
  function attach(next) {
    current = next;
    if (next) {
      next.replaceWith = value => attach(value);
      next.remove = () => {current = null;};
    }
  }
  attach(badge('3'));
  const link = {...element(), href: first, classList: {contains: name => name === 'nav-item', toggle() {}},
    querySelector: () => current, append: attach};
  state.navigationLinks.push(link);
  for (const count of ['2', null, '4']) {
    const refreshing = state.navigator.navigate(state.location.href, {refresh: true, history: 'replace'});
    await tick();
    state.respond(state.requests.length - 1, {reviewBadge: count});
    await refreshing;
    assert.equal(current?.textContent ?? null, count);
  }
  assert.equal(state.replacements.length, 3);
  assert.equal(state.fallbacks.length, 0);
  const olderFragment = state.navigator.navigate(second);
  await tick(); state.respond(3); await olderFragment;
  assert.equal(current.textContent, '4');
  state.navigationLinks.length = 0;
  const absentNavigation = state.navigator.navigate(third);
  await tick(); state.respond(4, {reviewBadge: '1'}); await absentNavigation;
  assert.equal(state.fallbacks.length, 0);
});

test('count-only review refresh preserves the register, focus, history and sidebar while updating its badge', async () => {
  const state = harness({initialContext: {apiPrefix: '/api', budgetId: 'default'}});
  let current = badge('3');
  const attach = next => {
    current = next;
    if (next) {
      next.replaceWith = attach;
      next.remove = () => {current = null;};
    }
  };
  attach(current);
  state.navigationLinks.push({...element(), href: first,
    classList: {contains: name => name === 'nav-item'}, querySelector: () => current, append: attach});
  const register = state.content(), context = state.context.updated;
  for (const count of ['2', null, '4']) {
    const pending = state.navigator.navigate(first, {refresh: true, summary: true, history: 'replace', focus: false});
    await tick();
    assert.equal(state.status.hidden, true);
    assert.equal(state.requests.at(-1).options.headers['X-WHAB-Transactions-Fragment'], 'summary');
    assert.equal(state.requests.at(-1).options.headers['X-WHAB-Transactions-Sidebar'], undefined);
    assert.equal(state.requests.at(-1).options.cache, 'no-store');
    state.respond(state.requests.length - 1, {marker: 'summary', reviewBadge: count});
    await pending;
    assert.equal(current?.textContent ?? null, count);
    assert.equal(state.navigator.pendingDestination(), null);
  }
  assert.equal(state.content(), register);
  assert.equal(state.context.updated, context);
  assert.equal(state.replacements.length, 0);
  assert.equal(state.accountListReplacements.length, 0);
  assert.equal(state.histories.length, 0);
  assert.equal(state.sidebar.scrollTop, 480);
  assert.equal(state.fallbacks.length, 0);
  state.navigationLinks.length = 0;
  const missing = state.navigator.navigate(first, {refresh: true, summary: true});
  await tick(); state.respond(3, {marker: 'summary', reviewBadge: '1'}); await missing;
  assert.equal(state.fallbacks.length, 0);
});

test('count summaries validate the same scope and cannot navigate or apply after cancellation', async () => {
  for (const response of [{marker: '1', reviewBadge: '1'}, {marker: 'summary'},
    {marker: 'summary', reviewBadge: '1', override: {transactionSearch: 'stale'}},
    {marker: 'summary', reviewBadge: '1', override: {transactionRequestedPage: '8'}},
    {marker: 'summary', reviewBadge: '1', override: {selectedAccountId: 'second'}},
    {marker: 'summary', reviewBadge: '1', override: {canonicalUrl: second}}]) {
    const state = harness();
    const pending = state.navigator.navigate(first, {refresh: true, summary: true});
    await tick(); state.respond(0, response); await pending;
    assert.equal(state.replacements.length, 0);
    assert.deepEqual(state.fallbacks, [['assign', first]]);
  }
  const state = harness();
  assert.equal(await state.navigator.navigate(second, {refresh: true, summary: true}), false);
  assert.equal(await state.navigator.navigate(first, {summary: true}), false);
  assert.equal(state.requests.length, 0);
  const stale = state.navigator.navigate(first, {refresh: true, summary: true});
  await tick();
  state.navigator.cancelPending();
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.respond(0, {marker: 'summary', reviewBadge: '1'}); await stale;
  assert.equal(state.replacements.length, 0);
  assert.equal(state.fallbacks.length, 0);
  assert.equal(state.histories.length, 0);
});

test('count summaries from a bookmarked anchor use a hashless request and preserve the visible bookmark', async () => {
  const href = first + '#transaction-42';
  const state = harness({href});
  const content = state.content();
  const pending = state.navigator.navigate(href, {refresh: true, summary: true, history: 'replace'});
  await tick();
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, first);
  state.respond(0, {marker: 'summary', reviewBadge: '2'});
  assert.equal(await pending, true);
  assert.equal(state.location.href, href);
  assert.equal(state.content(), content);
  assert.equal(state.histories.length, 0);
  assert.equal(state.replacements.length, 0);
  assert.equal(state.fallbacks.length, 0);
});

test('only same-budget HTTPS account-register destinations qualify for partial navigation', () => {
  const state = harness();
  const qualifies = value => state.context.isAccountDestination(new URL(value), new URL(first));
  assert.equal(qualifies(second), true);
  for (const value of [second.replace('/home/', '/other/'), second.replace('/usd', '/eur'),
    second.replace('https:', 'http:'), second.replace('localhost', 'untrusted.invalid'),
    second.replace('/transactions/', '/accounts/'), second + '#settings']) assert.equal(qualifies(value), false);
});

test('search and requested pagination must match the returned fragment, including clamped pages', () => {
  const state = harness();
  const target = new URL(first + '&q=older+memo&tx_page=999');
  const context = {transactionSearch: 'older memo', transactionRequestedPage: '999', transactionPage: 3};
  assert.equal(state.context.matchesTransactionFilters(target, context), true);
  assert.equal(state.context.matchesTransactionFilters(target, {...context, transactionSearch: 'stale'}), false);
  assert.equal(state.context.matchesTransactionFilters(target, {...context, transactionRequestedPage: '2'}), false);
  const account = state.context.preserveTransactionFilters(new URL(second + '&tx_page=5'), target);
  assert.equal(account.searchParams.get('q'), 'older memo');
  assert.equal(account.searchParams.has('tx_page'), false);
});

test('typing a newer search can cancel a pending request before its next debounce', async () => {
  const state = harness();
  const pending = state.navigator.navigate(first + '&q=old');
  await tick();
  state.navigator.cancelPending();
  assert.equal(state.requests[0].options.signal.aborted, true);
  assert.equal(state.navigator.pendingDestination(), null);
  state.respond(0);
  await pending;
  assert.equal(state.replacements.length, 0);
  assert.equal(state.status.hidden, true);
});

test('canceling a history request restores the address of the still-visible register', async () => {
  const state = harness();
  state.location.href = second;
  const pending = state.listeners.popstate();
  await tick();
  state.navigator.cancelPending();
  assert.equal(state.location.href, first);
  state.respond(0);
  await pending;
  assert.equal(state.replacements.length, 0);
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
  assert.equal(state.requests[0].options.headers['X-WHAB-Transactions-Fragment'], '1');
  assert.equal(state.requests[0].options.headers['X-WHAB-Transactions-Sidebar'], undefined);
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

test('sidebar account destinations retain date and review filters without inheriting the old account', () => {
  const state = harness();
  const current = new URL(first + '&period=custom&start_date=2026-09-10&end_date=2026-10-10&review=reviewed');
  const target = new URL(second + '&period=previous-month&review=needs-review');
  const preserved = state.context.preserveTransactionFilters(target, current);
  assert.equal(preserved.searchParams.get('account'), 'second');
  assert.equal(preserved.searchParams.get('period'), 'custom');
  assert.equal(preserved.searchParams.get('start_date'), '2026-09-10');
  assert.equal(preserved.searchParams.get('end_date'), '2026-10-10');
  assert.equal(preserved.searchParams.get('review'), 'reviewed');
  assert.equal(preserved.searchParams.get('all_dates'), 'true');
  const nonRegister = new URL(first.replace('/transactions/', '/accounts/'));
  const unchanged = new URL(second);
  const original = unchanged.href;
  assert.equal(state.context.preserveTransactionFilters(unchanged, nonRegister).href, original);
});

test('filter matching rejects stale multi-account, custom date and review fragments', () => {
  const state = harness();
  const target = new URL('https://localhost:5173/budgets/home/transactions/2026-10/usd?accounts=first&accounts=second&period=custom&start_date=2026-09-10&end_date=2026-10-10&review=reviewed');
  const expected = {selectedAccountIds: ['second', 'first'], transactionPeriod: 'custom',
    startDate: '2026-09-10', endDate: '2026-10-10', reviewFilter: 'reviewed'};
  assert.equal(state.context.matchesTransactionFilters(target, expected), true);
  for (const override of [
    {selectedAccountIds: ['first']}, {selectedAccountIds: ['first', 'third']},
    {transactionPeriod: 'previous-month'}, {startDate: '2026-09-11'}, {endDate: '2026-10-09'},
    {reviewFilter: 'needs-review'},
  ]) assert.equal(state.context.matchesTransactionFilters(target, {...expected, ...override}), false);
  const allAccounts = new URL(first.replace('?account=first&all_dates=true', ''));
  assert.equal(state.context.matchesTransactionFilters(allAccounts, {selectedAccountIds: ['first']}), false);
  assert.equal(state.context.matchesTransactionFilters(new URL(first), {}), true);
});

test('modern all-date fragments cannot silently return a bounded monthly register', () => {
  const state = harness();
  assert.equal(state.context.matchesTransactionFilters(new URL(first), {transactionPeriod: 'all'}), true);
  assert.equal(state.context.matchesTransactionFilters(new URL(first), {transactionPeriod: 'current-month'}), false);
});

test('mismatched filter fragments fall back without replacing the current budget content', async () => {
  for (const override of [
    {selectedAccountIds: ['first']}, {transactionPeriod: 'previous-month'},
    {startDate: '2026-09-11'}, {endDate: '2026-10-09'}, {reviewFilter: 'needs-review'},
  ]) {
    const state = harness({initialContext: {budgetId: 'private-home', apiPrefix: '/budgets/home/api'}});
    const target = new URL('https://localhost:5173/budgets/home/transactions/2026-10/usd?accounts=first&accounts=second&period=custom&start_date=2026-09-10&end_date=2026-10-10&review=reviewed');
    const pending = state.navigator.navigate(target);
    await tick();
    state.respond(0, {override: {selectedAccountIds: ['first', 'second'], transactionPeriod: 'custom',
      startDate: '2026-09-10', endDate: '2026-10-10', reviewFilter: 'reviewed', ...override}});
    await pending;
    assert.deepEqual(state.fallbacks, [['assign', target.href]]);
    assert.equal(state.replacements.length, 0);
    assert.equal(state.context.getContext().budgetId, 'private-home');
    assert.equal(state.context.getContext().apiPrefix, '/budgets/home/api');
  }
});

test('sidebar clicks preserve the active range and reject other budgets and origins', async () => {
  const href = first + '&period=custom&start_date=2026-09-10&end_date=2026-10-10&review=reviewed';
  const state = harness({href});
  function click(target) {
    const link = {href: target, hasAttribute: () => false, target: ''};
    const event = {button: 0, target: {closest: () => link}, preventDefault() {this.prevented = true;}};
    state.listeners.click(event);
    return event;
  }
  assert.equal(click(second.replace('/home/', '/another-budget/')).prevented, undefined);
  assert.equal(click(second.replace('localhost', 'external.invalid')).prevented, undefined);
  await tick();
  assert.equal(state.requests.length, 0);
  assert.equal(click(second).prevented, true);
  await tick();
  const fetched = new URL(state.requests[0].url);
  assert.equal(fetched.searchParams.get('account'), 'second');
  assert.equal(fetched.searchParams.get('period'), 'custom');
  assert.equal(fetched.searchParams.get('start_date'), '2026-09-10');
  assert.equal(fetched.searchParams.get('end_date'), '2026-10-10');
  assert.equal(fetched.searchParams.get('review'), 'reviewed');
  state.navigator.dispose();
  state.respond(0);
  await tick();
  assert.equal(state.replacements.length, 0);
});

test('the pending destination is transient, copied and retained until the newest request finishes', async () => {
  const state = harness();
  assert.equal(state.navigator.pendingDestination(), null);
  const earlier = state.navigator.navigate(second);
  assert.equal(state.navigator.pendingDestination().href, second);
  const copy = state.navigator.pendingDestination();
  copy.searchParams.set('review', 'reviewed');
  assert.equal(state.navigator.pendingDestination().searchParams.has('review'), false);
  await tick();
  const latest = state.navigator.navigate(third);
  await tick();
  state.respond(0);
  await earlier;
  assert.equal(state.navigator.pendingDestination().href, third);
  state.respond(1);
  await latest;
  assert.equal(state.navigator.pendingDestination(), null);
  const closing = state.navigator.navigate(second);
  await tick();
  state.navigator.dispose();
  assert.equal(state.navigator.pendingDestination(), null);
  state.respond(2);
  await closing;
  assert.equal(state.replacements.length, 1);
});

test('transaction navigation refuses a different visual generation before replacing content', async () => {
  const state = harness({initialContext: {uiGeneration: 'a'.repeat(64)}});
  const pending = state.navigator.navigate(second);
  await tick();
  state.respond(0, {override: {uiGeneration: 'b'.repeat(64)}});
  await pending;
  assert.equal(state.replacements.length, 0);
  assert.equal(state.context.getContext().uiGeneration, 'a'.repeat(64));
  assert.ok(state.fallbacks.length);
  state.navigator.dispose();
});
