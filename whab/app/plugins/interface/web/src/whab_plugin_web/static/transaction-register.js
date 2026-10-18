/* Small register controls. Financial validation and persistence stay on the server. */
import {api, apiUrl, notify, pageUrl} from './client.js';

export function createTransactionWriter(save, {saved = () => {}, state = () => {}} = {}) {
  let pending = {};
  let running = null;
  let failure = null;
  function flush() {
    if (running) return running;
    if (!Object.keys(pending).length) return Promise.resolve(!failure);
    running = (async () => {
      state('saving');
      while (Object.keys(pending).length) {
        const patch = pending;
        pending = {};
        try {
          await save(patch);
          saved(patch);
          failure = null;
        } catch (error) {
          pending = {...patch, ...pending};
          failure = error;
          state('error', error);
          return false;
        }
      }
      state('saved');
      return true;
    })().finally(() => { running = null; });
    return running;
  }
  return {
    push(patch) { pending = {...pending, ...patch}; return flush(); },
    flush,
    get dirty() { return Boolean(running || Object.keys(pending).length); },
  };
}

export function hydrateTransactionCategories(select, template) {
  if (!select?.hasAttribute('data-category-options-lazy') || !template?.content) return;
  const value = select.value;
  const placeholder = select.options[0].cloneNode(true);
  select.replaceChildren(placeholder, template.content.cloneNode(true));
  select.value = value;
  select.removeAttribute('data-category-options-lazy');
}

export function initTransactionRegister({navigate = async () => false, onMutation = () => {}} = {}) {
  const root = document.querySelector('[data-transaction-register]');
  if (!root) return {flush: async () => true, dispose() {}};
  const rows = [...root.querySelectorAll('.transaction-row')];
  const workspace = root.dataset.workspace;
  const search = root.querySelector('#transaction-search');
  const account = root.querySelector('#account-filter');
  const reviewFilter = root.querySelector('#review-filter');
  const count = root.querySelector('#transaction-count');
  const selectAll = root.querySelector('[data-select-all]');
  const selectionBar = root.querySelector('[data-transaction-selection-bar]');
  const feedback = root.querySelector('[data-transaction-feedback]');
  const live = root.querySelector('[data-transaction-save-status]');
  const editors = new Map();
  const timers = new Map();
  const listeners = [];
  let disposed = false;
  let bulkBusy = false;
  let bulkPromise = null;
  let receiptBusy = false;
  let receiptPromise = null;
  let receiptRow = null;
  let receiptObjectUrl = null;
  let receiptRequest = 0;
  const receiptIds = new Set();
  function listen(target, event, callback) {
    target.addEventListener(event, callback);
    listeners.push(() => target.removeEventListener(event, callback));
  }
  function error(message) {
    feedback.querySelector('[data-transaction-feedback-text]').textContent = message;
    feedback.hidden = false;
  }
  function selection() {
    const visible = rows.filter(row => !row.hidden);
    const selected = rows.filter(row => row.querySelector('[data-select-transaction]').checked);
    selectAll.disabled = !visible.length;
    selectAll.checked = Boolean(visible.length) && visible.every(row => row.querySelector('[data-select-transaction]').checked);
    selectAll.indeterminate = !selectAll.checked && selected.some(row => !row.hidden);
    selectionBar.hidden = !selected.length;
    selectionBar.querySelector('[data-transaction-selection-count]').textContent = `${selected.length} selected`;
    for (const row of rows) row.classList.toggle('is-selected', row.querySelector('[data-select-transaction]').checked);
    for (const button of selectionBar.querySelectorAll('[data-bulk-reviewed]')) {
      button.disabled = bulkBusy || !selected.some(row => row.dataset.pending !== 'true');
    }
    return selected;
  }
  function filter() {
    let visible = 0;
    for (const row of rows) {
      row.hidden = !row.dataset.payee.includes(search.value.toLowerCase()) ||
        (reviewFilter.getAttribute('aria-pressed') === 'true' && row.dataset.reviewed === 'true');
      if (row.hidden) row.querySelector('[data-select-transaction]').checked = false;
      else visible += 1;
    }
    count.textContent = `${visible} ${visible === 1 ? 'transaction' : 'transactions'} · ${count.dataset.period}`;
    selection();
  }
  async function go(url, focus = 'heading') {
    if (!(await flush())) { notify('Save the transaction edit before continuing.'); return; }
    if (!(await navigate(url, {focus}))) location.assign(url);
  }
  const categoryOptions = root.querySelector('template[data-transaction-categories]');
  const hydrateCategory = event => {
    if (event.target.matches('select[data-category-options-lazy]')) {
      hydrateTransactionCategories(event.target, categoryOptions);
    }
  };
  // Populate before the native picker opens, including touch and keyboard focus.
  listen(root, 'pointerdown', hydrateCategory);
  listen(root, 'focusin', hydrateCategory);
  listen(search, 'input', filter);
  listen(account, 'change', () => { void go(pageUrl({account: account.value}), '#account-filter'); });
  listen(root.querySelector('#all-dates-filter'), 'change', event => {
    void go(pageUrl({allDates: event.target.checked}), '#all-dates-filter');
  });
  listen(root.querySelector('[data-navigation="month"]'), 'change', event => {
    if (!event.target.checkValidity()) { event.target.reportValidity(); return; }
    void go(pageUrl({month: event.target.value}), '[data-navigation="month"]');
  });
  listen(reviewFilter, 'click', () => {
    const pressed = reviewFilter.getAttribute('aria-pressed') !== 'true';
    reviewFilter.setAttribute('aria-pressed', String(pressed));
    reviewFilter.classList.toggle('selected', pressed);
    filter();
  });
  listen(selectAll, 'change', () => {
    for (const row of rows) if (!row.hidden) row.querySelector('[data-select-transaction]').checked = selectAll.checked;
    selection();
  });
  listen(selectionBar.querySelector('[data-clear-selection]'), 'click', () => {
    for (const row of rows) row.querySelector('[data-select-transaction]').checked = false;
    selection();
  });
  for (const row of rows) {
    const memo = row.querySelector('[data-transaction-field="note"]');
    const category = row.querySelector('[data-transaction-field="categoryId"]');
    const reviewed = row.querySelector('[data-transaction-reviewed]');
    const committed = {note: memo.value, categoryId: category.value || null, reviewed: row.dataset.reviewed === 'true'};
    const writer = createTransactionWriter(patch => api(`/transactions/${encodeURIComponent(row.dataset.transactionId)}`, {workspace, ...patch}, 'PATCH'), {
      saved(patch) {
        Object.assign(committed, patch);
        if ('reviewed' in patch) row.dataset.reviewed = String(patch.reviewed);
        row.dataset.payee = `${row.dataset.merchant} ${memo.value}`.toLowerCase();
        onMutation();
        filter();
      },
      state(value, failure) {
        row.classList.toggle('is-saving', value === 'saving');
        row.classList.toggle('is-saved', value === 'saved');
        row.classList.toggle('has-error', value === 'error');
        if (value === 'saving') row.setAttribute('aria-busy', 'true');
        else row.removeAttribute('aria-busy');
        if (value === 'error') error(failure.message || 'The transaction could not be saved.');
        else if (value === 'saved') live.textContent = 'Transaction saved.';
      },
    });
    editors.set(row, writer);
    const saveMemo = () => {
      clearTimeout(timers.get(row));
      timers.delete(row);
      if (memo.value !== committed.note) return writer.push({note: memo.value});
      return writer.flush();
    };
    row.saveMemo = saveMemo;
    listen(row.querySelector('[data-select-transaction]'), 'change', selection);
    listen(category, 'change', () => {
      category.classList.toggle('uncategorized', !category.value && row.dataset.transfer !== 'true');
      void writer.push({categoryId: category.value || null});
    });
    listen(memo, 'input', () => {
      clearTimeout(timers.get(row));
      row.classList.remove('is-saved');
      row.dataset.payee = `${row.dataset.merchant} ${memo.value}`.toLowerCase();
      timers.set(row, setTimeout(saveMemo, 450));
    });
    listen(memo, 'change', saveMemo);
    listen(memo, 'keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void saveMemo(); memo.blur(); }
      if (event.key === 'Escape' && !writer.dirty) {
        clearTimeout(timers.get(row)); timers.delete(row); memo.value = committed.note; memo.blur();
      }
    });
    listen(reviewed, 'click', () => {
      const value = reviewed.getAttribute('aria-pressed') !== 'true';
      reviewed.setAttribute('aria-pressed', String(value));
      reviewed.title = value ? 'Reviewed' : 'Not reviewed';
      void writer.push({reviewed: value});
    });
  }
  for (const button of selectionBar.querySelectorAll('[data-bulk-reviewed]')) listen(button, 'click', () => {
    if (bulkBusy) return;
    bulkBusy = true;
    const selected = selection().filter(row => row.dataset.pending !== 'true');
    const value = button.dataset.bulkReviewed === 'true';
    const work = [...selected];
    bulkPromise = (async () => {
      let failed = 0;
      await Promise.all(Array.from({length: Math.min(4, work.length)}, async () => {
        while (work.length) {
          const row = work.shift();
          const control = row.querySelector('[data-transaction-reviewed]');
          control.setAttribute('aria-pressed', String(value));
          control.title = value ? 'Reviewed' : 'Not reviewed';
          if (!(await editors.get(row).push({reviewed: value}))) failed += 1;
        }
      }));
      if (failed) error(`${failed} transaction ${failed === 1 ? 'change could' : 'changes could'} not be saved. Retry to finish.`);
    })().finally(() => {
      bulkBusy = false;
      bulkPromise = null;
      selection();
    });
    return bulkPromise;
  });
  listen(root.querySelector('[data-transaction-retry]'), 'click', async () => { if (await flush()) feedback.hidden = true; });

  const receiptDialog = root.querySelector('[data-transaction-receipt-dialog]');
  const receiptImage = receiptDialog.querySelector('[data-receipt-preview]');
  const receiptError = receiptDialog.querySelector('[data-receipt-error]');
  const receiptStatus = receiptDialog.querySelector('[data-receipt-status]');
  const receiptFile = receiptDialog.querySelector('[data-receipt-file]');
  const removeReceipt = receiptDialog.querySelector('[data-receipt-remove]');
  function receiptUrl(row) { return apiUrl(`/transactions/${encodeURIComponent(row.dataset.transactionId)}/receipt?workspace=${workspace}`); }
  function receiptState(busy) {
    receiptBusy = busy;
    for (const control of receiptDialog.querySelectorAll('button,input')) control.disabled = busy;
  }
  function clearImage() {
    if (receiptObjectUrl) URL.revokeObjectURL(receiptObjectUrl);
    receiptObjectUrl = null;
    receiptImage.removeAttribute('src');
    receiptImage.hidden = true;
  }
  function markReceipt(row, exists) {
    if (exists) receiptIds.add(row.dataset.transactionId); else receiptIds.delete(row.dataset.transactionId);
    const button = row.querySelector('[data-transaction-receipt]');
    button.classList.toggle('has-receipt', exists);
    button.title = exists ? 'View receipt' : 'Add receipt';
    button.setAttribute('aria-label', `${exists ? 'View' : 'Add'} receipt for ${row.dataset.merchant}`);
  }
  async function loadReceipt(row) {
    const revision = ++receiptRequest;
    clearImage();
    receiptError.hidden = true;
    receiptDialog.querySelector('[data-receipt-empty]').hidden = receiptIds.has(row.dataset.transactionId);
    removeReceipt.hidden = !receiptIds.has(row.dataset.transactionId);
    if (!receiptIds.has(row.dataset.transactionId)) return;
    receiptStatus.textContent = 'Loading receipt…';
    try {
      const response = await fetch(receiptUrl(row), {cache: 'no-store'});
      if (!response.ok) throw new Error('The receipt could not be loaded.');
      const blob = await response.blob();
      if (revision !== receiptRequest || disposed) return;
      receiptObjectUrl = URL.createObjectURL(blob);
      receiptImage.src = receiptObjectUrl;
      receiptImage.hidden = false;
    } catch (failure) {
      if (revision !== receiptRequest || disposed) return;
      receiptError.textContent = failure.message; receiptError.hidden = false;
    } finally { if (revision === receiptRequest) receiptStatus.textContent = ''; }
  }
  for (const row of rows) listen(row.querySelector('[data-transaction-receipt]'), 'click', () => {
    receiptRow = row;
    receiptFile.value = '';
    receiptDialog.querySelector('[data-receipt-payee]').textContent = row.dataset.merchant;
    receiptDialog.showModal();
    void loadReceipt(row);
  });
  listen(receiptDialog.querySelector('[data-receipt-close]'), 'click', () => { if (!receiptBusy) receiptDialog.close(); });
  listen(receiptDialog, 'cancel', event => { if (receiptBusy) event.preventDefault(); });
  listen(receiptDialog, 'click', event => { if (event.target === receiptDialog && !receiptBusy) receiptDialog.close(); });
  listen(receiptDialog, 'close', () => { receiptRequest += 1; clearImage(); });
  async function saveReceipt(method, file) {
    if (receiptBusy || !receiptRow) return;
    const row = receiptRow;
    receiptError.hidden = true;
    receiptState(true);
    receiptStatus.textContent = method === 'PUT' ? 'Saving receipt…' : 'Removing receipt…';
    try {
      const response = await fetch(receiptUrl(row), {method, ...(file ? {body: file, headers: {'Content-Type': file.type}} : {})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'The receipt could not be saved.');
      markReceipt(row, method === 'PUT');
      receiptFile.value = '';
      onMutation();
      await loadReceipt(row);
      receiptStatus.textContent = method === 'PUT' ? 'Receipt saved.' : 'Receipt removed.';
    } catch (failure) { receiptError.textContent = failure.message; receiptError.hidden = false; }
    finally { receiptState(false); }
  }
  listen(receiptDialog.querySelector('[data-receipt-form]'), 'submit', event => {
    event.preventDefault();
    const file = receiptFile.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024 || !['image/png','image/jpeg','image/webp'].includes(file.type)) {
      receiptError.textContent = 'Choose a JPEG, PNG or WebP image no larger than 5 MiB.'; receiptError.hidden = false; return;
    }
    receiptPromise = saveReceipt('PUT', file).finally(() => { receiptPromise = null; });
  });
  listen(removeReceipt, 'click', () => { receiptPromise = saveReceipt('DELETE').finally(() => { receiptPromise = null; }); });
  api(`/receipts?workspace=${workspace}`).then(result => {
    if (disposed) return;
    for (const id of result.transactionIds || []) receiptIds.add(id);
    for (const row of rows) {
      const button = row.querySelector('[data-transaction-receipt]');
      button.disabled = result.supported !== true;
      if (result.supported) markReceipt(row, receiptIds.has(row.dataset.transactionId));
      else button.title = 'Receipts are unavailable with this storage plugin.';
    }
  }).catch(() => { if (!disposed) for (const row of rows) row.querySelector('[data-transaction-receipt]').title = 'Receipts could not be loaded.'; });
  async function flush() {
    if (disposed) return true;
    if (bulkPromise) await bulkPromise;
    const results = await Promise.all(rows.map(row => row.saveMemo()));
    if (receiptPromise) await receiptPromise;
    const ok = results.every(Boolean);
    if (ok) feedback.hidden = true;
    return ok;
  }
  function beforeUnload(event) {
    if (bulkBusy || [...editors.values()].some(writer => writer.dirty) || timers.size || receiptBusy) { event.preventDefault(); event.returnValue = ''; }
  }
  listen(window, 'beforeunload', beforeUnload);
  selection();
  return {
    flush,
    get dirty() { return bulkBusy || [...editors.values()].some(writer => writer.dirty) || timers.size > 0 || receiptBusy; },
    dispose() {
      disposed = true;
      for (const timer of timers.values()) clearTimeout(timer);
      for (const remove of listeners) remove();
      clearImage();
    },
  };
}
