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
  let accountId = 'account' in overrides ? overrides.account :
    context.selectedAccountId ?? url.searchParams.get('account') ??
    Object.keys(accountSlugs).find(id => accountSlugs[id] === current?.accountSlug);
  if (page !== 'transactions' || 'currency' in overrides || accountId === 'all') accountId = null;
  const accountSlug = accountId && accountSlugs[accountId];
  const accountPath = accountSlug ? `${accountSlug}/` : '';
  url.pathname = `${context.budgetUrl}/${accountPath}${page}/${month}/${currency}`.toLowerCase();
  url.searchParams.delete('account');
  if (page !== 'transactions') {
    url.searchParams.delete('account');
    url.searchParams.delete('all_dates');
  } else {
    if ('month' in overrides) url.searchParams.delete('all_dates');
    if (accountId && !accountSlug) url.searchParams.set('account', accountId);
    if ('allDates' in overrides) {
      if (overrides.allDates) url.searchParams.set('all_dates', 'true');
      else url.searchParams.delete('all_dates');
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
