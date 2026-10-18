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

export function initAccountNavigation({onReplace = () => {}, flush = async () => true, notify = () => {}} = {}) {
  let displayed = new URL(location.href);
  let request = 0;
  let controller = null;
  let loadingContent = null;
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

  function syncNavigation(target, context) {
    const selected = context.selectedAccountId || target.searchParams.get('account');
    const nextRoute = parsePageRoute(target);
    const accountSlugs = context.accountSlugs || {};
    for (const link of document.querySelectorAll('.sidebar a[href]')) {
      const url = new URL(link.href, target);
      const source = parsePageRoute(url);
      if (!source || url.origin !== target.origin || !sameBudget(source.budget, nextRoute.budget, context) || source.currency !== nextRoute.currency) continue;
      const accountId = link.dataset?.accountId || url.searchParams.get('account') || Object.keys(accountSlugs).find(id => accountSlugs[id] === source.accountSlug);
      const accountSlug = accountSlugs[accountId] || source.accountSlug;
      const accountPath = source.page === 'transactions' && accountSlug ? `${accountSlug}/` : '';
      url.pathname = `${context.budgetUrl || `/budgets/${nextRoute.budget}`}/${accountPath}${source.page}/${nextRoute.month}/${source.currency}`;
      if (accountSlug) url.searchParams.delete('account');
      link.href = url.href;
      if (link.classList.contains('sidebar-account')) {
        if (selected && accountId === selected) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
      } else if (link.classList.contains('nav-item')) {
        const active = source.page === 'transactions';
        link.classList.toggle('active', active);
        if (active) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
      }
    }
    const breadcrumb = document.querySelector('.breadcrumb > span');
    if (breadcrumb) breadcrumb.textContent = 'Transactions';
  }

  async function navigate(value, {history: action = 'push', focus = 'heading', refresh = false} = {}) {
    const target = new URL(value, location.href);
    if (refresh) target.hash = '';
    if (disposed || !isAccountDestination(target, displayed, getContext())) return false;
    if (!refresh && target.href === displayed.href) {
      request += 1;
      controller?.abort();
      controller = null;
      busy(false);
      return true;
    }
    const revision = ++request;
    controller?.abort();
    controller = null;
    busy(false);
    try {
      if (!(await flush())) throw new Error('Save the transaction edit before changing accounts.');
    } catch (failure) {
      if (revision !== request || disposed) return true;
      if (action === 'none') window.history.replaceState(window.history.state, '', displayed.href);
      notify(failure.message || 'Save the transaction edit before continuing.');
      return true;
    }
    if (revision !== request || disposed) return true;
    controller = new AbortController();
    const sidebar = document.querySelector('.sidebar');
    const position = sidebar && {top: sidebar.scrollTop, left: sidebar.scrollLeft};
    busy(true);
    try {
      const response = await fetch(target.href, {
        headers: {'X-WHAB-Transactions-Fragment': '1'},
        cache: 'no-store', signal: controller.signal,
      });
      if (!response.ok || response.headers.get('X-WHAB-Transactions-Fragment') !== '1' ||
          response.url && new URL(response.url).href !== target.href) throw new Error('Register fragment unavailable.');
      const html = await response.text();
      if (revision !== request || disposed) return true;
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const fresh = parsed.querySelector('.page-content[data-transactions-fragment]');
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
          actual.currency !== expected.currency ||
          knownAccount && context.selectedAccountId !== undefined && context.selectedAccountId !== knownAccount) throw new Error('Register context changed.');
      const current = document.querySelector('main > .page-content');
      if (!current) throw new Error('Register container unavailable.');
      const accountList = refresh && document.querySelector('.sidebar-accounts');
      const accountTemplate = refresh && parsed.querySelector('template[data-account-list]');
      if (refresh && accountList && !accountTemplate?.content) throw new Error('Account list unavailable.');
      current.replaceWith(fresh);
      if (accountList) accountList.replaceChildren(accountTemplate.content.cloneNode(true));
      updateContext(context);
      displayed = canonical;
      syncNavigation(canonical, context);
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
      if (revision !== request || disposed || failure.name === 'AbortError') return true;
      // The normal server page is the fallback; no financial values are cached.
      if (action === 'none') location.replace(target.href);
      else location.assign(target.href);
    } finally {
      if (revision === request && !disposed) {
        controller = null;
        busy(false);
      }
    }
    return true;
  }

  function click(event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a.sidebar-account[href]');
    if (!link || link.hasAttribute('download') || link.target && link.target !== '_self') return;
    const target = new URL(link.href, location.href);
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
    dispose() {
      disposed = true;
      request += 1;
      controller?.abort();
      busy(false);
      status.remove();
      document.removeEventListener('click', click);
      window.removeEventListener('popstate', popstate);
    },
  };
}
