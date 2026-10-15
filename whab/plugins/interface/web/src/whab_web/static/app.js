/* WHAB controls. Business rules live in Python; bank UI is loaded by enabled plugins. */
import {api, notify} from '/static/client.js';

const toast = document.getElementById('toast');
document.addEventListener('whab:notice', event => {
  if (typeof event.detail?.message !== 'string') return;
  event.preventDefault();
  notify(event.detail.message);
});

for (const image of document.querySelectorAll('[data-bank-logo]')) {
  const update = () => {
    const loaded = image.naturalWidth > 0;
    image.hidden = !loaded;
    image.previousElementSibling.hidden = loaded;
    image.parentElement.classList.toggle('has-logo', loaded);
  };
  image.addEventListener('load', update);
  image.addEventListener('error', update);
  if (image.complete) update();
}

function minorAmount(value, digits) {
  const clean = value.trim();
  const decimal = digits ? `(\\.\\d{1,${digits}})?` : '';
  if (!new RegExp(`^\\d+${decimal}$`).test(clean)) {
    throw new Error(digits ? `Enter an amount with up to ${digits} decimal places.` : 'Enter a whole-number amount.');
  }
  const [whole, fraction = ''] = clean.split('.');
  const result = BigInt(whole) * 10n ** BigInt(digits) + BigInt(fraction.padEnd(digits, '0'));
  if (result > 1_000_000_000_000n) throw new Error('That amount is too large.');
  return Number(result);
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
          field.hasAttribute('data-cents') ? minorAmount(field.value, Number(form.elements.namedItem('currency')?.selectedOptions?.[0]?.dataset.minorUnits ?? field.dataset.minorUnits)) :
          field.hasAttribute('data-null') && !field.value ? null : field.value;
      }
      if (body._kind === 'income') body.amount = -body.amount;
      delete body._kind;
      form.dataset.busy = 'true';
      if (error) error.hidden = true;
      await api(form.dataset.api, body, form.dataset.method || 'POST');
      if (body.currency) {
        const url = new URL(location.href);
        url.searchParams.set('currency', body.currency);
        location.assign(url);
      } else location.reload();
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

const pluginSelection = document.querySelector('[data-plugin-selection]');
if (pluginSelection) {
  const initialSelection = new Set([...pluginSelection.querySelectorAll('input[name="plugin"]:checked')].map(input => input.value));
  const pending = pluginSelection.querySelector('.plugin-restart-notice');
  pluginSelection.addEventListener('change', () => {
    const selection = new Set([...pluginSelection.querySelectorAll('input[name="plugin"]:checked')].map(input => input.value));
    const changed = selection.size !== initialSelection.size || [...selection].some(id => !initialSelection.has(id));
    if (changed) {
      pending.textContent = 'You have unsaved plugin changes. Save your selection, then restart WHAB to apply it.';
      pending.hidden = false;
    } else if (pending.dataset.restartRequired === 'true') {
      pending.textContent = 'Restart WHAB to apply your saved plugin selection. Running plugins stay active until then.';
      pending.hidden = false;
    } else pending.hidden = true;
  });
  pending.dataset.restartRequired = String(!pending.hidden);
  pluginSelection.addEventListener('submit', async event => {
    event.preventDefault();
    if (pluginSelection.dataset.busy) return;
    const error = pluginSelection.querySelector('.field-error');
    const submit = pluginSelection.querySelector('button[type="submit"]');
    try {
      pluginSelection.dataset.busy = 'true';
      error.hidden = true;
      submit.disabled = true;
      const plugins = [...pluginSelection.querySelectorAll('input[name="plugin"]:checked')].map(input => input.value);
      await api('/plugins/management', {plugins}, 'PUT');
      location.hash = 'plugins';
      location.reload();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      delete pluginSelection.dataset.busy;
      submit.disabled = false;
    }
  });
  if (['#settings', '#plugins'].includes(location.hash)) document.getElementById('settings-dialog').showModal();
  if (location.hash === '#plugins') document.getElementById('plugins-dialog').showModal();
}


const pluginCategory = document.querySelector('[data-plugin-category-filter]');
if (pluginCategory) pluginCategory.addEventListener('change', () => {
  let visible = 0;
  for (const row of pluginSelection.querySelectorAll('[data-plugin-category]')) {
    row.hidden = pluginCategory.value !== 'all' && row.dataset.pluginCategory !== pluginCategory.value;
    if (!row.hidden) visible += 1;
  }
  pluginSelection.querySelector('[data-plugin-category-empty]').hidden = visible > 0;
});

function settingsText(tag, value, className) {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  return element;
}

function renderPluginSettings(dialog, settings) {
  const content = dialog.querySelector('[data-plugin-settings]');
  content.replaceChildren();
  const insights = document.createElement('dl');
  insights.className = 'plugin-settings-insights';
  for (const insight of settings.insights) {
    const row = document.createElement('div');
    row.append(settingsText('dt', insight.title), settingsText('dd', insight.value));
    if (insight.detail) row.append(settingsText('p', insight.detail));
    insights.append(row);
  }
  content.append(insights);
  for (const action of settings.actions) {
    const row = document.createElement('div');
    row.className = 'plugin-setting-action';
    row.append(settingsText('p', action.description, 'settings-notice'));
    const button = settingsText('button', action.label, 'button');
    button.type = 'button';
    button.addEventListener('click', async () => {
      if (dialog.dataset.busy) return;
      const error = dialog.querySelector('[data-plugin-settings-error]');
      const status = dialog.querySelector('[data-plugin-settings-status]');
      dialog.dataset.busy = 'true';
      for (const control of content.querySelectorAll('button')) control.disabled = true;
      error.hidden = true;
      status.textContent = 'Applying change…';
      try {
        const result = await api(`/plugins/${encodeURIComponent(dialog.dataset.pluginId)}/settings/actions/${encodeURIComponent(action.id)}`, {});
        renderPluginSettings(dialog, result.settings);
        status.textContent = result.message;
      } catch (failure) {
        status.textContent = '';
        error.textContent = failure.message;
        error.hidden = false;
      } finally {
        delete dialog.dataset.busy;
        for (const control of content.querySelectorAll('button')) control.disabled = false;
      }
    });
    row.append(button);
    content.append(row);
  }
  if (!settings.insights.length && !settings.actions.length) content.append(settingsText('p', 'This plugin has no additional settings.', 'settings-notice'));
}

for (const button of document.querySelectorAll('[data-plugin-options]')) button.addEventListener('click', async () => {
  const dialog = document.getElementById(button.dataset.dialog);
  if (dialog.dataset.pluginActive !== 'true' || dialog.dataset.busy) return;
  const error = dialog.querySelector('[data-plugin-settings-error]');
  const status = dialog.querySelector('[data-plugin-settings-status]');
  dialog.dataset.busy = 'true';
  dialog.querySelector('[data-plugin-settings]').replaceChildren();
  error.hidden = true;
  status.textContent = 'Loading settings…';
  try {
    const settings = await api(`/plugins/${encodeURIComponent(dialog.dataset.pluginId)}/settings`);
    renderPluginSettings(dialog, settings);
    status.textContent = '';
  } catch (failure) {
    status.textContent = '';
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    delete dialog.dataset.busy;
  }
});

for (const input of document.querySelectorAll('[data-navigation]')) input.addEventListener('change', () => {
  if (!input.checkValidity()) {
    input.reportValidity();
    return;
  }
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
for (const time of document.querySelectorAll('[data-local-time]')) time.textContent = new Date(time.dateTime).toISOString().slice(0, 10);

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

if (!location.search.includes('oauth_state_id') && ['#budget', '#transactions', '#insights', '#accounts'].includes(location.hash)) {
  const url = new URL(location.href);
  url.searchParams.set('page', location.hash.slice(1));
  url.hash = '';
  location.replace(url);
}
