/* Shared local HTTP and feedback helpers. No provider integration runs here. */

const context = JSON.parse(document.getElementById('whab-context').textContent);
const toast = document.getElementById('toast');

export function apiUrl(path) {
  return `${context.apiPrefix}${path}`;
}

export function pageUrl(overrides = {}) {
  const url = new URL(location.href);
  const page = overrides.page ?? context.page;
  const month = overrides.month ?? context.monthToken;
  const currency = overrides.currency ?? context.currency;
  url.pathname = `${context.budgetUrl}/${page}/${month}/${currency}`.toLowerCase();
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
