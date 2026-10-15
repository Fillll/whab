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
      location.hash = 'settings';
      location.reload();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      delete pluginSelection.dataset.busy;
      submit.disabled = false;
    }
  });
  if (location.hash === '#settings') document.getElementById('settings-dialog').showModal();
}

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
