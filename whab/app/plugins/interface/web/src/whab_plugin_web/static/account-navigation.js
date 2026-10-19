/* Keep the account list in place while fetching the server-rendered register. */
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
  let disposed = false;
  const initialContext = getContext();
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

  function busy(active) {
    loadingContent?.removeAttribute('aria-busy');
    loadingContent = active ? document.querySelector('main > .page-content') : null;
    loadingContent?.setAttribute('aria-busy', 'true');
    status.textContent = active ? 'Loading transactions…' : '';
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
    const selected = context.selectedAccountId || target.searchParams.get('account');
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
        const active = source.page === 'transactions';
        link.classList.toggle('active', active);
        if (active) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
        if (active) syncReviewCount(link, countTemplate);
      }
    }
    const breadcrumb = !summary && document.querySelector('.breadcrumb > span');
    if (breadcrumb) breadcrumb.textContent = 'Transactions';
  }

  async function navigate(value, {history: action = 'push', focus = 'heading', refresh = false, summary = false} = {}) {
    const target = new URL(value, location.href);
    const displayedTarget = new URL(displayed);
    displayedTarget.hash = '';
    if (refresh) target.hash = '';
    if (disposed || !isAccountDestination(target, displayed, getContext())) return false;
    // A count refresh belongs to the displayed register and returns false if canceled.
    if (summary && (!refresh || target.href !== displayedTarget.href)) return false;
    if (!refresh && target.href === displayed.href) {
      request += 1;
      controller?.abort();
      controller = null;
      pendingTarget = null;
      busy(false);
      return true;
    }
    const revision = ++request;
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
      notify(failure.message || 'Save the transaction edit before continuing.');
      return !summary;
    }
    if (revision !== request || disposed) return !summary;
    controller = new AbortController();
    const sidebar = document.querySelector('.sidebar');
    const position = sidebar && {top: sidebar.scrollTop, left: sidebar.scrollLeft};
    if (!summary) busy(true);
    try {
      const marker = summary ? 'summary' : '1';
      const response = await fetch(target.href, {
        headers: {'X-WHAB-Transactions-Fragment': marker,
          ...(!refresh && !summary ? {'X-WHAB-Transactions-Sidebar': 'omit'} : {})},
        cache: 'no-store', signal: controller.signal,
      });
      if (!response.ok || response.headers.get('X-WHAB-Transactions-Fragment') !== marker ||
          response.url && new URL(response.url).href !== target.href) throw new Error('Register fragment unavailable.');
      const html = await response.text();
      if (revision !== request || disposed) return !summary;
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const fresh = parsed.querySelector(summary ? '[data-transactions-summary]' : '.page-content[data-transactions-fragment]');
      const context = JSON.parse(fresh?.dataset.pageContext || 'null');
      const expected = parsePageRoute(target);
      const previous = getContext();
      const canonical = new URL(context?.canonicalUrl || target.href, target);
      const actual = parsePageRoute(canonical);
      const knownAccount = target.searchParams.get('account') || Object.keys(previous.accountSlugs || {}).find(id => previous.accountSlugs[id] === expected.accountSlug);
      if (!fresh || !context || !actual || !isAccountDestination(canonical, target, context) ||
          context.budgetUrl !== `/budgets/${actual.budget}` ||
          previous.apiPrefix && context.apiPrefix !== previous.apiPrefix ||
          previous.budgetId && context.budgetId !== previous.budgetId ||
          context.page !== 'transactions' || context.monthToken !== expected.month ||
          actual.month !== expected.month || context.currency.toLowerCase() !== expected.currency ||
          actual.currency !== expected.currency || !matchesTransactionFilters(target, context) ||
          knownAccount && context.selectedAccountId !== undefined && context.selectedAccountId !== knownAccount) throw new Error('Register context changed.');
      const countTemplate = fresh.querySelector('template[data-transaction-navigation-count]');
      if (summary) {
        if (!countTemplate?.content || canonical.href !== displayedTarget.href) throw new Error('Register summary changed.');
        syncNavigation(canonical, context, countTemplate, true);
        return true;
      }
      const current = document.querySelector('main > .page-content');
      if (!current) throw new Error('Register container unavailable.');
      const accountList = refresh && document.querySelector('.sidebar-accounts');
      const accountTemplate = refresh && parsed.querySelector('template[data-account-list]');
      if (refresh && accountList && !accountTemplate?.content) throw new Error('Account list unavailable.');
      if (beforeReplace() === false) {
        if (action === 'none') window.history.replaceState(window.history.state, '', displayed.href);
        notify('Save the current edit before continuing.');
        return true;
      }
      current.replaceWith(fresh);
      if (accountList) accountList.replaceChildren(accountTemplate.content.cloneNode(true));
      updateContext(context);
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
    } catch (failure) {
      if (revision !== request || disposed || failure.name === 'AbortError') return !summary;
      // The normal server page is the fallback; no financial values are cached.
      if (action === 'none') location.replace(target.href);
      else location.assign(target.href);
    } finally {
      if (revision === request && !disposed) {
        controller = null;
        pendingTarget = null;
        busy(false);
      }
    }
    return true;
  }

  function click(event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a.sidebar-account[href]');
    if (!link || link.hasAttribute('download') || link.target && link.target !== '_self') return;
    const target = preserveTransactionFilters(new URL(link.href, location.href), new URL(location.href));
    if (!isAccountDestination(target, displayed, getContext())) return;
    event.preventDefault();
    void navigate(target);
  }
  async function popstate() {
    const target = new URL(location.href);
    if (target.href === displayed.href) return;
    if (!isAccountDestination(target, displayed, getContext())) {
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
      busy(false);
    },
    pendingDestination() { return pendingTarget ? new URL(pendingTarget) : null; },
    dispose() {
      disposed = true;
      request += 1;
      pendingTarget = null;
      controller?.abort();
      busy(false);
      status.remove();
      document.removeEventListener('click', click);
      window.removeEventListener('popstate', popstate);
    },
  };
}
