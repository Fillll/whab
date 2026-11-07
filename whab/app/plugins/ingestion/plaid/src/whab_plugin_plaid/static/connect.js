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
          if (document.querySelector('[data-bank-provider="plaid"]')?.dataset.syncSetup !== 'true') {
            await api('/plaid/sync', itemId ? {itemId} : {});
          }
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
function setupElement(tag, text) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
}
function renderSyncSetup(form, preview) {
  form.dataset.itemId = preview.itemId;
  const choices = form.querySelector('[data-plaid-account-choices]');
  choices.replaceChildren();
  for (const account of preview.accounts) {
    const row = setupElement('fieldset');
    row.dataset.bankAccountId = account.id;
    row.append(setupElement('legend', `${account.name}${account.mask ? ` · ending ${account.mask}` : ''} · ${account.currency}`));
    const destination = setupElement('select');
    destination.setAttribute('aria-label', `WHAB destination for ${account.name}`);
    destination.dataset.destination = '';
    for (const [value, text] of [['choose', 'Choose a WHAB destination'], ['skip', 'Do not import'], ['', 'Create a new WHAB account'],
        ...preview.destinations.filter(candidate => candidate.currency === account.currency && candidate.available !== false).map(candidate => [candidate.id, candidate.name])]) {
      const option = setupElement('option', text); option.value = value; destination.append(option);
    }
    destination.value = account.link?.accountId || (preview.configured ? 'skip' : 'choose');
    const history = setupElement('select'); history.dataset.history = '';
    history.setAttribute('aria-label', `Transaction history for ${account.name}`);
    for (const [value, text] of [['', 'Choose transaction history'], ['date', 'Choose a start date'], ['all', 'All available history']]) {
      const option = setupElement('option', text); option.value = value; history.append(option);
    }
    history.value = account.link ? (account.link.since ? 'date' : 'all') : '';
    const label = setupElement('label', 'Import transactions since');
    const since = setupElement('input'); since.type = 'date'; since.dataset.since = '';
    since.setAttribute('aria-label', `Import start date for ${account.name}`);
    since.value = account.link?.since || '';
    label.append(since);
    destination.addEventListener('change', () => {
      history.disabled = destination.value === 'skip';
      since.disabled = history.disabled || history.value !== 'date';
    });
    history.addEventListener('change', () => {since.disabled = history.value !== 'date';});
    history.disabled = destination.value === 'skip';
    since.disabled = history.disabled || history.value !== 'date';
    row.append(destination, history, label); choices.append(row);
  }
  form.querySelector('[data-plaid-setup-error]').hidden = true;
  form.hidden = false;
  form.scrollIntoView({block: 'nearest'});
  form.querySelector('[data-destination]')?.focus();
}

let disconnectId;
const bound = new WeakSet();
function bindButtons(selector, callback, eventType = 'click') {
  for (const button of document.querySelectorAll(selector)) {
    if (bound.has(button)) continue;
    bound.add(button);
    button.addEventListener(eventType, event => callback(button, event));
  }
}
function bind() {
  bindInvestments();
  bindButtons('[data-plaid-setup]', async button => {
    if (button.disabled || linking || activeRequests) return;
    button.disabled = true; activeRequests += 1;
    try {
      const preview = await api('/plaid/sync-preview', {itemId: button.dataset.plaidSetup});
      renderSyncSetup(document.querySelector('[data-plaid-sync-form]'), preview);
    } catch (failure) {notify(failure.message);}
    finally {button.disabled = false; activeRequests -= 1;}
  });
  bindButtons('[data-plaid-setup-cancel]', button => {button.closest('[data-plaid-sync-form]').hidden = true;});
  bindButtons('[data-plaid-setup-save]', async button => {
    if (button.disabled) return;
    const form = button.closest('[data-plaid-sync-form]');
    button.disabled = true; activeRequests += 1;
    const error = form.querySelector('[data-plaid-setup-error]'); error.hidden = true;
    try {
      const rows = [...form.querySelectorAll('[data-bank-account-id]')].filter(row => row.querySelector('[data-destination]').value !== 'skip');
      if (rows.some(row => row.querySelector('[data-destination]').value === 'choose')) {
        throw new Error('Choose a WHAB destination for each bank account.');
      }
      if (rows.some(row => !row.querySelector('[data-history]').value || (row.querySelector('[data-history]').value === 'date' && !row.querySelector('[data-since]').value))) {
        throw new Error('Choose a start date or All available history for each bank account.');
      }
      const accounts = rows.map(row => ({
        bankAccountId: row.dataset.bankAccountId, accountId: row.querySelector('[data-destination]').value || null,
        since: row.querySelector('[data-history]').value === 'all' ? null : row.querySelector('[data-since]').value,
      }));
      await api('/plaid/sync-settings', {itemId: form.dataset.itemId, accounts}, 'PUT');
      await api('/plaid/sync', {itemId: form.dataset.itemId});
      reloadSettings();
    } catch (failure) {error.textContent = failure.message; error.hidden = false;}
    finally {button.disabled = false; activeRequests -= 1;}
  });
  for (const panel of document.querySelectorAll('[data-plaid-schedule]')) {
    if (bound.has(panel)) continue;
    bound.add(panel);
    api('/plaid/schedule').then(result => {
      const job = result.jobs[0];
      if (!job) return;
      panel.querySelector('[data-plaid-cadence]').value = job.interval_seconds === null ? 'manual' : String(job.interval_seconds);
      const cadence = job.interval_seconds === null ? 'Automatic updates are off.' :
        `Next check: ${job.next_due ? new Date(job.next_due * 1000).toLocaleString() : 'When due'}.`;
      panel.querySelector('[data-plaid-schedule-status]').textContent = `${cadence} Background checks: ${result.background?.enabled ? 'enabled' : 'off'}.`;
    }).catch(failure => {panel.querySelector('[data-plaid-schedule-status]').textContent = failure.message;});
  }
  bindButtons('[data-plaid-schedule-save]', async button => {
    const panel = button.closest('[data-plaid-schedule]');
    button.disabled = true;
    try {
      const value = panel.querySelector('[data-plaid-cadence]').value;
      await api('/plaid/schedule', {interval_seconds: value === 'manual' ? null : Number(value)}, 'PUT');
      panel.querySelector('[data-plaid-schedule-status]').textContent = 'Update frequency saved.';
    } catch (failure) {panel.querySelector('[data-plaid-schedule-status]').textContent = failure.message;}
    finally {button.disabled = false;}
  });
  bindButtons('[data-plaid-background]', async button => {
    const panel = button.closest('[data-plaid-schedule]'); button.disabled = true;
    try {
      await api('/plaid/schedule/background', {enabled: button.dataset.plaidBackground === 'true'});
      panel.querySelector('[data-plaid-schedule-status]').textContent = button.dataset.plaidBackground === 'true' ? 'Background checks enabled.' : 'Background checks disabled.';
    } catch (failure) {panel.querySelector('[data-plaid-schedule-status]').textContent = failure.message;}
    finally {button.disabled = false;}
  });
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
function investmentMoney(value, currency) {
  if (value === null) return 'Unavailable';
  const formatter = new Intl.NumberFormat(undefined, {style: 'currency', currency});
  return formatter.format(value / 10 ** formatter.resolvedOptions().maximumFractionDigits);
}
function investmentCell(row, value, className) {
  const cell = setupElement('td', value);
  if (className) cell.className = className;
  row.append(cell);
  return cell;
}
function renderInvestmentReport(view, report) {
  view.investmentReport = report;
  const accountNames = new Map(report.accounts.map(account => [account.id, account.name]));
  const totals = view.querySelector('[data-investment-totals]'); totals.replaceChildren();
  for (const total of report.totals) {
    for (const [label, value] of [
      [`Value · ${total.currency}`, investmentMoney(total.value, total.currency)],
      ['Cost basis available', total.coveredPositions ? investmentMoney(total.costBasis, total.currency) : 'Unavailable'],
      ['Unrealized gain/loss', total.coveredPositions ? `${investmentMoney(total.unrealizedGain, total.currency)} · ${total.unrealizedReturnPercent}%` : 'Unavailable'],
    ]) {
      const group = setupElement('div'); group.append(setupElement('dt', label), setupElement('dd', value)); totals.append(group);
    }
  }
  const positions = view.querySelector('[data-investment-positions]'); positions.replaceChildren();
  const limit = Number(view.dataset.positionLimit || 500);
  for (const position of report.positions.slice(0, limit)) {
    const row = setupElement('tr'); const identity = investmentCell(row, '');
    identity.append(setupElement('strong', position.ticker || position.name));
    identity.append(setupElement('small', `${position.name} · ${position.quantity} units · ${position.status}`));
    identity.append(setupElement('small', accountNames.get(position.accountId) || ''));
    if (position.price !== null) identity.append(setupElement('small', `${position.price} ${position.currency} / unit · ${position.priceAsOf || 'Price date unavailable'}`));
    investmentCell(row, investmentMoney(position.value, position.currency));
    investmentCell(row, investmentMoney(position.costBasis, position.currency));
    const gainClass = position.unrealizedGain < 0 ? 'investment-loss' : 'investment-gain';
    investmentCell(row, investmentMoney(position.unrealizedGain, position.currency), gainClass);
    investmentCell(row, position.unrealizedReturnPercent === null ? 'Unavailable' : `${position.unrealizedReturnPercent}%`, gainClass);
    investmentCell(row, position.firstRecordedAddition || 'Unavailable'); positions.append(row);
  }
  view.querySelector('[data-investment-more]').hidden = report.positions.length <= limit;
  const activity = view.querySelector('[data-investment-activity]'); activity.replaceChildren();
  const activityLimit = Number(view.dataset.activityLimit || 100);
  for (const event of report.activity.slice(0, activityLimit)) {
    const row = setupElement('tr'); const name = investmentCell(row, event.name);
    name.append(setupElement('small', `${event.type}${event.subtype ? ` · ${event.subtype}` : ''}`));
    investmentCell(row, event.date); investmentCell(row, investmentMoney(-event.amount, event.currency));
    investmentCell(row, investmentMoney(event.fees, event.currency)); activity.append(row);
  }
  view.querySelector('[data-investment-more-activity]').hidden = report.activity.length <= activityLimit;
  view.querySelector('[data-investment-status]').textContent = report.positions.length
    ? `${Math.min(limit, report.positions.length)} of ${report.positions.length} positions. ${Math.min(activityLimit, report.activity.length)} of ${report.activity.length} activity records.`
    : 'No current investment positions were returned for this account.';
  view.querySelector('[data-investment-snapshot]').textContent = `Snapshot: ${new Date(report.asOf).toLocaleString()}${report.providerUpdatedAt ? ` · Provider update: ${new Date(report.providerUpdatedAt).toLocaleString()}` : ''}`;
}
async function loadInvestmentReport(view) {
  if (activeRequests || linking) return;
  const provider = view.closest('[data-bank-provider]');
  const button = view.querySelector('[data-investment-load]');
  const error = view.querySelector('[data-investment-error]'); error.hidden = true;
  const account = view.querySelector('[data-investment-account]');
  const start = view.querySelector('[data-investment-start]'); const end = view.querySelector('[data-investment-end]');
  const selected = account.value;
  view.dataset.epoch = String(Number(view.dataset.epoch || 0) + 1); const epoch = view.dataset.epoch;
  button.disabled = true; activeRequests += 1;
  for (const control of [account, start, end]) control.disabled = true;
  view.investmentReport = null;
  for (const selector of ['[data-investment-positions]', '[data-investment-activity]', '[data-investment-totals]']) view.querySelector(selector).replaceChildren();
  view.querySelector('[data-investment-snapshot]').textContent = '';
  view.querySelector('[data-investment-more]').hidden = true; view.querySelector('[data-investment-more-activity]').hidden = true;
  view.querySelector('[data-investment-status]').textContent = 'Loading investment positions…';
  try {
    const report = await api('/plaid/investments', {itemId: view.dataset.itemId, bankAccountId: selected || null,
      startDate: start.value || null, endDate: end.value || null});
    if (view.dataset.epoch !== epoch || view.isConnected === false || provider.isConnected === false) return;
    const known = new Set([...account.options].map(option => option.value));
    for (const entry of report.accounts) {
      if (!known.has(entry.id)) {
        const option = setupElement('option', `${entry.name}${entry.mask ? ` · ${entry.mask}` : ''}`); option.value = entry.id; account.append(option);
      }
    }
    account.value = selected; start.value = report.startDate; end.value = report.endDate;
    view.dataset.positionLimit = '500'; view.dataset.activityLimit = '100';
    renderInvestmentReport(view, report);
  } catch (failure) {
    if (view.dataset.epoch === epoch) {
      error.textContent = failure.message; error.hidden = false;
      view.querySelector('[data-investment-status]').textContent = '';
    }
  } finally {
    button.disabled = false; activeRequests -= 1;
    for (const control of [account, start, end]) control.disabled = false;
  }
}
function bindInvestments() {
  bindButtons('[data-plaid-investments]', async button => {
    if (activeRequests || linking) return;
    const provider = button.closest('[data-bank-provider]'); const view = provider.querySelector('[data-plaid-investment-view]');
    view.dataset.itemId = button.dataset.plaidInvestments; view.investmentReport = null;
    const option = setupElement('option', 'All investment accounts'); option.value = '';
    view.querySelector('[data-investment-account]').replaceChildren(option);
    view.querySelector('[data-investment-positions]').replaceChildren(); view.querySelector('[data-investment-activity]').replaceChildren();
    view.querySelector('[data-investment-totals]').replaceChildren();
    provider.querySelector('[data-plaid-connections-view]').hidden = true; view.hidden = false;
    view.querySelector('[data-investment-heading]').focus(); await loadInvestmentReport(view);
  });
  bindButtons('[data-investment-back]', button => {
    const view = button.closest('[data-plaid-investment-view]'); view.dataset.epoch = String(Number(view.dataset.epoch || 0) + 1);
    view.hidden = true; view.closest('[data-bank-provider]').querySelector('[data-plaid-connections-view]').hidden = false;
  });
  bindButtons('[data-investment-load]', button => loadInvestmentReport(button.closest('[data-plaid-investment-view]')));
  bindButtons('[data-investment-account]', button => loadInvestmentReport(button.closest('[data-plaid-investment-view]')), 'change');
  for (const [selector, key, size] of [['[data-investment-more]', 'positionLimit', 500], ['[data-investment-more-activity]', 'activityLimit', 100]]) {
    bindButtons(selector, button => {
      const view = button.closest('[data-plaid-investment-view]'); view.dataset[key] = String(Number(view.dataset[key] || size) + size);
      if (view.investmentReport) renderInvestmentReport(view, view.investmentReport);
    });
  }
}
