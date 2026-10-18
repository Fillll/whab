/* Shared local HTTP and feedback helpers. No provider integration runs here. */

const context = JSON.parse(document.getElementById('whab-context').textContent);
const toast = document.getElementById('toast');

export function updateContext(next) {
  Object.assign(context, next);
  document.getElementById('whab-context').textContent = JSON.stringify(context);
}

export function getContext() {
  return context;
}

export function parsePageRoute(url) {
  const match = url.pathname.match(/^\/budgets\/([^/]+)\/(?:([^/]+)\/)?(budget|transactions|insights|accounts)\/(current-month|\d{4}-\d{2})\/([a-z]{3})$/);
  if (!match || match[2] && match[3] !== 'transactions') return null;
  return {budget: match[1], accountSlug: match[2] || null, page: match[3], month: match[4], currency: match[5]};
}

export function apiUrl(path) {
  return `${context.apiPrefix}${path}`;
}

export function pageUrl(overrides = {}) {
  const url = new URL(location.href);
  const page = overrides.page ?? context.page;
  const month = overrides.month ?? context.monthToken;
  const currency = overrides.currency ?? context.currency;
  const current = parsePageRoute(url);
  const accountSlugs = context.accountSlugs || {};
  const oldAccount = context.selectedAccountId ?? url.searchParams.get('account') ??
    Object.keys(accountSlugs).find(id => accountSlugs[id] === current?.accountSlug);
  let accounts = 'accounts' in overrides ? overrides.accounts :
    'account' in overrides ? (overrides.account && overrides.account !== 'all' ? [overrides.account] : []) :
    context.selectedAccountIds ?? (url.searchParams.has('accounts') ? url.searchParams.getAll('accounts') : oldAccount ? [oldAccount] : []);
  accounts = [...new Set(accounts || [])];
  if (page !== 'transactions' || 'currency' in overrides) accounts = [];
  const accountId = accounts.length === 1 ? accounts[0] : null;
  const accountSlug = accountId && accountSlugs[accountId];
  const accountPath = accountSlug ? `${accountSlug}/` : '';
  url.pathname = `${context.budgetUrl}/${accountPath}${page}/${month}/${currency}`.toLowerCase();
  url.searchParams.delete('account');
  url.searchParams.delete('accounts');
  const clearPeriod = () => ['all_dates', 'period', 'start_date', 'end_date'].forEach(key => url.searchParams.delete(key));
  if (page !== 'transactions') {
    clearPeriod();
    url.searchParams.delete('review');
  } else {
    if ('month' in overrides && !('period' in overrides)) clearPeriod();
    if (accountId && !accountSlug) url.searchParams.set('account', accountId);
    if (accounts.length > 1) accounts.forEach(id => url.searchParams.append('accounts', id));
    if ('allDates' in overrides) {
      clearPeriod();
      if (overrides.allDates) url.searchParams.set('all_dates', 'true');
    }
    if ('period' in overrides) {
      clearPeriod();
      url.searchParams.set('period', overrides.period);
    }
    for (const [property, query] of [['startDate', 'start_date'], ['endDate', 'end_date']]) {
      if (property in overrides) {
        if (overrides[property] && (!overrides.period || overrides.period === 'custom')) url.searchParams.set(query, overrides[property]);
        else url.searchParams.delete(query);
      }
    }
    if ('review' in overrides) {
      if (overrides.review === 'all') url.searchParams.delete('review');
      else url.searchParams.set('review', overrides.review);
    }
  }
  url.hash = '';
  return url;
}
let toastTimer;

export function notify(message) {
  toast.querySelector('span').textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 6000);
}

export async function api(path, body, method = 'POST') {
  const response = await fetch(apiUrl(path), {
    method: body === undefined ? 'GET' : method,
    ...(body === undefined ? {} : {
      body: JSON.stringify(body)
    }),
    headers: {
      'Content-Type': 'application/json'
    },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}
