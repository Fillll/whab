/* WHAB controls. Business rules live in Python; bank UI is loaded by enabled plugins. */
import {api, apiUrl, notify, pageUrl} from './client.js';
import {initBudgetEdits, syncBudgetGroups} from './budget-edits.js';
import {initBudgetSourceLinks} from './budget-source-links.js';
import {initAccountNavigation} from './account-navigation.js';
import {initTransactionRegister} from './transaction-register.js';
import {hydrateCurrencyChoices, initAccountSettings} from './account-settings.js';
import {bindLogos} from './logo-images.js';

const toast = document.getElementById('toast');
document.addEventListener('whab:notice', event => {
  if (typeof event.detail?.message !== 'string') return;
  event.preventDefault();
  notify(event.detail.message);
});

bindLogos();

let currentRegister = null;
async function flushEdits() {
  if (!(await budgetEdits.flush())) return false;
  return !currentRegister || await currentRegister.flush();
}
async function requireSavedEdits() {
  if (!(await flushEdits())) throw new Error('Save the transaction or budget edit before continuing.');
}
initBudgetSourceLinks();
const accountNavigation = initAccountNavigation({
  flush: flushEdits,
  beforeReplace: () => budgetEdits.dispose(),
  onReplace() {
    currentRegister?.dispose();
    currentRegister = initTransactionRegister({navigate: (...args) => accountNavigation.navigate(...args), pendingDestination: () => accountNavigation.pendingDestination(), cancelNavigation: () => accountNavigation.cancelPending()});
    bindLogos();
  },
  notify,
});

function minorAmount(value, digits, authoritative = false) {
  const trimmed = value.trim();
  const negative = authoritative && trimmed.startsWith('-');
  const clean = negative ? trimmed.slice(1) : trimmed;
  const decimal = digits ? `(\\.\\d{1,${digits}})?` : '';
  if (!new RegExp(`^\\d+${decimal}$`).test(clean)) {
    throw new Error(digits ? `Enter an amount with up to ${digits} decimal places.` : 'Enter a whole-number amount.');
  }
  const [whole, fraction = ''] = clean.split('.');
  const result = BigInt(whole) * 10n ** BigInt(digits) + BigInt(fraction.padEnd(digits, '0'));
  if (result > (authoritative ? BigInt(Number.MAX_SAFE_INTEGER) : 1_000_000_000_000n)) throw new Error('That amount is too large.');
  return negative ? -Number(result) : Number(result);
}

function formBody(form, {authoritative = false} = {}) {
  const body = {};
  for (const field of form.elements) {
    if (!field.name || field.disabled || field.type === 'radio' && !field.checked) continue;
    body[field.name] = field.hasAttribute('data-boolean') ? field.checked :
      field.hasAttribute('data-boolean-value') ? field.value === 'true' :
      field.hasAttribute('data-cents') ? minorAmount(field.value, Number(form.elements.namedItem('currency')?.selectedOptions?.[0]?.dataset.minorUnits ?? field.dataset.minorUnits), authoritative) :
      field.hasAttribute('data-null') && !field.value ? null : field.value;
  }
  if (body._kind === 'income') body.amount = -body.amount;
  delete body._kind;
  return body;
}

const budgetEdits = initBudgetEdits({readForm: formBody, onDraft: () => accountNavigation.cancelPending()});
for (const form of document.querySelectorAll('form[data-api]')) {
  if (form.hasAttribute('data-inline-assignment') || form.hasAttribute('data-budget-mutation') || form.hasAttribute('data-account-classification')) continue;
  const radioChoices = [...form.querySelectorAll('input[type="radio"]')];
  const originalChoices = new Map(radioChoices.map(choice =>
    [choice, {checked: choice.checked, disabled: choice.disabled}]));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.busy) return;
    const error = form.querySelector('.field-error');
    form.dataset.busy = 'true';
    try {
      const body = formBody(form);
      await requireSavedEdits();
      if (error) error.hidden = true;
      if (radioChoices.length) {
        for (const choice of radioChoices) choice.disabled = true;
        form.setAttribute('aria-busy', 'true');
      }
      await api(form.dataset.api, body, form.dataset.method || 'POST');
      if (body.currency) location.assign(pageUrl({currency: body.currency}));
      else location.reload();
    } catch (failure) {
      if (error) {
        error.textContent = failure.message;
        error.hidden = false;
      } else notify(failure.message);
      delete form.dataset.busy;
      for (const choice of radioChoices) {
        choice.disabled = originalChoices.get(choice).disabled;
        choice.checked = originalChoices.get(choice).checked;
      }
      form.removeAttribute('aria-busy');
    }
  });
  if (form.hasAttribute('data-auto')) form.addEventListener('change', () => form.requestSubmit());
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
initAccountSettings({
  api, readForm: formBody, updateFields: updateAccountFields,
  flush: flushEdits,
  async refresh({currency, changedCurrency}) {
    const target = new URL(location.href);
    if (changedCurrency) {
      target.pathname = target.pathname.replace(/\/[a-z]{3}$/, '/' + currency.toLowerCase());
      location.assign(target);
    } else if (!(await accountNavigation.navigate(target, {history: 'replace', refresh: true}))) location.reload();
    bindLogos();
  },
});

document.addEventListener('click', event => {
  const button = event.target.closest('[data-dialog]');
  if (button && !button.disabled) {
    const dialog = document.getElementById(button.dataset.dialog);
    for (const select of dialog?.querySelectorAll('select[data-currency-choices-lazy]') || []) hydrateCurrencyChoices(select);
    dialog?.showModal();
  }
});
for (const button of document.querySelectorAll('[data-close-dialog]')) button.addEventListener('click', () => {
  const dialog = button.closest('dialog');
  if (!dialog.querySelector('[data-account-classification][data-busy]')) dialog.close();
});
for (const dialog of document.querySelectorAll('dialog')) {
  if (dialog.hasAttribute('data-transaction-receipt-dialog')) continue;
  dialog.addEventListener('click', event => {
    if (event.target === dialog && !dialog.querySelector('[data-budget-new][data-busy], [data-budget-open][data-busy], [data-budget-switch][data-busy], [data-account-classification][data-busy]')) dialog.close();
  });
  dialog.addEventListener('cancel', event => {
    if (dialog.querySelector('[data-budget-new][data-busy], [data-budget-open][data-busy], [data-budget-switch][data-busy]')) event.preventDefault();
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
    if (pluginSelection.dataset.busy || pluginSelection.hasAttribute('data-read-only')) return;
    const error = pluginSelection.querySelector('.field-error');
    const submit = pluginSelection.querySelector('button[type="submit"]');
    try {
      pluginSelection.dataset.busy = 'true';
      error.hidden = true;
      submit.disabled = true;
      const plugins = selectedPlugins();
      await requireSavedEdits();
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
function budgetChoice(entry, workspace, current, description) {
  const row = settingsText('div', '', 'budget-choice');
  if (current) row.setAttribute('aria-current', 'true');
  const label = settingsText('label', '', 'budget-choice-select');
  const radio = document.createElement('input');
  Object.assign(radio, {type: 'radio', name: 'budget', value: `${entry.id}:${workspace}`, required: true, checked: current});
  radio.dataset.budgetId = entry.id;
  radio.dataset.workspace = workspace;
  const copy = settingsText('span', '', 'budget-choice-copy');
  copy.append(settingsText('strong', entry.name), settingsText('small', current ? 'Current budget' : description, current ? 'budget-current' : ''));
  label.append(radio, copy);
  const download = settingsText('button', 'Download', 'button secondary');
  download.type = 'button';
  download.setAttribute('aria-label', `Download ${entry.name}`);
  Object.assign(download.dataset, {budgetDownload: '', budgetId: entry.id, workspace, name: entry.name});
  row.append(label, download);
  return row;
}

async function loadBudgetControls() {
  const error = budgetDialog.querySelector('[data-budget-load-error]');
  error.hidden = true;
  try {
    const [budget, catalog] = await Promise.all([api('/budget'), api('/budgets')]);
    budgetDialog.querySelector('[data-budget-name]').textContent = budget.name;
    const personal = budgetDialog.dataset.workspace === 'personal';
    budgetDialog.querySelector('[data-budget-export]').hidden = !personal || budget.storage !== 'client';
    budgetDialog.querySelector('[data-budget-save]').hidden = !personal || !budget.portable;
    budgetDialog.querySelector('[data-budget-copy]').hidden = !personal || !budget.portable;
    budgetDialog.querySelector('[data-budget-advanced]').hidden = !personal || (!budget.portable && budget.storage !== 'client' && !budget.missingPlugins?.length);
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
    const list = budgetDialog.querySelector('[data-available-budgets]');
    list.replaceChildren(settingsText('legend', 'Available budgets'));
    const order = entry => entry.id === 'default' ? 0 : entry.id === 'demo' ? 1 : 2;
    const entries = [...catalog.budgets].sort((a, b) => order(a) - order(b));
    for (const entry of entries) {
      const workspace = entry.id === 'demo' ? 'demo' : 'personal';
      list.append(budgetChoice(entry, workspace, entry.active, workspace === 'demo' ? 'Fictional data to explore WHAB' : 'Saved on this device'));
    }
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
    await requireSavedEdits();
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

async function downloadBudget(path, password, name) {
  await requireSavedEdits();
  const response = await fetch(path, {headers: budgetPasswordHeaders(password)});
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(result.error || 'The budget could not be downloaded.');
  }
  const url = URL.createObjectURL(await response.blob());
  const download = document.createElement('a');
  download.href = url;
  download.download = `${name.replace(/[\\/:*?"<>|]/g, '-').trim() || 'budget'}.whab`;
  download.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

budgetDialog.addEventListener('click', async event => {
  const button = event.target.closest('[data-budget-download]');
  if (!button || button.disabled || budgetDialog.dataset.navigating) return;
  const form = budgetDialog.querySelector('[data-budget-switch]');
  const error = form.querySelector('.field-error');
  button.disabled = true;
  button.textContent = 'Downloading…';
  error.hidden = true;
  try {
    const key = button.dataset.budgetId === 'default' ? 'home' : encodeURIComponent(button.dataset.budgetId);
    const path = button.dataset.workspace === 'demo' ? '/budgets/demo/api/budget/export' : `/budgets/${key}/api/budgets/${encodeURIComponent(button.dataset.budgetId)}/export`;
    await downloadBudget(path, form.elements.password.value, button.dataset.name);
    notify(`${button.dataset.name} downloaded.`);
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Download';
  }
});

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
    await downloadBudget(apiUrl(`/budget/export${included ? '?include_credentials=true' : ''}`), included ? password : '', 'budget');
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
    if (form.dataset.busy || budgetDialog.dataset.navigating) return;
    const error = form.querySelector('.field-error');
    const buttons = budgetDialog.querySelectorAll('button');
    budgetDialog.dataset.navigating = 'true';
    form.dataset.busy = 'true';
    for (const button of buttons) button.disabled = true;
    error.hidden = true;
    try {
      await requireSavedEdits();
      const result = await action(form);
      budgetDialog.close();
      document.getElementById('settings-dialog').close();
      location.assign(result.url);
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      delete form.dataset.busy;
      delete budgetDialog.dataset.navigating;
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
    await requireSavedEdits();
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
  const selected = form.querySelector('[name="budget"]:checked');
  if (!selected) throw new Error('Choose a budget.');
  if (selected.dataset.workspace === 'demo') return {url: form.dataset.demoUrl};
  const response = await fetch(apiUrl(`/budgets/${encodeURIComponent(selected.dataset.budgetId)}/activate`), {
    method: 'POST', body: '{}',
    headers: {'Content-Type': 'application/json', ...budgetPasswordHeaders(form.elements.password.value)},
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The budget could not be opened.');
  const destination = new URL(form.dataset.personalUrl, location.href);
  const target = new URL(result.url, location.href);
  destination.pathname = destination.pathname.replace(/^\/budgets\/[^/]+/, target.pathname.split('/').slice(0, 3).join('/'));
  return {url: destination.href};
});
budgetForm('[data-budget-open]', async form => {
  const file = form.elements.budget.files[0];
  if (!file) throw new Error('Choose a WHAB budget file.');
  if (file.size > 50 * 1024 * 1024) throw new Error('Choose a budget file smaller than 50 MiB.');
  const path = form.elements.as_copy.checked ? '/budget/open?as_copy=true' : '/budget/open';
  const response = await fetch(apiUrl(path), {
    method: 'POST', body: file,
    headers: {'Content-Type': 'application/zip', ...budgetPasswordHeaders(form.elements.password.value)},
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'The budget could not be opened.');
  return result;
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
  const readOnly = dialog.dataset.pluginReadOnly === 'true';
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
    button.disabled = readOnly;
    button.addEventListener('click', async () => {
      if (dialog.dataset.busy || readOnly) return;
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
        for (const control of content.querySelectorAll('button')) control.disabled = readOnly;
      }
    });
    row.append(button);
    content.append(row);
  }
  if (!settings.insights.length && !settings.actions.length && !dialog.querySelector('[data-appearance-choice], [data-file-import]')) content.append(settingsText('p', 'This plugin has no additional settings.', 'settings-notice'));
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
  if (input.closest('[data-transaction-register]')) continue;
  const initialValue = input.value;
  input.addEventListener('change', async () => {
    if (!input.checkValidity()) {
      input.reportValidity();
      return;
    }
    const url = pageUrl({[input.dataset.navigation]: input.value});
    if (currentRegister && !(await currentRegister.flush())) {
      input.value = initialValue;
      notify('Save the transaction edit before continuing.');
      return;
    }
    if (!budgetEdits.navigate(url.href, () => { input.value = initialValue; })) location.assign(url);
  });
}
document.addEventListener('click', event => {
  const button = event.target.closest('[data-budget-group]');
  if (!button) return;
  button.setAttribute('aria-expanded', String(button.getAttribute('aria-expanded') !== 'true'));
  syncBudgetGroups(document);
});
for (const time of document.querySelectorAll('[data-local-time]')) time.textContent = new Date(time.dateTime).toISOString().slice(0, 10);

currentRegister = initTransactionRegister({navigate: (...args) => accountNavigation.navigate(...args), pendingDestination: () => accountNavigation.pendingDestination(), cancelNavigation: () => accountNavigation.cancelPending()});
document.addEventListener('click', async event => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || !currentRegister?.dirty) return;
  const link = event.target.closest('a[href]');
  if (!link || link.hasAttribute('download') || link.target && link.target !== '_self') return;
  const target = new URL(link.href, location.href);
  if (target.origin !== location.origin || target.href.split('#')[0] === location.href.split('#')[0]) return;
  event.preventDefault();
  if (await currentRegister.flush()) location.assign(target.href);
  else notify('Save the transaction edit before continuing.');
});
