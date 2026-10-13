/* Plaid Link controls. Legacy OAuth storage keys deliberately remain stable. */
// A provider must not depend on the selected visual plugin's JavaScript modules.
async function api(path, body, method = 'POST') {
  const response = await fetch(`/api${path}`, {
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


if (location.search.includes('oauth_state_id')) {
  const token = sessionStorage.getItem('ihab-link-token');
  if (token) connect(sessionStorage.getItem('ihab-link-item') || undefined, token);
  else notify('The bank connection session expired. Open Accounts and connect again.');
}

const previousNotice = sessionStorage.getItem('ihab-notice');
if (previousNotice) {
  sessionStorage.removeItem('ihab-notice');
  notify(previousNotice);
}
