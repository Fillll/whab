/* Small register controls. Financial validation and persistence stay on the server. */
import {api, apiUrl, getContext, notify, pageUrl, updateContext} from './client.js';
import {initAccountPicker} from './account-picker.js';
import {initPeriodPicker} from './period-picker.js';
import {initTransactionGrid, parseRegisterAmount} from './transaction-grid.js';
import {initTransactionStream} from './transaction-stream.js';
import {bindLogos} from './logo-images.js';

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

/* A failed batch keeps its identity until each affected writer confirms its result. */
export function createReviewBatcher({workspace, saveBatch, saveOne}) {
  const collecting = new Map();
  const records = new Map();

  function collect(transactionId, reviewed) {
    let ticket = collecting.get(reviewed);
    if (!ticket || ticket.ids.length === 100) {
      let ready;
      ticket = {ids: [], reviewed, requestId: crypto.randomUUID(),
        ready: new Promise(resolve => {ready = resolve;}), running: null, confirmed: false};
      collecting.set(reviewed, ticket);
      const queued = ticket;
      queueMicrotask(() => {
        if (collecting.get(reviewed) === queued) collecting.delete(reviewed);
        queued.body = Object.freeze({workspace, transactionIds: Object.freeze([...queued.ids]),
          reviewed, requestId: queued.requestId});
        ready();
      });
    }
    ticket.ids.push(transactionId);
    records.set(transactionId, ticket);
    return ticket;
  }

  async function confirm(ticket) {
    await ticket.ready;
    if (ticket.confirmed) return;
    if (!ticket.running) {
      ticket.running = Promise.resolve().then(() => saveBatch(ticket.body)).then(result => {
        if (result?.ok !== true) throw new Error('Review changes could not be confirmed. Retry to finish.');
        ticket.confirmed = true;
      }).finally(() => {ticket.running = null;});
    }
    return ticket.running;
  }

  return async (transactionId, patch, batch = false) => {
    let ticket = records.get(transactionId);
    if (!ticket && batch && Object.keys(patch).length === 1 && typeof patch.reviewed === 'boolean') {
      ticket = collect(transactionId, patch.reviewed);
    }
    if (!ticket) return saveOne(transactionId, patch);
    await confirm(ticket);
    const remaining = {...patch};
    if (remaining.reviewed === ticket.reviewed) delete remaining.reviewed;
    if (Object.keys(remaining).length) await saveOne(transactionId, remaining);
    if (records.get(transactionId) === ticket) records.delete(transactionId);
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

export function initTransactionRegister({navigate = async () => false, pendingDestination = () => null, cancelNavigation = () => {}, onMutation = () => {}} = {}) {
  const root = document.querySelector('[data-transaction-register]');
  if (!root) return {flush: async () => true, dispose() {}};
  const rows = [...root.querySelectorAll('.transaction-row')];
  let stream = null;
  const workspace = root.dataset.workspace;
  const grid = initTransactionGrid(root.querySelector('.transactions-table'));
  const createForm = root.querySelector('[data-transaction-create]');
  const draft = root.querySelector('[data-new-transaction]');
  let creating = false, createRequestId = null;
  const search = root.querySelector('#transaction-search');
  const account = root.querySelector('#account-filter');
  const reviewFilter = root.querySelector('#review-filter');
  const count = root.querySelector('#transaction-count');
  const selectAll = root.querySelector('[data-select-all]');
  const selectionBar = root.querySelector('[data-transaction-selection-bar]');
  const feedback = root.querySelector('[data-transaction-feedback]');
  const live = root.querySelector('[data-transaction-save-status]');
  const paged = root.dataset.transactionPaged === 'true';
  const displayedSearch = search.value;
  const displayedReview = reviewFilter.dataset.reviewState || 'all';
  const accountPopup = root.querySelector('[data-account-picker-popup]');
  const periodPopup = root.querySelector('[data-period-popup]');
  let searchTimer = null;
  let refreshTimer = null;
  let refreshNeeded = false;
  let refreshRetries = 0;
  if (paged) updateContext({transactionSearch: search.value, transactionPage: Number(root.dataset.transactionPage), transactionRequestedPage: root.dataset.transactionRequestedPage});
  const editors = new Map();
  const timers = new Map();
  const listeners = [];
  let disposed = false;
  let pendingFilters = {};
  let filterNavigationPending = false, filterNavigationRevision = 0;
  let bulkBusy = false;
  let bulkPromise = null;
  const nativeBulk = getContext().bulkReview === true;
  const saveOne = (transactionId, patch) => api(`/transactions/${encodeURIComponent(transactionId)}`, {workspace, ...patch}, 'PATCH');
  const saveReview = nativeBulk ? createReviewBatcher({workspace, saveOne,
    saveBatch: body => api('/transactions/review', body, 'PUT')}) : null;
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
        (reviewFilter.dataset.reviewState === 'needs-review' && row.dataset.reviewed === 'true') ||
        (reviewFilter.dataset.reviewState === 'reviewed' && row.dataset.reviewed !== 'true');
      if (row.hidden) row.querySelector('[data-select-transaction]').checked = false;
      else visible += 1;
    }
    count.textContent = `${visible} ${visible === 1 ? 'transaction' : 'transactions'} · ${count.dataset.period}`;
    selection();
  }
  async function go(url, focus = 'heading', refresh = false, summary = false) {
    stream?.cancel();
    const revision = ++filterNavigationRevision;
    filterNavigationPending = true;
    try {
      if (!(await flush())) { if (revision === filterNavigationRevision) notify('Save the transaction edit before continuing.'); return; }
      if (revision !== filterNavigationRevision) return;
      const handled = await navigate(url, {focus, ...(refresh ? {refresh: true, history: 'replace'} : {}), ...(summary ? {summary: true} : {})});
      if (!handled && !summary) location.assign(url);
      if (refresh && handled && revision === filterNavigationRevision) refreshNeeded = false;
    } finally {
      if (revision === filterNavigationRevision) {
        filterNavigationPending = false;
        if (refresh && refreshNeeded && refreshRetries < 1) {
          refreshRetries += 1;
          refreshAfterMutation(true);
        }
      }
    }
  }
  function applyFilters(changes, focus) {
    pendingFilters = {...pendingFilters, ...(paged ? {q: search.value} : {}), ...changes, review: reviewFilter.dataset.reviewState || 'all'};
    void go(pageUrl(pendingFilters), focus);
  }
  function refreshAfterMutation(retry = false) {
    refreshNeeded = true;
    // A canceled request gets one idle retry; later interaction or mutation can retry again.
    if (!retry) refreshRetries = 0;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      if (!disposed && !filterNavigationPending && !pendingDestination() && !timers.size && !searchTimer &&
          search.value === displayedSearch && accountPopup?.hidden !== false && periodPopup?.hidden !== false &&
          document.activeElement?.matches?.('[data-transaction-field="note"]') !== true &&
          !document.activeElement?.closest?.('td.grid-current') && (!draft || draft.hidden) &&
          !receiptDialog.open && !receiptBusy && !bulkBusy &&
          ![...editors.values()].some(writer => writer.dirty)) {
        void go(new URL(location.href), false, true, !search.value && displayedReview === 'all');
      }
    }, 0);
  }
  function editStarted() {
    stream?.cancel();
    const interruptedFilters = filterNavigationPending || pendingDestination() || Object.keys(pendingFilters).length;
    clearTimeout(refreshTimer);
    clearTimeout(searchTimer);
    searchTimer = null;
    if (paged) {
      search.value = displayedSearch;
      if (reviewFilter.dataset.reviewState !== displayedReview) setReviewState(displayedReview);
      pendingFilters = {};
      if (interruptedFilters) accountPicker.restore?.();
    }
    filterNavigationRevision += 1;
    filterNavigationPending = false;
    cancelNavigation();
  }
  function setReviewState(next) {
    reviewFilter.dataset.reviewState = next;
    const labels = {all: 'All', 'needs-review': 'Needs review', reviewed: 'Reviewed'};
    const nextLabels = {all: 'Needs review', 'needs-review': 'Reviewed', reviewed: 'All'};
    reviewFilter.querySelector('[data-review-label]').textContent = labels[next];
    reviewFilter.setAttribute('aria-label', `Review status: ${labels[next]}. Click for ${nextLabels[next]}.`);
    updateContext({reviewFilter: next});
  }
  listen(document, 'click', () => { if (refreshNeeded) refreshAfterMutation(); stream?.check(); });
  const accountPicker = initAccountPicker(root, accounts => {
    applyFilters({accounts}, '#account-filter-toggle');
  });
  const periodPicker = initPeriodPicker(root, changes => applyFilters(changes, '#transaction-period-toggle'));
  const categoryOptions = root.querySelector('template[data-transaction-categories]');
  const accountOptions = root.querySelector('template[data-transaction-accounts]');
  const hydrateCategory = event => {
    if (event.target.matches('select[data-account-options-lazy]') && accountOptions?.content) {
      const value = event.target.value; event.target.replaceChildren(accountOptions.content.cloneNode(true));
      event.target.value = value; event.target.removeAttribute('data-account-options-lazy');
    }
    if (event.target.matches('select[data-category-options-lazy]')) {
      hydrateTransactionCategories(event.target, categoryOptions);
    }
  };
  // Populate before the native picker opens, including touch and keyboard focus.
  listen(root, 'pointerdown', hydrateCategory);
  listen(root, 'focusin', hydrateCategory);
  if (paged) {
    const searchForm = root.querySelector('[data-transaction-search-form]');
    const searchNow = () => {
      clearTimeout(searchTimer);
      searchTimer = null;
      applyFilters({q: search.value}, '#transaction-search');
    };
    listen(search, 'input', event => {
      clearTimeout(searchTimer);
      filterNavigationRevision += 1;
      filterNavigationPending = false;
      cancelNavigation();
      if (!event.isComposing) searchTimer = setTimeout(searchNow, 300);
    });
    listen(search, 'compositionend', searchNow);
    listen(searchForm, 'submit', event => { event.preventDefault(); searchNow(); });
    for (const link of root.querySelectorAll('[data-transaction-page]')) listen(link, 'click', event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      clearTimeout(searchTimer);
      searchTimer = null;
      if (search.value !== displayedSearch) applyFilters({q: search.value}, '#transaction-search');
      else void go(link.href, false);
    });
  } else listen(search, 'input', filter);
  listen(account, 'change', () => {
    applyFilters({accounts: [...account.selectedOptions].map(option => option.value).filter(value => value !== 'all')}, '#account-filter');
  });
  listen(reviewFilter, 'click', () => {
    const next = {all: 'needs-review', 'needs-review': 'reviewed', reviewed: 'all'}[reviewFilter.dataset.reviewState || 'all'];
    setReviewState(next);
    pendingFilters.review = next;
    if (paged) {
      clearTimeout(searchTimer);
      searchTimer = null;
      const pending = pendingDestination();
      if (pending) {
        const url = new URL(pending);
        url.searchParams.delete('tx_page');
        if (next === 'all') url.searchParams.delete('review');
        else url.searchParams.set('review', next);
        void go(url, '#review-filter');
      } else applyFilters({review: next}, '#review-filter');
      return;
    }
    filter();
    const pending = pendingDestination();
    if (pending) {
      const url = new URL(pending);
      if (next === 'all') url.searchParams.delete('review');
      else url.searchParams.set('review', next);
      void go(url, '#review-filter');
    } else if (filterNavigationPending) applyFilters({review: next}, '#review-filter');
    else {
      const url = pageUrl({review: next});
      window.history.replaceState(window.history.state, '', url.href);
    }
  });
  listen(selectAll, 'change', () => {
    for (const row of rows) if (!row.hidden) row.querySelector('[data-select-transaction]').checked = selectAll.checked;
    selection();
  });
  listen(selectionBar.querySelector('[data-clear-selection]'), 'click', () => {
    for (const row of rows) row.querySelector('[data-select-transaction]').checked = false;
    selection();
  });
  function bindRow(row) {
    const memo = row.querySelector('[data-transaction-field="note"]');
    const category = row.querySelector('[data-transaction-field="categoryId"]');
    const reviewed = row.querySelector('[data-transaction-reviewed]');
    const detailFields = [...row.querySelectorAll('[data-transaction-detail]')];
    const detailValue = control => control.dataset.transactionDetail === 'amount' ? parseRegisterAmount(control.value, Number(control.dataset.minorUnits), true) : control.value;
    const committed = { ...Object.fromEntries(detailFields.map(control => [control.dataset.transactionDetail, detailValue(control)])), note: memo.value, categoryId: category.value || null, reviewed: row.dataset.reviewed === 'true'};
    const prepareReview = patch => row.dataset.suggestionId && patch.reviewed === true ?
      {...patch, categoryId: category.value || null, suggestionId: row.dataset.suggestionId} : patch;
    const writer = createTransactionWriter(patch => {
      const prepared = prepareReview(patch);
      return saveReview ? saveReview(row.dataset.transactionId, prepared, bulkBusy) : saveOne(row.dataset.transactionId, prepared);
    }, {
      saved(patch) {
        Object.assign(committed, patch);
        if ('reviewed' in patch) row.dataset.reviewed = String(patch.reviewed);
        if (patch.reviewed === true && row.dataset.suggestionId) {
          delete row.dataset.suggestionId;
          row.querySelector('.ml-suggestion')?.remove();
        }
        if ('merchant' in patch) row.dataset.merchant = patch.merchant;
        if ('accountId' in patch) row.dataset.account = patch.accountId;
        const amountField = detailFields.find(control => control.dataset.transactionDetail === 'amount');
        if (amountField) amountField.closest('td').classList.toggle('inflow', Number(amountField.value) > 0);
        row.dataset.payee = `${row.dataset.merchant} ${memo.value}`.toLowerCase();
        onMutation();
        if (stream) {
          const context = getContext();
          const outsideAccount = 'accountId' in patch && context.selectedAccountIds?.length && !context.selectedAccountIds.includes(patch.accountId);
          const outsideDates = 'date' in patch && (context.startDate && patch.date < context.startDate || context.endDate && patch.date > context.endDate);
          if (!row.hidden && (outsideAccount || outsideDates)) {
            row.hidden = true; row.querySelector('[data-select-transaction]').checked = false; selection();
            const remaining = Math.max(0, Number(root.dataset.streamCount) - 1); root.dataset.streamCount = String(remaining);
            count.textContent = count.textContent.replace(/^\d+/, String(remaining));
          }
          stream.mutation('date' in patch || 'accountId' in patch || Boolean(search.value && ('merchant' in patch || 'note' in patch)) || displayedReview !== 'all' && 'reviewed' in patch);
        }
        if (!paged) filter();
        else if (!bulkBusy && (refreshNeeded || search.value && 'note' in patch && document.activeElement !== memo || 'reviewed' in patch || detailFields.some(control => control.dataset.transactionDetail in patch))) {
          refreshAfterMutation();
        }
      },
      state(value, failure) {
        row.classList.toggle('is-saving', value === 'saving');
        row.classList.toggle('is-saved', value === 'saved');
        row.classList.toggle('has-error', value === 'error');
        if (value === 'saving') row.setAttribute('aria-busy', 'true');
        else row.removeAttribute('aria-busy');
        if (value === 'error') error(failure.message || 'The transaction could not be saved.');
        else if (value === 'saved') {live.textContent = 'Transaction saved.'; stream?.check();}
      },
    });
    editors.set(row, writer);
    const saveMemo = () => {
      clearTimeout(timers.get(row));
      timers.delete(row);
      const patch = {};
      try {
        for (const control of detailFields) {
          control.setCustomValidity('');
          if (!control.checkValidity()) throw new Error(control.validationMessage);
          const value = detailValue(control);
          if (value !== committed[control.dataset.transactionDetail]) patch[control.dataset.transactionDetail] = value;
        }
      } catch (failure) {error(failure.message); row.classList.add('has-error'); return Promise.resolve(false);}
      if (memo.value !== committed.note) patch.note = memo.value;
      return Object.keys(patch).length ? writer.push(patch) : writer.flush();
    };
    row.saveMemo = saveMemo;
    row.hasDraft = () => {
      try {return memo.value !== committed.note || detailFields.some(control => detailValue(control) !== committed[control.dataset.transactionDetail]);}
      catch {return true;}
    };
    for (const control of detailFields) {
      listen(control, 'input', () => {editStarted(); row.classList.remove('is-saved');});
      listen(control, 'change', () => {editStarted(); void saveMemo();});
    }
    listen(row.querySelector('[data-select-transaction]'), 'change', selection);
    listen(category, 'change', () => {
      editStarted();
      category.classList.toggle('uncategorized', !category.value && row.dataset.transfer !== 'true');
      if (!row.dataset.suggestionId) void writer.push({categoryId: category.value || null});
    });
    if (row.dataset.suggestionId) listen(row.querySelector('[data-dismiss-suggestion]'), 'click', async () => {
      try {
        await api('/categorization', {workspace, payload: {action: 'dismiss', pluginId: row.dataset.suggestionPlugin,
          transactionId: row.dataset.transactionId, suggestionId: row.dataset.suggestionId}});
        delete row.dataset.suggestionId;
        category.value = ''; row.querySelector('.ml-suggestion')?.remove();
        onMutation();
      } catch (failure) { error(failure.message); }
    });
    listen(memo, 'input', () => {
      editStarted();
      clearTimeout(timers.get(row));
      row.classList.remove('is-saved');
      row.dataset.payee = `${row.dataset.merchant} ${memo.value}`.toLowerCase();
      timers.set(row, setTimeout(saveMemo, 450));
    });
    listen(memo, 'change', saveMemo);
    if (paged) listen(memo, 'blur', () => {
      if (refreshNeeded || search.value && memo.value === committed.note) refreshAfterMutation();
    });
    listen(memo, 'keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void saveMemo(); memo.blur(); }
      if (event.key === 'Escape' && !writer.dirty) {
        clearTimeout(timers.get(row)); timers.delete(row); memo.value = committed.note; memo.blur();
      }
    });
    listen(reviewed, 'click', () => {
      editStarted();
      const value = reviewed.getAttribute('aria-pressed') !== 'true';
      reviewed.setAttribute('aria-pressed', String(value));
      reviewed.title = value ? 'Reviewed' : 'Not reviewed';
      void writer.push({reviewed: value});
    });
  }
  for (const row of rows) bindRow(row);
  for (const button of selectionBar.querySelectorAll('[data-bulk-reviewed]')) listen(button, 'click', () => {
    if (bulkBusy) return;
    editStarted();
    bulkBusy = true;
    const selected = selection().filter(row => row.dataset.pending !== 'true');
    const value = button.dataset.bulkReviewed === 'true';
    const work = [...selected];
    bulkPromise = (async () => {
      let failed = 0;
      await Promise.all(Array.from({length: Math.min(nativeBulk && !selected.some(row => row.dataset.suggestionId) ? 100 : 4, work.length)}, async () => {
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
      if (paged) refreshAfterMutation();
    });
    return bulkPromise;
  });
  listen(root.querySelector('[data-transaction-retry]'), 'click', async () => { if (await flush()) feedback.hidden = true; });

  const receiptDialog = root.querySelector('[data-transaction-receipt-dialog]');
  const receiptImage = receiptDialog.querySelector('[data-receipt-preview]');
  const receiptPdf = receiptDialog.querySelector('[data-receipt-pdf]');
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
    receiptPdf.removeAttribute('href');
    receiptPdf.hidden = true;
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
      if (blob.type === 'application/pdf') {
        receiptPdf.href = receiptUrl(row);
        receiptPdf.hidden = false;
        return;
      }
      receiptObjectUrl = URL.createObjectURL(blob);
      receiptImage.src = receiptObjectUrl;
      receiptImage.hidden = false;
    } catch (failure) {
      if (revision !== receiptRequest || disposed) return;
      receiptError.textContent = failure.message; receiptError.hidden = false;
    } finally { if (revision === receiptRequest) receiptStatus.textContent = ''; }
  }
  function bindReceiptRow(row) {listen(row.querySelector('[data-transaction-receipt]'), 'click', () => {
    editStarted();
    receiptRow = row;
    receiptFile.value = '';
    receiptDialog.querySelector('[data-receipt-payee]').textContent = row.dataset.merchant;
    receiptDialog.showModal();
    void loadReceipt(row);
  });}
  for (const row of rows) bindReceiptRow(row);
  listen(receiptDialog.querySelector('[data-receipt-close]'), 'click', () => { if (!receiptBusy) receiptDialog.close(); });
  listen(receiptFile, 'change', editStarted);
  listen(receiptDialog, 'cancel', event => { if (receiptBusy) event.preventDefault(); });
  listen(receiptDialog, 'click', event => { if (event.target === receiptDialog && !receiptBusy) receiptDialog.close(); });
  listen(receiptDialog, 'close', () => {
    receiptRequest += 1;
    clearImage();
    if (paged && (refreshNeeded || search.value || reviewFilter.dataset.reviewState !== 'all')) refreshAfterMutation();
  });
  async function saveReceipt(method, file) {
    if (receiptBusy || !receiptRow) return;
    editStarted();
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
    if (file.size > 5 * 1024 * 1024 || !['image/jpeg','image/png','application/pdf'].includes(file.type)) {
      receiptError.textContent = 'Choose a JPEG, PNG or PDF file no larger than 5 MiB.'; receiptError.hidden = false; return;
    }
    receiptPromise = saveReceipt('PUT', file).finally(() => { receiptPromise = null; });
  });
  listen(removeReceipt, 'click', () => { receiptPromise = saveReceipt('DELETE').finally(() => { receiptPromise = null; }); });
  function loadReceiptMarkers(targetRows) {
    if (!targetRows.length) return;
    const request = getContext().receiptIndexScoped === true ?
      api('/receipts/index', {workspace, transactionIds: targetRows.map(row => row.dataset.transactionId)}, 'POST') :
      api(`/receipts?workspace=${workspace}`);
    request.then(result => {
      if (disposed) return;
      for (const id of result.transactionIds || []) receiptIds.add(id);
      for (const row of targetRows) {
        const button = row.querySelector('[data-transaction-receipt]');
        button.disabled = result.supported !== true;
        if (result.supported) markReceipt(row, receiptIds.has(row.dataset.transactionId));
        else button.title = 'Receipts are unavailable with this storage plugin.';
      }
    }).catch(() => {if (!disposed) for (const row of targetRows) row.querySelector('[data-transaction-receipt]').title = 'Receipts could not be loaded.';});
  }
  loadReceiptMarkers(rows);
  if (createForm && draft) {
    listen(root.querySelector('[data-add-transaction]'), 'click', () => {
      editStarted(); draft.hidden = false; createRequestId ||= crypto.randomUUID();
      const empty = root.querySelector('.empty-state'); if (empty) empty.hidden = true;
      grid.focus(draft.querySelector('[name="merchant"]'));
    });
    listen(root.querySelector('[data-cancel-transaction]'), 'click', () => {
      if (creating) return;
      createForm.reset(); draft.hidden = true; createRequestId = null; feedback.hidden = true;
      const empty = root.querySelector('.empty-state'); if (empty) empty.hidden = false;
      root.querySelector('[data-add-transaction]').focus();
    });
    listen(draft, 'input', editStarted);
    listen(draft, 'change', editStarted);
    listen(createForm, 'submit', async event => {
      event.preventDefault();
      if (creating) return;
      for (const control of createForm.elements) if ('readOnly' in control) control.readOnly = false;
      if (!createForm.reportValidity()) return;
      const fields = Object.fromEntries(new FormData(createForm));
      try {
        const amountControl = createForm.elements.namedItem('amount');
        fields.amount = parseRegisterAmount(fields.amount, Number(amountControl.dataset.minorUnits));
        fields.categoryId ||= null;
        if (!(await flushRows())) return;
        creating = true; editStarted();
        for (const control of createForm.elements) control.disabled = true;
        await api('/transactions', {workspace, ...fields, requestId: createRequestId});
        draft.hidden = true; createForm.reset(); createRequestId = null; creating = false;
        onMutation(); notify('Transaction saved. Active filters determine where it appears.');
        await go(new URL(location.href), false, true);
      } catch (failure) {error(failure.message || 'The transaction could not be saved.');}
      finally {creating = false; for (const control of createForm.elements) control.disabled = false;}
    });
  }
  async function flushRows() {
    if (disposed) return true;
    if (bulkPromise) await bulkPromise;
    const results = await Promise.all(rows.map(row => row.saveMemo()));
    if (receiptPromise) await receiptPromise;
    const ok = results.every(Boolean);
    if (ok) feedback.hidden = true;
    return ok;
  }
  async function flush() {
    if (draft && !draft.hidden) return false;
    return flushRows();
  }
  function beforeUnload(event) {
    if (creating || draft && !draft.hidden || rows.some(row => row.hasDraft()) || bulkBusy || [...editors.values()].some(writer => writer.dirty) || timers.size || receiptBusy) { event.preventDefault(); event.returnValue = ''; }
  }
  stream = initTransactionStream({root, context: {...getContext()},
    ready: () => !disposed && !filterNavigationPending && !pendingDestination() && !searchTimer &&
      search.value === displayedSearch && accountPopup?.hidden !== false && periodPopup?.hidden !== false &&
      !creating && (!draft || draft.hidden) && !bulkBusy && !receiptBusy && !timers.size &&
      !rows.some(row => row.hasDraft()) && ![...editors.values()].some(writer => writer.dirty),
    changed: () => {notify('Transactions changed. Refreshing this view.'); void go(new URL(location.href), 'heading', true);},
    append(incoming, direction) {
      const known = new Set(rows.map(row => row.dataset.transactionId));
      const added = incoming.filter(row => !known.has(row.dataset.transactionId));
      if (!added.length) return;
      const body = root.querySelector('.transactions-table tbody'), anchor = body.querySelector('.transaction-row');
      for (const row of added) {
        if (direction === 'previous') body.insertBefore(row, anchor);
        else body.append(row);
      }
      if (direction === 'previous') rows.unshift(...added); else rows.push(...added);
      for (const row of added) {bindRow(row); bindReceiptRow(row);}
      loadReceiptMarkers(added); bindLogos(); selection();
    },
  });
  listen(window, 'beforeunload', beforeUnload);
  if (paged) selection();
  else filter();
  return {
    flush,
    get dirty() { return creating || Boolean(draft && !draft.hidden) || rows.some(row => row.hasDraft()) || bulkBusy || [...editors.values()].some(writer => writer.dirty) || timers.size > 0 || receiptBusy; },
    dispose() {
      disposed = true;
      clearTimeout(searchTimer);
      clearTimeout(refreshTimer);
      stream?.dispose();
      grid.dispose();
      accountPicker.dispose();
      periodPicker.dispose();
      for (const timer of timers.values()) clearTimeout(timer);
      for (const remove of listeners) remove();
      clearImage();
    },
  };
}
