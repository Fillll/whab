/* Keep the app shell in place while fetching server-rendered views. */
import {getContext, parsePageRoute, updateContext} from './client.js';

function sameBudget(source, target, context) {
  if (source === target) return true;
  const aliases = new Set(context.budgetAliases || []);
  return aliases.has(source) && aliases.has(target);
}

export function isAccountDestination(target, current, context = {}) {
  const destination = parsePageRoute(target);
  const source = parsePageRoute(current);
  return Boolean(source && destination && target.origin === current.origin &&
    target.protocol === 'https:' && destination.page === 'transactions' &&
    sameBudget(source.budget, destination.budget, context) && source.currency === destination.currency && !target.hash);
}

export function isPageDestination(target, current, context = {}) {
  const destination = parsePageRoute(target);
  const source = parsePageRoute(current);
  return Boolean(context.pageFragments === true && source && destination &&
    target.origin === current.origin && target.protocol === 'https:' &&
    sameBudget(source.budget, destination.budget, context) &&
    source.currency === destination.currency && !target.hash && !target.username && !target.password);
}

export function preserveTransactionFilters(target, current) {
  if (parsePageRoute(current)?.page !== 'transactions') return target;
  target.searchParams.delete('tx_page');
  for (const key of ['period', 'start_date', 'end_date', 'review', 'all_dates', 'q']) {
    target.searchParams.delete(key);
    for (const value of current.searchParams.getAll(key)) target.searchParams.append(key, value);
  }
  return target;
}

export function matchesTransactionFilters(target, context) {
  const params = target.searchParams;
  if (context.transactionSearch !== undefined && context.transactionSearch !== (params.get('q') || '')) return false;
  if (context.transactionRequestedPage !== undefined && context.transactionRequestedPage !== (params.get('tx_page') ?? '1')) return false;
  const accounts = [...new Set(params.getAll('accounts'))].sort();
  if (accounts.length && (!Array.isArray(context.selectedAccountIds) ||
      JSON.stringify(accounts) !== JSON.stringify([...new Set(context.selectedAccountIds)].sort()))) return false;
  if (!accounts.length && !params.has('account') && !parsePageRoute(target)?.accountSlug && context.selectedAccountIds?.length) return false;
  if (params.has('period') && context.transactionPeriod !== params.get('period')) return false;
  const legacyAll = !params.has('period') && ['true', '1', 'on', 'yes'].includes((params.get('all_dates') || '').toLowerCase());
  if (legacyAll && context.transactionPeriod !== undefined &&
      (context.transactionPeriod !== 'all' || context.startDate || context.endDate)) return false;
  if (params.get('period') === 'custom' || !params.has('period') && !legacyAll && (params.has('start_date') || params.has('end_date'))) {
    if ((context.startDate || null) !== params.get('start_date') || (context.endDate || null) !== params.get('end_date')) return false;
  }
  return context.reviewFilter === undefined || context.reviewFilter === (params.get('review') || 'all');
}

export function initAccountNavigation({onReplace = () => {}, beforeReplace = () => true, flush = async () => true, notify = () => {}} = {}) {
  let displayed = new URL(location.href);
  let request = 0;
  let controller = null;
  let loadingContent = null;
  let pendingTarget = null;
  let pendingCancel = null;
  let disposed = false;
  const scrollPositions = new Map();
  const initialContext = getContext();
  if (initialContext.pageFragments === true) window.history.scrollRestoration = 'manual';
  if (initialContext.canonicalUrl) {
    const canonical = new URL(initialContext.canonicalUrl, displayed);
    const currentRoute = parsePageRoute(displayed);
    const canonicalRoute = parsePageRoute(canonical);
    if (currentRoute && canonicalRoute && canonical.origin === displayed.origin &&
        sameBudget(currentRoute.budget, canonicalRoute.budget, initialContext) &&
        ['page', 'month', 'currency'].every(key => currentRoute[key] === canonicalRoute[key])) {
      canonical.hash = displayed.hash;
      if (canonical.href !== displayed.href) window.history.replaceState(window.history.state, '', canonical.href);
      displayed = canonical;
    }
  }
  const status = document.createElement('p');
  status.className = 'transaction-navigation-status';
  status.setAttribute('role', 'status');
  status.hidden = true;
  document.body.append(status);

  function busy(active, label = 'transactions') {
    loadingContent?.removeAttribute('aria-busy');
    loadingContent = active ? document.querySelector('main > .page-content') : null;
    loadingContent?.setAttribute('aria-busy', 'true');
    status.textContent = active ? `Loading ${label}…` : '';
    status.hidden = !active;
  }

  function syncReviewCount(link, countTemplate) {
    if (!countTemplate?.content) return;
    const current = link.querySelector('.nav-count');
    const fresh = countTemplate.content.querySelector('.nav-count');
    if (fresh && current) current.replaceWith(fresh.cloneNode(true));
    else if (fresh) link.append(fresh.cloneNode(true));
    else current?.remove();
  }

  function syncNavigation(target, context, countTemplate, summary = false) {
    const selected = context.page === 'transactions' && (context.selectedAccountId || target.searchParams.get('account'));
    const nextRoute = parsePageRoute(target);
    const accountSlugs = context.accountSlugs || {};
    for (const link of document.querySelectorAll('.sidebar a[href]')) {
      const url = new URL(link.href, target);
      const source = parsePageRoute(url);
      if (!source || url.origin !== target.origin || !sameBudget(source.budget, nextRoute.budget, context) || source.currency !== nextRoute.currency) continue;
      if (summary) {
        if (source.page === 'transactions' && link.classList.contains('nav-item')) syncReviewCount(link, countTemplate);
        continue;
      }
      const accountId = link.dataset?.accountId || url.searchParams.get('account') || Object.keys(accountSlugs).find(id => accountSlugs[id] === source.accountSlug);
      const accountSlug = accountSlugs[accountId] || source.accountSlug;
      const accountPath = source.page === 'transactions' && accountSlug ? `${accountSlug}/` : '';
      url.pathname = `${context.budgetUrl || `/budgets/${nextRoute.budget}`}/${accountPath}${source.page}/${nextRoute.month}/${source.currency}`;
      if (accountSlug) url.searchParams.delete('account');
      link.href = url.href;
      if (link.classList.contains('sidebar-account')) {
        preserveTransactionFilters(url, target);
        link.href = url.href;
        if (selected && accountId === selected) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
      } else if (link.classList.contains('nav-item')) {
        const active = source.page === nextRoute.page;
        link.classList.toggle('active', active);
        if (active) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
        if (source.page === 'transactions') syncReviewCount(link, countTemplate);
      }
    }
    const breadcrumb = !summary && document.querySelector('.breadcrumb > span');
    if (breadcrumb) breadcrumb.textContent = nextRoute.page[0].toUpperCase() + nextRoute.page.slice(1);
  }

  function syncFormOptions(options, context) {
    for (const [target, source] of [
      ['#transaction-dialog select[name="accountId"]', '[data-page-account-options]'],
      ['#transaction-dialog select[name="categoryId"]', '[data-page-category-options]'],
      ['#category-groups', '[data-page-group-options]'],
    ]) {
      const current = document.querySelector(target);
      const fresh = options.content.querySelector(source);
      if (!current || !fresh) continue;
      const value = current.value;
      current.replaceChildren(...[...fresh.childNodes].map(node => node.cloneNode(true)));
      if (current.options && [...current.options].some(option => option.value === value)) current.value = value;
    }
    const budgetSwitch = document.querySelector('[data-budget-switch]');
    for (const name of ['personalUrl', 'demoUrl']) {
      if (!budgetSwitch?.dataset[name]) continue;
      const url = new URL(budgetSwitch.dataset[name], location.href);
      const route = parsePageRoute(url);
      if (!route) continue;
      url.pathname = `/budgets/${route.budget}/${context.page}/${context.monthToken}/${context.currency.toLowerCase()}`;
      url.search = '';
      url.hash = '';
      budgetSwitch.dataset[name] = url.href;
    }
  }

  async function navigate(value, {history: action = 'push', focus = 'heading', refresh = false, summary = false, onCancel = () => {}} = {}) {
    const target = new URL(value, location.href);
    const displayedTarget = new URL(displayed);
    displayedTarget.hash = '';
    if (refresh) target.hash = '';
    const expected = parsePageRoute(target);
    const generic = !summary && getContext().pageFragments === true &&
      (expected?.page !== 'transactions' || parsePageRoute(displayed)?.page !== 'transactions');
    if (disposed || !(generic ? isPageDestination(target, displayed, getContext()) : isAccountDestination(target, displayed, getContext()))) return false;
    // A count refresh belongs to the displayed register and returns false if canceled.
    if (summary && (!refresh || target.href !== displayedTarget.href)) return false;
    if (!refresh && target.href === displayed.href) {
      request += 1;
      controller?.abort();
      controller = null;
      pendingTarget = null;
      pendingCancel?.();
      pendingCancel = null;
      busy(false);
      return true;
    }
    pendingCancel?.();
    pendingCancel = onCancel;
    const revision = ++request;
    if (!summary && !refresh) {
      const register = document.querySelector('.transactions-scroll');
      scrollPositions.set(displayed.href, {left: window.scrollX || 0, top: window.scrollY || 0,
        ...(register ? {register: {top: register.scrollTop, left: register.scrollLeft,
          loaded: document.querySelectorAll('.transaction-row').length}} : {})});
      if (scrollPositions.size > 64) scrollPositions.delete(scrollPositions.keys().next().value);
    }
    pendingTarget = target.href;
    controller?.abort();
    controller = null;
    busy(false);
    try {
      if (!(await flush())) throw new Error('Save the transaction edit before changing accounts.');
    } catch (failure) {
      if (revision !== request || disposed) return !summary;
      if (action === 'none') window.history.replaceState(window.history.state, '', displayed.href);
      pendingTarget = null;
      pendingCancel?.();
      pendingCancel = null;
      notify(failure.message || 'Save the transaction edit before continuing.');
      return !summary;
    }
    if (revision !== request || disposed) return !summary;
    controller = new AbortController();
    const sidebar = document.querySelector('.sidebar');
    const position = sidebar && {top: sidebar.scrollTop, left: sidebar.scrollLeft};
    if (!summary) busy(true, generic ? expected.page : 'transactions');
    try {
      const marker = summary ? 'summary' : '1';
      const markerHeader = generic ? 'X-WHAB-Page-Fragment' : 'X-WHAB-Transactions-Fragment';
      const response = await fetch(target.href, {
        headers: {[markerHeader]: marker,
          ...(!generic && !refresh && !summary ? {'X-WHAB-Transactions-Sidebar': 'omit'} : {})},
        cache: 'no-store', signal: controller.signal,
      });
      if (!response.ok || response.headers.get(markerHeader) !== marker ||
          response.url && new URL(response.url).href !== target.href) throw new Error('Register fragment unavailable.');
      const html = await response.text();
      if (revision !== request || disposed) return !summary;
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const fresh = parsed.querySelector(summary ? '[data-transactions-summary]' : generic ? '.page-content[data-page-fragment]' : '.page-content[data-transactions-fragment]');
      const context = JSON.parse(fresh?.dataset.pageContext || 'null');
      const previous = getContext();
      const canonical = new URL(context?.canonicalUrl || target.href, target);
      const actual = parsePageRoute(canonical);
      const knownAccount = target.searchParams.get('account') || Object.keys(previous.accountSlugs || {}).find(id => previous.accountSlugs[id] === expected.accountSlug);
      if (!fresh || !context || !actual || !(generic ? isPageDestination(canonical, target, context) : isAccountDestination(canonical, target, context)) ||
          context.budgetUrl !== `/budgets/${actual.budget}` ||
          previous.apiPrefix && context.apiPrefix !== previous.apiPrefix ||
          previous.budgetId && context.budgetId !== previous.budgetId ||
          previous.workspace && context.workspace !== previous.workspace ||
          previous.uiGeneration && context.uiGeneration !== previous.uiGeneration ||
          context.page !== expected.page || actual.page !== expected.page || context.monthToken !== expected.month ||
          actual.month !== expected.month || context.currency.toLowerCase() !== expected.currency ||
          actual.currency !== expected.currency || expected.page === 'transactions' && !matchesTransactionFilters(target, context) ||
          generic && (context.pageFragments !== true || !fresh.dataset.pageAssets ||
            expected.page !== 'transactions' && JSON.stringify([...canonical.searchParams].sort()) !== JSON.stringify([...target.searchParams].sort()) ||
            fresh.dataset.pageAssets !== document.querySelector('main')?.dataset.pageAssets) ||
          knownAccount && context.selectedAccountId !== undefined && context.selectedAccountId !== knownAccount) throw new Error('Register context changed.');
      const countTemplate = fresh.querySelector('template[data-transaction-navigation-count]');
      if (summary) {
        if (!countTemplate?.content || canonical.href !== displayedTarget.href) throw new Error('Register summary changed.');
        syncNavigation(canonical, context, countTemplate, true);
        return true;
      }
      const current = document.querySelector('main > .page-content');
      if (!current) throw new Error('Register container unavailable.');
      const accountList = (generic || refresh) && document.querySelector('.sidebar-accounts');
      const accountTemplate = (generic || refresh) && parsed.querySelector('template[data-account-list]');
      const options = generic && parsed.querySelector('template[data-page-options]');
      if (accountList && !accountTemplate?.content || generic && (!options?.content ||
          !['[data-page-account-options]', '[data-page-category-options]', '[data-page-group-options]']
            .every(selector => options.content.querySelector(selector)))) throw new Error('Page controls unavailable.');
      if (beforeReplace() === false) {
        if (action === 'none') window.history.replaceState(window.history.state, '', displayed.href);
        notify('Save the current edit before continuing.');
        pendingCancel?.();
        pendingCancel = null;
        return true;
      }
      current.replaceWith(fresh);
      if (accountList) accountList.replaceChildren(accountTemplate.content.cloneNode(true));
      if (generic) syncFormOptions(options, context);
      updateContext(context, generic);
      displayed = canonical;
      syncNavigation(canonical, context, countTemplate);
      if (action === 'push') window.history.pushState(window.history.state, '', canonical.href);
      else if (action === 'replace' || canonical.href !== location.href) window.history.replaceState(window.history.state, '', canonical.href);
      onReplace({content: fresh, url: canonical, context});
      if (sidebar && position) {
        sidebar.scrollTop = position.top;
        sidebar.scrollLeft = position.left;
      }
      const focusTarget = focus === 'heading' ? fresh.querySelector('h1') : focus && fresh.querySelector(focus);
      if (focusTarget) {
        if (focus === 'heading') focusTarget.setAttribute('tabindex', '-1');
        focusTarget.focus({preventScroll: true});
      }
      if (!refresh && getContext().pageFragments === true && (generic || action === 'none')) {
        const position = action === 'none' ? scrollPositions.get(canonical.href) : null;
        window.scrollTo?.({left: position?.left || 0, top: position?.top || 0, behavior: 'instant'});
        if (position?.register) fresh.querySelector('[data-transaction-register]')?.dispatchEvent(
          new CustomEvent('whab:restore-register', {detail: position.register}));
      }
    } catch (failure) {
      if (revision !== request || disposed || failure.name === 'AbortError') return !summary;
      // The normal server page is the fallback; no financial values are cached.
      if (action === 'none') location.replace(target.href);
      else location.assign(target.href);
    } finally {
      if (revision === request && !disposed) {
        controller = null;
        pendingTarget = null;
        pendingCancel = null;
        busy(false);
      }
    }
    return true;
  }

  function click(event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest(getContext().pageFragments === true ? 'a[href]' : 'a.sidebar-account[href]');
    if (!link || link.hasAttribute('download') || link.target && link.target !== '_self') return;
    const target = new URL(link.href, location.href);
    if (link.classList?.contains('sidebar-account')) preserveTransactionFilters(target, new URL(displayed));
    else if (getContext().pageFragments !== true) preserveTransactionFilters(target, new URL(displayed));
    if (!(isPageDestination(target, displayed, getContext()) || isAccountDestination(target, displayed, getContext()))) return;
    event.preventDefault();
    void navigate(target);
  }
  async function popstate() {
    const target = new URL(location.href);
    if (target.href === displayed.href) return;
    if (target.origin === displayed.origin && target.pathname === displayed.pathname && target.search === displayed.search) {
      request += 1;
      controller?.abort();
      controller = null;
      pendingTarget = null;
      pendingCancel?.();
      pendingCancel = null;
      busy(false);
      displayed = target;
      return;
    }
    if (!(isPageDestination(target, displayed, getContext()) || isAccountDestination(target, displayed, getContext()))) {
      location.reload();
      return;
    }
    await navigate(target, {history: 'none'});
  }
  document.addEventListener('click', click);
  window.addEventListener('popstate', popstate);
  return {
    navigate,
    cancelPending() {
      request += 1;
      controller?.abort();
      controller = null;
      if (pendingTarget && location.href !== displayed.href) window.history.replaceState(window.history.state, '', displayed.href);
      pendingTarget = null;
      pendingCancel?.();
      pendingCancel = null;
      busy(false);
    },
    pendingDestination() { return pendingTarget ? new URL(pendingTarget) : null; },
    dispose() {
      disposed = true;
      request += 1;
      pendingTarget = null;
      pendingCancel?.();
      pendingCancel = null;
      controller?.abort();
      busy(false);
      status.remove();
      document.removeEventListener('click', click);
      window.removeEventListener('popstate', popstate);
    },
  };
}
