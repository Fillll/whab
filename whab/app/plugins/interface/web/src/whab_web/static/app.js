/* WHAB controls. Business rules live in Python; bank UI is loaded by enabled plugins. */
import {api, notify} from '/static/client.js';

const toast = document.getElementById('toast');
document.addEventListener('whab:notice', event => {
  if (typeof event.detail?.message !== 'string') return;
  event.preventDefault();
  notify(event.detail.message);
});

for (const image of document.querySelectorAll('[data-bank-logo], [data-merchant-logo]')) {
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

const inlineAssignments = [...document.querySelectorAll('form[data-inline-assignment]')];
let assignmentBusy = false;
let assignmentNavigation;
const pendingAssignmentControls = new Map();

function lockAssignments(locked) {
  assignmentBusy = locked;
  for (const form of inlineAssignments) form.querySelector('[data-cents]').readOnly = locked;
}

// Finish the assignment before following a month/category link that blurred it.
document.addEventListener('click', event => {
  if (!assignmentBusy || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest('a[href]');
  if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
  event.preventDefault();
  assignmentNavigation = link.href;
});

for (const form of document.querySelectorAll('form[data-api]')) {
  const inline = form.hasAttribute('data-inline-assignment');
  const amount = inline ? form.querySelector('[data-cents]') : null;
  const originalAmount = amount?.value;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.busy || inline && (assignmentBusy || amount.value.trim() === originalAmount)) return;
    const error = form.querySelector('.field-error');
    try {
      const body = {};
      for (const field of form.elements) {
        if (!field.name || field.disabled) continue;
        body[field.name] = field.hasAttribute('data-boolean') ? field.checked :
          field.hasAttribute('data-boolean-value') ? field.value === 'true' :
          field.hasAttribute('data-cents') ? minorAmount(field.value, Number(form.elements.namedItem('currency')?.selectedOptions?.[0]?.dataset.minorUnits ?? field.dataset.minorUnits)) :
          field.hasAttribute('data-null') && !field.value ? null : field.value;
      }
      if (body._kind === 'income') body.amount = -body.amount;
      delete body._kind;
      form.dataset.busy = 'true';
      if (error) error.hidden = true;
      if (inline) {
        lockAssignments(true);
        form.setAttribute('aria-busy', 'true');
        amount.removeAttribute('aria-invalid');
      }
      await api(form.dataset.api, body, form.dataset.method || 'POST');
      if (inline && assignmentNavigation) {
        location.assign(assignmentNavigation);
      } else if (body.currency) {
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
      if (inline) {
        lockAssignments(false);
        assignmentNavigation = undefined;
        for (const [control, value] of pendingAssignmentControls) control.value = value;
        pendingAssignmentControls.clear();
        form.removeAttribute('aria-busy');
        amount.setAttribute('aria-invalid', 'true');
      }
    }
  });
  if (form.hasAttribute('data-auto')) form.addEventListener('change', () => form.requestSubmit());
  if (inline) {
    amount.addEventListener('focus', () => amount.select());
    amount.addEventListener('blur', () => form.requestSubmit());
    amount.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        form.requestSubmit();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        amount.value = originalAmount;
        amount.removeAttribute('aria-invalid');
        const error = form.querySelector('.field-error');
        if (error) error.hidden = true;
        amount.blur();
      }
    });
  }
}

function updateAccountFields(form) {
  const scope = form.querySelector('[data-account-scope]');
  const type = form.querySelector('[data-account-type]');
  const subtype = form.querySelector('[data-account-subtype]');
  for (const option of type.options) {
    option.hidden = scope.value === 'true' && option.dataset.onBudget !== 'true';
    option.disabled = option.hidden;
  }
  if (type.selectedOptions[0].disabled) type.value = [...type.options].find(option => !option.disabled).value;
  for (const option of subtype.options) {
    option.hidden = Boolean(option.value) && option.dataset.accountType !== type.value;
    option.disabled = option.hidden;
  }
  if (subtype.selectedOptions[0].disabled) subtype.value = '';
  subtype.closest('label').hidden = ![...subtype.options].some(option => option.value && !option.disabled);
  const balanceLabel = form.querySelector('[data-account-balance-label]');
  if (balanceLabel) {
    const liability = type.selectedOptions[0].dataset.liability === 'true';
    balanceLabel.textContent = liability ? 'Current amount owed' : 'Current balance';
    form.querySelector('[data-account-balance-hint]').textContent = liability ?
      'Enter the amount you owe as a positive number. It reduces your net worth.' :
      'Enter the current account balance or the estimated value of this asset.';
  }
}
for (const form of document.querySelectorAll('[data-account-form]')) {
  form.querySelector('[data-account-scope]').addEventListener('change', () => updateAccountFields(form));
  form.querySelector('[data-account-type]').addEventListener('change', () => updateAccountFields(form));
  updateAccountFields(form);
}
for (const button of document.querySelectorAll('[data-edit-account]')) button.addEventListener('click', () => {
  const form = document.querySelector('[data-account-classification]');
  form.dataset.api = `/accounts/${encodeURIComponent(button.dataset.editAccount)}`;
  form.querySelector('[data-account-edit-name]').textContent = button.dataset.accountName;
  form.elements.onBudget.value = button.dataset.accountOnBudget;
  updateAccountFields(form);
  form.elements.type.value = button.dataset.accountType;
  updateAccountFields(form);
  form.elements.subtype.value = button.dataset.accountSubtype;
  form.querySelector('.field-error').hidden = true;
});

for (const button of document.querySelectorAll('[data-dialog]')) button.addEventListener('click', () => document.getElementById(button.dataset.dialog).showModal());
for (const button of document.querySelectorAll('[data-close-dialog]')) button.addEventListener('click', () => button.closest('dialog').close());
for (const dialog of document.querySelectorAll('dialog')) {
  dialog.addEventListener('click', event => {
    if (event.target === dialog && !dialog.querySelector('[data-budget-new][data-busy]')) dialog.close();
  });
  dialog.addEventListener('cancel', event => {
    if (dialog.querySelector('[data-budget-new][data-busy]')) event.preventDefault();
  });
}
document.querySelector('[data-dismiss-toast]').addEventListener('click', () => {
  toast.hidden = true;
});

const pluginSelection = document.querySelector('[data-plugin-selection]');
if (pluginSelection) {
  const selectedPlugins = () => new FormData(pluginSelection).getAll('plugin');
  const initialSelection = new Set(selectedPlugins());
  const pending = pluginSelection.querySelector('.plugin-restart-notice');
  pluginSelection.addEventListener('change', () => {
    const selection = new Set(selectedPlugins());
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
      const plugins = selectedPlugins();
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
  if (['#settings', '#budgets', '#plugins'].includes(location.hash)) document.getElementById('settings-dialog').showModal();
  if (location.hash === '#budgets') document.getElementById('budgets-dialog').showModal();
  if (location.hash === '#plugins') document.getElementById('plugins-dialog').showModal();
}


const budgetDialog = document.getElementById('budgets-dialog');
async function loadBudgetControls() {
  const error = budgetDialog.querySelector('[data-budget-load-error]');
  error.hidden = true;
  try {
    const [budget, catalog] = await Promise.all([api('/budget'), api('/budgets')]);
    budgetDialog.querySelector('[data-budget-name]').textContent = budget.name;
    budgetDialog.querySelector('[data-budget-export]').hidden = budget.storage !== 'client';
    budgetDialog.querySelector('[data-budget-save]').hidden = !budget.portable;
    budgetDialog.querySelector('[data-budget-copy]').hidden = !budget.portable;
    const pending = budget.copies.filter(copy => copy.status === 'pending');
    const missing = budget.missingPlugins || [];
    const copies = budgetDialog.querySelector('[data-budget-copies]');
    copies.replaceChildren();
    for (const copy of budget.copies) {
      const row = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = copy.name;
      const status = document.createElement('small');
      status.textContent = copy.status === 'pending' ? 'Waiting to sync' : 'Saved';
      row.append(name, status);
      copies.append(row);
    }
    budgetDialog.querySelector('[data-budget-status]').textContent = [
      pending.length ? `${pending.length} saved ${pending.length === 1 ? 'copy is' : 'copies are'} waiting to sync.` : 'All saved copies are up to date.',
      missing.length ? `Install compatible plugins to restore all features: ${missing.join(', ')}.` : '',
    ].filter(Boolean).join(' ');
    const select = budgetDialog.querySelector('[data-available-budgets]');
    select.replaceChildren();
    for (const entry of catalog.budgets) {
      const option = document.createElement('option');
      option.value = entry.id;
      option.textContent = entry.name;
      option.selected = entry.active;
      select.append(option);
    }
    budgetDialog.querySelector('[data-budget-switch]').hidden = catalog.budgets.length < 2;
  } catch (failure) {
    budgetDialog.querySelector('[data-budget-name]').textContent = 'Unavailable';
    error.textContent = failure.message;
    error.hidden = false;
  }
}
for (const button of document.querySelectorAll('[data-dialog="budgets-dialog"]')) button.addEventListener('click', loadBudgetControls);
if (location.hash === '#budgets') loadBudgetControls();
budgetDialog.querySelector('[data-budget-save]').addEventListener('click', async event => {
  const button = event.currentTarget;
  const error = budgetDialog.querySelector('[data-budget-load-error]');
  button.disabled = true;
  error.hidden = true;
  try {
    await api('/budget/save', {});
    await loadBudgetControls();
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});

function budgetPasswordHeaders(password) {
  if (!password) return {};
  const bytes = new TextEncoder().encode(password);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return {'X-Budget-Password': btoa(binary), 'X-Budget-Password-Encoding': 'base64'};
}

const exportForm = document.querySelector('[data-budget-export]');
exportForm.elements.include_credentials.addEventListener('change', () => {
  const included = exportForm.elements.include_credentials.checked;
  exportForm.elements.password.disabled = !included;
  exportForm.elements.password.required = included;
});
exportForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (exportForm.dataset.busy) return;
  const error = exportForm.querySelector('.field-error');
  const submit = exportForm.querySelector('button[type="submit"]');
  exportForm.dataset.busy = 'true';
  submit.disabled = true;
  error.hidden = true;
  try {
    const included = exportForm.elements.include_credentials.checked;
    const password = exportForm.elements.password.value;
    if (included && Array.from(password).length < 12) throw new Error('Use a password of at least 12 characters.');
    const response = await fetch(`/api/budget/export${included ? '?include_credentials=true' : ''}`, {
      headers: included ? budgetPasswordHeaders(password) : {},
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.error || 'The budget could not be exported.');
    }
    const url = URL.createObjectURL(await response.blob());
    const download = document.createElement('a');
    download.href = url;
    download.download = 'budget.whab';
    download.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    exportForm.reset();
    exportForm.elements.password.disabled = true;
    exportForm.elements.password.required = false;
    notify('Budget exported.');
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    delete exportForm.dataset.busy;
    submit.disabled = false;
  }
});

function budgetForm(selector, action) {
  const form = document.querySelector(selector);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.busy) return;
    const error = form.querySelector('.field-error');
    const submit = form.querySelector('button[type="submit"]');
    const buttons = form.hasAttribute('data-budget-new') ? form.closest('dialog').querySelectorAll('button') : [submit];
    form.dataset.busy = 'true';
    for (const button of buttons) button.disabled = true;
    error.hidden = true;
    try {
      await action(form);
      location.assign('/?workspace=personal');
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      delete form.dataset.busy;
      for (const button of buttons) button.disabled = false;
    }
  });
}
const copyForm = document.querySelector('[data-budget-copy]');
copyForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (copyForm.dataset.busy) return;
  const error = copyForm.querySelector('.field-error');
  const submit = copyForm.querySelector('button[type="submit"]');
  copyForm.dataset.busy = 'true';
  submit.disabled = true;
  error.hidden = true;
  try {
    await api('/budget/copies', {name: copyForm.elements.name.value.trim(), path: copyForm.elements.path.value.trim()});
    copyForm.reset();
    await loadBudgetControls();
    notify('Saved copy added.');
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    delete copyForm.dataset.busy;
    submit.disabled = false;
  }
});
budgetForm('[data-budget-new]', form => api('/budget/new', {name: form.elements.name.value.trim()}));
budgetForm('[data-budget-switch]', async form => {
  const response = await fetch(`/api/budgets/${encodeURIComponent(form.elements.budget.value)}/activate`, {
    method: 'POST', body: '{}',
    headers: {'Content-Type': 'application/json', ...budgetPasswordHeaders(form.elements.password.value)},
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The budget could not be opened.');
});
budgetForm('[data-budget-open]', async form => {
  const file = form.elements.budget.files[0];
  if (!file) throw new Error('Choose a WHAB budget file.');
  if (file.size > 50 * 1024 * 1024) throw new Error('Choose a budget file smaller than 50 MiB.');
  const path = form.elements.as_copy.checked ? '/api/budget/open?as_copy=true' : '/api/budget/open';
  const response = await fetch(path, {
    method: 'POST', body: file,
    headers: {'Content-Type': 'application/zip', ...budgetPasswordHeaders(form.elements.password.value)},
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The budget could not be opened.');
});

const pluginCategory = document.querySelector('[data-plugin-category-filter]');
if (pluginCategory) pluginCategory.addEventListener('change', () => {
  let visible = 0;
  for (const group of pluginSelection.querySelectorAll('[data-plugin-category]')) {
    group.hidden = pluginCategory.value !== 'all' && group.dataset.pluginCategory !== pluginCategory.value;
    if (!group.hidden) visible += 1;
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
  if (!settings.insights.length && !settings.actions.length && !dialog.querySelector('[data-appearance-choice]')) content.append(settingsText('p', 'This plugin has no additional settings.', 'settings-notice'));
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

for (const input of document.querySelectorAll('[data-navigation]')) {
  const initialValue = input.value;
  input.addEventListener('change', () => {
    if (!input.checkValidity()) {
      input.reportValidity();
      return;
    }
    const url = new URL(location.href);
    url.hash = '';
    url.searchParams.set(input.dataset.navigation, input.value);
    if (input.dataset.navigation === 'workspace') url.searchParams.delete('category');
    if (assignmentBusy) {
      assignmentNavigation = url.href;
      pendingAssignmentControls.set(input, initialValue);
    } else location.assign(url);
  });
}
for (const button of document.querySelectorAll('[data-budget-group]')) button.addEventListener('click', () => {
  const expanded = button.getAttribute('aria-expanded') === 'true';
  button.setAttribute('aria-expanded', String(!expanded));
  for (const row of document.querySelectorAll('[data-budget-group-member]'))
    if (row.dataset.budgetGroupMember === button.dataset.budgetGroup) row.hidden = expanded;
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
