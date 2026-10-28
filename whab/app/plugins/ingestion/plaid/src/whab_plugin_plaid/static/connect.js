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
  url.hash = 'plugin-settings-plaid';
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

const sessionKeys = ['whab-link-token', 'whab-link-item', 'whab-link-budget', 'whab-link-return-url', 'whab-link-redirect-uri', 'whab-link-context'];
const stages = new Set(['ready', 'exchanging', 'exchange_uncertain', 'captured', 'applying', 'apply_uncertain', 'complete', 'cancelled', 'expired']);
const pendingMessage = 'This connection has an unfinished operation. Check its status; do not connect or exchange again.';
const expiredMessage = 'The bank connection session expired. Open Plaid plugin settings and connect again.';
let finishable = false;
function environment() {
  const value = document.querySelector('[data-bank-provider="plaid"]')?.dataset.bankEnvironment;
  if (!['sandbox', 'production'].includes(value)) throw new Error('The bank connection scope is unavailable. Refresh Accounts.');
  return value;
}
function status(value, context, begin = false) {
  const fields = ['flow_id', 'generation', 'kind', 'stage', 'deadline_ms', ...(begin ? ['link_token'] : [])];
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).length !== fields.length || fields.some(key => !Object.hasOwn(value, key)) ||
      typeof value.flow_id !== 'string' || !/^[a-f0-9]{32}$/.test(value.flow_id) ||
      !Number.isSafeInteger(value.generation) || value.generation < 1 ||
      !['link', 'update'].includes(value.kind) || !stages.has(value.stage) ||
      !Number.isSafeInteger(value.deadline_ms) || value.deadline_ms < 1 ||
      (begin && (value.stage !== 'ready' || typeof value.link_token !== 'string' || value.link_token.length < 1 || value.link_token.length > 4096)) ||
      (context && ['flow_id', 'generation', 'kind', 'deadline_ms'].some(key => value[key] !== context[key]))) {
    throw new Error('The bank returned an invalid connection status. Preserve this session and check its status.');
  }
  return value;
}
function storedContext() {
  const raw = sessionStorage.getItem('whab-link-context');
  if (!raw) return null;
  let context;
  try {context = JSON.parse(raw);} catch {throw new Error(expiredMessage);}
  if (!context || context.formatVersion !== 1 || context.provider !== 'plaid' ||
      typeof context.legacy !== 'boolean' || context.environment !== environment() ||
      sessionStorage.getItem('whab-link-budget') !== budgetUrl) {
    throw new Error('This bank connection belongs to a different budget or environment. Open its original Accounts page.');
  }
  if (!context.legacy) {
    status(Object.fromEntries(['flow_id', 'generation', 'kind', 'stage', 'deadline_ms'].map(key => [key, context[key]])));
    if (context.kind !== (sessionStorage.getItem('whab-link-item') ? 'update' : 'link')) throw new Error(expiredMessage);
  }
  if (!['ready', 'submitted'].includes(context.phase)) throw new Error(expiredMessage);
  return context;
}
function saveContext(context) {
  sessionStorage.setItem('whab-link-context', JSON.stringify(context));
  showContinuation();
}
function clearSession() {
  finishable = false;
  for (const key of sessionKeys) sessionStorage.removeItem(key);
  showContinuation();
}
function showContinuation() {
  const panel = document.querySelector('[data-plaid-continuation]');
  if (panel) panel.hidden = !sessionStorage.getItem('whab-link-context');
  const finish = document.querySelector('[data-plaid-finish-link]');
  if (finish) finish.hidden = !finishable;
}
async function refreshConnection() {
  // Read connection records separately; a completion response contains no Item or account data.
  await api('/plaid/items');
  location.assign(accountsUrl());
}
async function check(context) {
  const result = status(await api(`/plaid/continuations/${context.flow_id}`), context);
  finishable = ['captured', 'apply_uncertain'].includes(result.stage);
  saveContext({...context, stage: result.stage});
  if (['complete', 'cancelled', 'expired'].includes(result.stage)) clearSession();
  return result;
}
async function cancel(context) {
  if (context.legacy) {
    if (context.phase === 'submitted') throw new Error(pendingMessage);
    clearSession(); return;
  }
  const result = status(await api(`/plaid/continuations/${context.flow_id}`, {}, 'DELETE'), context);
  if (!['cancelled', 'expired', 'complete'].includes(result.stage)) throw new Error(pendingMessage);
  clearSession();
}
function settings(open) {
  document.dispatchEvent(new CustomEvent('whab:plugin-settings', {detail: {pluginId: 'plaid', open}}));
}
function reloadSettings() {location.hash = 'plugin-settings-plaid'; location.reload();}
let linking = false;
let activeRequests = 0;
async function connect(itemId, resumeToken) {
  if (linking || activeRequests) return;
  linking = true;
  const buttons = document.querySelectorAll('[data-plaid-connect]');
  buttons.forEach(button => {button.disabled = true;});
  const unlock = () => {
    linking = false;
    settings(true);
    buttons.forEach(button => {button.disabled = false;});
  };
  let context, handler, completing = false;
  try {
    context = storedContext();
    if (context) {
      if (context.legacy && context.phase === 'submitted') throw new Error(pendingMessage);
      const originalItem = sessionStorage.getItem('whab-link-item') || undefined;
      if (originalItem !== itemId) throw new Error('Finish or cancel the original bank connection first.');
      if (!context.legacy) {
        const current = await check(context);
        if (current.stage === 'complete') {await refreshConnection(); unlock(); return;}
        if (['cancelled', 'expired'].includes(current.stage)) throw new Error(expiredMessage);
        if (current.stage !== 'ready') throw new Error(pendingMessage);
        if (context.phase === 'submitted') throw new Error(pendingMessage);
      }
    } else if (resumeToken || sessionStorage.getItem('whab-link-token')) {
      // Old browser records have no durable correlation and cannot resume an exchange.
      clearSession();
      throw new Error(expiredMessage);
    }
    let receivedRedirectUri;
    if (resumeToken) {
      receivedRedirectUri = sessionStorage.getItem('whab-link-redirect-uri') || location.href;
      const redirect = new URL(receivedRedirectUri);
      if (redirect.origin !== location.origin || !redirect.searchParams.has('oauth_state_id') || redirect.hash) throw new Error(expiredMessage);
    }
    let token = sessionStorage.getItem('whab-link-token');
    if (!context) {
      const scope = {formatVersion: 1, provider: 'plaid', environment: environment(), phase: 'ready'};
      const result = await api('/plaid/link-token', itemId ? {itemId} : {});
      if (result && typeof result === 'object' && Object.keys(result).length === 1 && typeof result.link_token === 'string' && result.link_token.length >= 1 && result.link_token.length <= 4096) {
        context = {...scope, legacy: true};
      } else {
        const initial = status(result, null, true);
        if (initial.kind !== (itemId ? 'update' : 'link')) throw new Error('The bank returned the wrong connection kind.');
        context = {...scope, legacy: false, ...Object.fromEntries(['flow_id', 'generation', 'kind', 'stage', 'deadline_ms'].map(key => [key, initial[key]]))};
      }
      token = result.link_token;
      sessionStorage.setItem('whab-link-budget', budgetUrl);
      sessionStorage.setItem('whab-link-return-url', accountsUrl().pathname);
      sessionStorage.setItem('whab-link-token', token);
      if (itemId) sessionStorage.setItem('whab-link-item', itemId);
      else sessionStorage.removeItem('whab-link-item');
      sessionStorage.removeItem('whab-link-redirect-uri');
      saveContext(context);
    }
    if (!token || (!context.legacy && context.deadline_ms <= Date.now())) throw new Error(expiredMessage);
    await loadPlaid();
    handler = window.Plaid.create({
      token,
      ...(resumeToken ? {receivedRedirectUri} : {}),
      onSuccess: async (publicToken, metadata) => {
        if (completing) return;
        completing = true;
        activeRequests += 1;
        let submitted = false;
        try {
          if (!itemId) {
            const existing = await api('/workspace?workspace=personal');
            const duplicate = (metadata.accounts || []).some(candidate => candidate.mask && existing.accounts.some(account => !account.stale && account.source === 'plaid' && account.institution === metadata.institution?.name && account.mask === candidate.mask && account.name === candidate.name));
            if (duplicate) throw new Error('These accounts are already connected. Use Manage on the existing bank connection.');
          }
          if (!context.legacy || !itemId) {
            context = {...context, phase: 'submitted'}; saveContext(context);
            submitted = true;
            const result = await api('/plaid/exchange', {
              ...(!context.legacy ? {flowId: context.flow_id} : {}),
              ...(!itemId ? {publicToken, ...(metadata.institution ? {institutionId: metadata.institution.institution_id, institutionName: metadata.institution.name} : {})} : {}),
            });
            if (!context.legacy && status(result, context).stage !== 'complete') throw new Error(pendingMessage);
          }
          clearSession();
          await api('/plaid/sync', itemId ? {itemId} : {});
          await refreshConnection();
        } catch (failure) {
          if (submitted) {
            // A lost reply never authorizes another exchange. Status may prove completion.
            try {
              if (context.legacy) throw new Error(pendingMessage);
              const current = await check(context);
              if (current.stage === 'complete') {await refreshConnection(); return;}
            } catch { /* Retain the original intent when status is unavailable. */ }
            notify(pendingMessage);
          } else {
            try {await cancel(context);} catch { /* Keep an unconfirmed cancellation. */ }
            notify(failure.message);
          }
        } finally {
          activeRequests -= 1;
          unlock();
          setTimeout(() => handler.destroy(), 0);
        }
      },
      onExit: async error => {
        if (completing) return;
        completing = true;
        activeRequests += 1;
        try {await cancel(context);}
        catch {notify(pendingMessage);}
        finally {
          activeRequests -= 1;
          unlock();
          if (error) notify('The bank connection could not be completed. Check its status before retrying.');
          setTimeout(() => handler.destroy(), 0);
        }
      },
    });
    settings(false);
    handler.open();
  } catch (failure) {
    // A prepared continuation remains visible for explicit status/cancellation.
    unlock();
    notify(failure.message);
  }
}
let disconnectId;
const bound = new WeakSet();
function bindButtons(selector, callback) {
  for (const button of document.querySelectorAll(selector)) {
    if (bound.has(button)) continue;
    bound.add(button);
    button.addEventListener('click', event => callback(button, event));
  }
}
function bind() {
  bindButtons('[data-plaid-close-dialog]', button => document.getElementById('plaid-disconnect-dialog').hidden = true);
  bindButtons('[data-plaid-connect]', button => connect(button.dataset.plaidConnect || undefined));
  bindButtons('[data-plaid-check-link]', async () => {
    if (linking || activeRequests) return;
    activeRequests += 1;
    try {
      const context = storedContext();
      if (!context || context.legacy) throw new Error(expiredMessage);
      const current = await check(context);
      if (current.stage === 'complete') await refreshConnection();
      else if (['cancelled', 'expired'].includes(current.stage)) notify(expiredMessage);
      else notify(current.stage === 'ready' && context.phase === 'ready' ? 'The bank session is ready. Resume its original Connect or Manage action, or cancel it.' : pendingMessage);
    } catch (failure) {notify(failure.message);}
    finally {activeRequests -= 1;}
  });
  bindButtons('[data-plaid-cancel-link]', async () => {
    if (linking || activeRequests) return;
    activeRequests += 1;
    try {
      const context = storedContext();
      if (!context) throw new Error(expiredMessage);
      await cancel(context);
    } catch (failure) {notify(failure.message);}
    finally {activeRequests -= 1;}
  });
  bindButtons('[data-plaid-finish-link]', async () => {
    if (linking || activeRequests || !finishable) return;
    activeRequests += 1;
    finishable = false;
    showContinuation();
    let context;
    try {
      context = storedContext();
      if (!context || context.legacy) throw new Error(expiredMessage);
      const current = await check(context);
      if (current.stage === 'complete') {await refreshConnection(); return;}
      if (!['captured', 'apply_uncertain'].includes(current.stage)) throw new Error(pendingMessage);
      // Explicitly resume only the encrypted captured native command. No public token is sent.
      context = {...context, phase: 'submitted'};
      saveContext(context);
      const result = status(await api('/plaid/exchange', {flowId: context.flow_id}), context);
      if (result.stage !== 'complete') throw new Error(pendingMessage);
      clearSession();
      await refreshConnection();
    } catch {
      try {
        if (context && !context.legacy && (await check(context)).stage === 'complete') {await refreshConnection(); return;}
      } catch { /* Preserve the original intent when receipt reconciliation is unavailable. */ }
      notify(pendingMessage);
    } finally {activeRequests -= 1;}
  });
  showContinuation();
  bindButtons('[data-plaid-sync]', async button => {
    if (button.disabled) return;
    button.disabled = true;
    activeRequests += 1;
    try {
      await api('/plaid/sync', {});
      reloadSettings();
    } catch (failure) {
      notify(failure.message);
      button.disabled = false;
    } finally {activeRequests -= 1;}
  });
  bindButtons('[data-plaid-disconnect]', button => {
    disconnectId = button.dataset.plaidDisconnect;
    document.querySelector('#plaid-disconnect-dialog h2').textContent = `Disconnect ${button.dataset.institution}?`;
    document.getElementById('plaid-disconnect-dialog').hidden = false;
    document.getElementById('plaid-confirm-disconnect').focus();
  });
  bindButtons('#plaid-confirm-disconnect', async button => {
    if (button.disabled) return;
    button.disabled = true;
    activeRequests += 1;
    try {
      await api(`/plaid/items/${encodeURIComponent(disconnectId)}`, {}, 'DELETE');
      reloadSettings();
    } catch (failure) {
      notify(failure.message);
      button.disabled = false;
    } finally {activeRequests -= 1;}
  });
}
bind();
document.addEventListener('whab:page-ready', bind);
document.addEventListener('whab:before-navigate', event => {
  if (linking || activeRequests) event.preventDefault();
});

if (new URL(location.href).searchParams.has('oauth_state_id')) {
  const token = sessionStorage.getItem('whab-link-token');
  if (token) connect(sessionStorage.getItem('whab-link-item') || undefined, token);
  else notify('The bank connection session expired. Open Plaid plugin settings and connect again.');
}

const previousNotice = sessionStorage.getItem('whab-notice');
if (previousNotice) {
  sessionStorage.removeItem('whab-notice');
  notify(previousNotice);
}
