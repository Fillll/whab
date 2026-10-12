/* IHAB browser controls. Budgeting, persistence, and API integration live in Python. */
'use strict';

const context = JSON.parse(document.getElementById('ihab-context').textContent);
const toast = document.getElementById('toast');
let toastTimer;

function notify(message) {
  toast.querySelector('span').textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 6000);
}

async function api(path, body, method = 'POST') {
  const response = await fetch(`/api${path}`, {
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

function cents(value) {
  const clean = value.trim().replace(/[$,]/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) throw new Error('Enter a positive amount with up to two decimal places.');
  const [whole, fraction = ''] = clean.split('.');
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(result) || result > 1_000_000_000_000) throw new Error('That amount is too large.');
  return result;
}

for (const form of document.querySelectorAll('form[data-api]')) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.busy) return;
    const error = form.querySelector('.field-error');
    try {
      const body = {};
      for (const field of form.elements) {
        if (!field.name || field.disabled) continue;
        body[field.name] = field.hasAttribute('data-boolean') ? field.checked :
          field.hasAttribute('data-cents') ? cents(field.value) :
          field.hasAttribute('data-null') && !field.value ? null : field.value;
      }
      if (body._kind === 'income') body.amount = -body.amount;
      delete body._kind;
      form.dataset.busy = 'true';
      if (error) error.hidden = true;
      await api(form.dataset.api, body, form.dataset.method || 'POST');
      location.reload();
    } catch (failure) {
      if (error) {
        error.textContent = failure.message;
        error.hidden = false;
      } else notify(failure.message);
      delete form.dataset.busy;
    }
  });
  if (form.hasAttribute('data-auto')) form.addEventListener('change', () => form.requestSubmit());
}

for (const button of document.querySelectorAll('[data-dialog]')) button.addEventListener('click', () => document.getElementById(button.dataset.dialog).showModal());
for (const button of document.querySelectorAll('[data-close-dialog]')) button.addEventListener('click', () => button.closest('dialog').close());
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => {
  if (event.target === dialog) dialog.close();
});
document.querySelector('[data-dismiss-toast]').addEventListener('click', () => {
  toast.hidden = true;
});

for (const input of document.querySelectorAll('[data-navigation]')) input.addEventListener('change', () => {
  const url = new URL(location.href);
  url.hash = '';
  url.searchParams.set(input.dataset.navigation, input.value);
  if (input.dataset.navigation === 'workspace') url.searchParams.delete('category');
  location.assign(url);
});
for (const button of document.querySelectorAll('[data-toggle-group]')) button.addEventListener('click', () => {
  const expanded = button.getAttribute('aria-expanded') === 'true';
  button.setAttribute('aria-expanded', String(!expanded));
  button.querySelector('svg').classList.toggle('collapsed', expanded);
  for (const row of document.querySelectorAll('[data-group]'))
    if (row.dataset.group === button.dataset.toggleGroup) row.hidden = expanded;
});
for (const time of document.querySelectorAll('[data-local-time]')) time.textContent = new Date(time.dateTime).toLocaleString();

const search = document.getElementById('transaction-search');
const accountFilter = document.getElementById('account-filter');
const reviewFilter = document.getElementById('review-filter');

function filterTransactions() {
  let count = 0;
  for (const row of document.querySelectorAll('.transaction-row')) {
    row.hidden = !row.dataset.payee.includes(search.value.toLowerCase()) ||
      (accountFilter.value !== 'all' && row.dataset.account !== accountFilter.value) ||
      (reviewFilter.getAttribute('aria-pressed') === 'true' && row.dataset.reviewed === 'true');
    if (!row.hidden) count += 1;
  }
  document.getElementById('transaction-count').textContent = `${count} transactions`;
}
if (search) {
  search.addEventListener('input', filterTransactions);
  accountFilter.addEventListener('change', filterTransactions);
  reviewFilter.addEventListener('click', () => {
    const pressed = reviewFilter.getAttribute('aria-pressed') !== 'true';
    reviewFilter.setAttribute('aria-pressed', String(pressed));
    reviewFilter.classList.toggle('selected', pressed);
    filterTransactions();
  });
}

let plaidScript;
async function loadPlaid() {
  if (window.Plaid) return;
  if (!plaidScript) plaidScript = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js';
    script.onload = resolve;
    script.onerror = () => {
      script.remove();
      plaidScript = undefined;
      reject(new Error('Plaid could not load. Check your connection and retry.'));
    };
    document.head.appendChild(script);
  });
  await plaidScript;
  if (!window.Plaid) throw new Error('Plaid is unavailable.');
}

let linking = false;
async function connect(itemId, resumeToken) {
  if (linking) return;
  linking = true;
  const buttons = document.querySelectorAll('[data-connect]');
  buttons.forEach(button => {
    button.disabled = true;
  });
  const clearSession = () => {
    sessionStorage.removeItem('ihab-link-token');
    sessionStorage.removeItem('ihab-link-item');
  };
  const unlock = () => {
    linking = false;
    buttons.forEach(button => {
      button.disabled = false;
    });
  };
  try {
    const token = resumeToken || (await api('/plaid/link-token', itemId ? {
      itemId
    } : {})).link_token;
    sessionStorage.setItem('ihab-link-token', token);
    if (itemId) sessionStorage.setItem('ihab-link-item', itemId);
    else sessionStorage.removeItem('ihab-link-item');
    await loadPlaid();
    const handler = window.Plaid.create({
      token,
      ...(resumeToken ? {
        receivedRedirectUri: location.href
      } : {}),
      onSuccess: async (publicToken, metadata) => {
        try {
          if (!itemId) {
            const existing = await api('/workspace?workspace=personal');
            const duplicate = (metadata.accounts || []).some(candidate => candidate.mask && existing.accounts.some(account => !account.stale && account.source === 'plaid' && account.institution === metadata.institution?.name && account.mask === candidate.mask && account.name === candidate.name));
            if (duplicate) throw new Error('These accounts are already connected. Use Manage on the existing bank connection.');
            await api('/plaid/exchange', {
              publicToken,
              ...(metadata.institution ? {
                institutionId: metadata.institution.institution_id,
                institutionName: metadata.institution.name
              } : {})
            });
          }
          await api('/plaid/sync', itemId ? {
            itemId
          } : {});
          clearSession();
          location.assign('/?page=accounts&workspace=personal');
        } catch (failure) {
          clearSession();
          sessionStorage.setItem('ihab-notice', failure.message);
          location.assign('/?page=accounts&workspace=personal');
        } finally {
          setTimeout(() => handler.destroy(), 0);
        }
      },
      onExit: error => {
        clearSession();
        unlock();
        if (error) notify(error.display_message || 'The bank connection could not be completed. Please retry.');
        setTimeout(() => handler.destroy(), 0);
      },
    });
    handler.open();
  } catch (failure) {
    clearSession();
    unlock();
    notify(failure.message);
  }
}
for (const button of document.querySelectorAll('[data-connect]')) button.addEventListener('click', () => connect(button.dataset.connect || undefined));
for (const button of document.querySelectorAll('[data-sync]')) button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    await api('/plaid/sync', {});
    location.reload();
  } catch (failure) {
    notify(failure.message);
    button.disabled = false;
  }
});
let disconnectId;
for (const button of document.querySelectorAll('[data-disconnect]')) button.addEventListener('click', () => {
  disconnectId = button.dataset.disconnect;
  document.querySelector('#disconnect-dialog h2').textContent = `Disconnect ${button.dataset.institution}?`;
  document.getElementById('disconnect-dialog').showModal();
});
document.getElementById('confirm-disconnect').addEventListener('click', async event => {
  event.target.disabled = true;
  try {
    await api(`/plaid/items/${encodeURIComponent(disconnectId)}`, {}, 'DELETE');
    location.reload();
  } catch (failure) {
    notify(failure.message);
    event.target.disabled = false;
  }
});

if (location.search.includes('oauth_state_id')) {
  const token = sessionStorage.getItem('ihab-link-token');
  if (token) connect(sessionStorage.getItem('ihab-link-item') || undefined, token);
  else notify('The bank connection session expired. Open Accounts and connect again.');
} else if (['#budget', '#transactions', '#insights', '#accounts'].includes(location.hash)) {
  const url = new URL(location.href);
  url.searchParams.set('page', location.hash.slice(1));
  url.hash = '';
  location.replace(url);
}

const previousNotice = sessionStorage.getItem('ihab-notice');
if (previousNotice) {
  sessionStorage.removeItem('ihab-notice');
  notify(previousNotice);
}
