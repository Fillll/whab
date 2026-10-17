/* WHAB Plaid Link controls. */
// A provider must not depend on the selected visual plugin's JavaScript modules.
const budgetUrl = location.pathname.split('/').slice(0, 3).join('/');
const apiPrefix = `${budgetUrl}/api`;

function accountsUrl() {
  const url = new URL(location.href);
  const segments = url.pathname.split('/');
  segments[3] = 'accounts';
  url.pathname = segments.join('/');
  url.search = '';
  url.hash = '';
  return url;
}
async function api(path, body, method = 'POST') {
  const response = await fetch(`${apiPrefix}${path}`, {
    method: body === undefined ? 'GET' : method,
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    headers: {'Content-Type': 'application/json'},
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}

function notify(message) {
  const event = new CustomEvent('whab:notice', {detail: {message}, cancelable: true});
  if (document.dispatchEvent(event)) window.alert(message);
}

for (const button of document.querySelectorAll('[data-plaid-close-dialog]')) {
  button.addEventListener('click', () => button.closest('dialog').close());
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
  const buttons = document.querySelectorAll('[data-plaid-connect]');
  buttons.forEach(button => {
    button.disabled = true;
  });
  const clearSession = () => {
    try {
      for (const key of ['whab-link-token', 'whab-link-item', 'whab-link-budget', 'whab-link-return-url', 'whab-link-redirect-uri']) sessionStorage.removeItem(key);
    } catch { /* Storage may be unavailable. */ }
  };
  const unlock = () => {
    linking = false;
    buttons.forEach(button => {
      button.disabled = false;
    });
  };
  try {
    let receivedRedirectUri;
    if (resumeToken) {
      if (sessionStorage.getItem('whab-link-budget') !== budgetUrl) {
        throw new Error('This bank connection belongs to a different budget. Open its Accounts page and connect again.');
      }
      receivedRedirectUri = sessionStorage.getItem('whab-link-redirect-uri') || location.href;
      const redirect = new URL(receivedRedirectUri);
      if (redirect.origin !== location.origin || !redirect.searchParams.has('oauth_state_id') || redirect.hash) {
        throw new Error('The bank connection session expired. Open Accounts and connect again.');
      }
    } else {
      sessionStorage.setItem('whab-link-budget', budgetUrl);
      sessionStorage.setItem('whab-link-return-url', accountsUrl().pathname);
      sessionStorage.removeItem('whab-link-redirect-uri');
    }
    const token = resumeToken || (await api('/plaid/link-token', itemId ? {
      itemId
    } : {})).link_token;
    sessionStorage.setItem('whab-link-token', token);
    if (itemId) sessionStorage.setItem('whab-link-item', itemId);
    else sessionStorage.removeItem('whab-link-item');
    await loadPlaid();
    const handler = window.Plaid.create({
      token,
      ...(resumeToken ? {
        receivedRedirectUri
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
          location.assign(accountsUrl());
        } catch (failure) {
          clearSession();
          sessionStorage.setItem('whab-notice', failure.message);
          location.assign(accountsUrl());
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
for (const button of document.querySelectorAll('[data-plaid-connect]')) button.addEventListener('click', () => connect(button.dataset.plaidConnect || undefined));
for (const button of document.querySelectorAll('[data-plaid-sync]')) button.addEventListener('click', async () => {
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
for (const button of document.querySelectorAll('[data-plaid-disconnect]')) button.addEventListener('click', () => {
  disconnectId = button.dataset.plaidDisconnect;
  document.querySelector('#plaid-disconnect-dialog h2').textContent = `Disconnect ${button.dataset.institution}?`;
  document.getElementById('plaid-disconnect-dialog').showModal();
});
document.getElementById('plaid-confirm-disconnect')?.addEventListener('click', async event => {
  event.target.disabled = true;
  try {
    await api(`/plaid/items/${encodeURIComponent(disconnectId)}`, {}, 'DELETE');
    location.reload();
  } catch (failure) {
    notify(failure.message);
    event.target.disabled = false;
  }
});


if (new URL(location.href).searchParams.has('oauth_state_id')) {
  const token = sessionStorage.getItem('whab-link-token');
  if (token) connect(sessionStorage.getItem('whab-link-item') || undefined, token);
  else notify('The bank connection session expired. Open Accounts and connect again.');
}

const previousNotice = sessionStorage.getItem('whab-notice');
if (previousNotice) {
  sessionStorage.removeItem('whab-notice');
  notify(previousNotice);
}
