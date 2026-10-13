/* WHAB controls. Business rules live in Python; bank UI is loaded by enabled plugins. */
import {api, notify} from '/static/client.js';

const toast = document.getElementById('toast');
document.addEventListener('whab:notice', event => {
  if (typeof event.detail?.message !== 'string') return;
  event.preventDefault();
  notify(event.detail.message);
});

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

if (!location.search.includes('oauth_state_id') && ['#budget', '#transactions', '#insights', '#accounts'].includes(location.hash)) {
  const url = new URL(location.href);
  url.searchParams.set('page', location.hash.slice(1));
  url.hash = '';
  location.replace(url);
}
