/* Account metadata stays in the addressed budget; opening settings loads one account. */
export function initAccountSettings({api, readForm, updateFields, flush, refresh}) {
  const form = document.querySelector('[data-account-classification]');
  if (!form) return;
  const dialog = form.closest('dialog');
  const error = form.querySelector('.field-error');
  const status = form.querySelector('[data-account-settings-status]');
  const submit = form.querySelector('[type="submit"]');
  const currency = form.elements.currency;
  let revision = 0;
  let originalCurrency;
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-edit-account]');
    if (!button || form.dataset.busy) return;
    const current = ++revision;
    form.reset();
    form.dataset.api = `/accounts/${encodeURIComponent(button.dataset.editAccount)}`;
    error.hidden = true;
    submit.disabled = true;
    currency.disabled = true;
    status.textContent = 'Loading account settings…';
    try {
      const result = await api(`${form.dataset.api}/settings?workspace=${encodeURIComponent(form.elements.workspace.value)}`);
      if (current !== revision) return;
      const account = result.account;
      form.elements.name.value = account.name;
      form.elements.onBudget.value = String(account.onBudget);
      updateFields(form);
      form.elements.type.value = account.type;
      updateFields(form);
      form.elements.subtype.value = account.subtype || '';
      currency.value = originalCurrency = account.currency;
      currency.disabled = !result.currencyEditable;
      if (form.elements.bankWebsite) form.elements.bankWebsite.value = account.bankWebsite || '';
      status.textContent = '';
      submit.disabled = false;
      if (dialog.open) form.elements.name.focus();
    } catch (failure) {
      if (current !== revision) return;
      status.textContent = '';
      error.textContent = failure.message;
      error.hidden = false;
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.busy || submit.disabled) return;
    form.dataset.busy = 'true';
    submit.disabled = true;
    error.hidden = true;
    status.textContent = 'Saving account…';
    try {
      if (!(await flush())) throw new Error('Save the transaction or budget edit before continuing.');
      const body = readForm(form);
      await api(form.dataset.api, body, 'PATCH');
      dialog.close();
      await refresh({currency: body.currency || originalCurrency, changedCurrency: Boolean(body.currency && body.currency !== originalCurrency)});
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      status.textContent = '';
      submit.disabled = false;
      delete form.dataset.busy;
    }
  });
  dialog.addEventListener('cancel', event => { if (form.dataset.busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { revision += 1; });
}
