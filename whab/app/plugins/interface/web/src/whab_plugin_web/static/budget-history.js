/* Permanent financial history. Every reversal is previewed and explicitly saved. */
const operations = {add_account:'Add account', update_account:'Edit account', add_category:'Add category', add_transaction:'Add transaction', edit_transaction:'Edit transaction', edit_transaction_details:'Edit transaction', review_transactions:'Review transactions', assign:'Assign budget', assign_next_month:'Set aside next month', set_rollover_mode:'Change carryover', apply_sync:'Bank sync', import_workspace:'Import', save_receipt:'Add receipt', delete_receipt:'Remove receipt', change:'Financial change', history:'History action'};
const fields = {categoryId:'Category', categoryAgent:'Categorized by', reviewed:'Reviewed', categoryEdited:'Category override', isTransfer:'Transfer exclusion', inferredTransfer:'Bank transfer hint', merchant:'Payee', accountId:'Account', note:'Memo', amount:'Amount', balance:'Balance', onBudget:'On budget', budgetStartMonth:'Budget start month'};
export function changedFields(change) {
  if (!change.before || !change.after || typeof change.before !== 'object') return [{field:'Record', before:change.before, after:change.after}];
  return [...new Set([...Object.keys(change.before), ...Object.keys(change.after)])]
    .filter(key => JSON.stringify(change.before[key]) !== JSON.stringify(change.after[key]))
    .map(key => ({field:fields[key] || key, before:change.before[key] ?? null, after:change.after[key] ?? null}));
}
export function initBudgetHistory({api, getContext, beforeChange, onChanged = () => {}, document: page = document}) {
  const root = page.querySelector('[data-budget-history]');
  if (!root) return null;
  const rows = root.querySelector('[data-history-rows]'), error = root.querySelector('[data-history-error]');
  const previewBox = root.querySelector('[data-history-preview]'), changesBox = root.querySelector('[data-history-changes]');
  const apply = root.querySelector('[data-history-apply]'), retry = root.querySelector('[data-history-retry]');
  const status = root.querySelector('[data-history-status]');
  let current = null, next = null, serial = 0, detailSerial = 0, intent = null, busy = false;
  const workspace = () => getContext().workspace;
  const storageKey = () => `whab-history-intent:${getContext().budgetUrl}:${workspace()}`;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey()) || 'null');
    if (saved && saved.workspace === workspace() && ['undo','redo','restore','snapshot'].includes(saved.action) &&
        typeof saved.fingerprint === 'string' && /^[a-f0-9]{64}$/.test(saved.fingerprint) &&
        typeof saved.requestId === 'string' && typeof saved.revision === 'string') intent = saved;
  } catch { /* A damaged local notice cannot authorize a mutation. */ }
  function pending() {
    retry.hidden = !intent;
    apply.disabled = busy || Boolean(intent) || !current;
    if (intent) status.textContent = 'A previous history request needs confirmation. Inspect history or retry that same request.';
  }
  function setBusy(value) {
    busy = value;
    if (value) root.dataset.busy = 'true'; else delete root.dataset.busy;
    root.setAttribute('aria-busy', String(value));
    for (const button of root.querySelectorAll('button')) button.disabled = value;
    pending();
  }
  function fail(failure) {error.textContent = failure.message; error.hidden = false;}
  function text(tag, value, className) {
    const item = page.createElement(tag); item.textContent = value;
    if (className) item.className = className;
    return item;
  }
  function button(label, action) {
    const item = text('button', label, 'button secondary small'); item.type = 'button';
    item.addEventListener('click', action); return item;
  }
  async function load(older = false) {
    if (busy) return;
    const token = ++serial;
    error.hidden = true;
    try {
      const suffix = `/history?workspace=${encodeURIComponent(workspace())}&limit=50${older && next ? `&before=${encodeURIComponent(next)}` : ''}`;
      const result = await api(suffix);
      if (token !== serial) return;
      if (!older) rows.replaceChildren();
      next = result.data.next;
      for (const entry of result.data.entries) {
        const row = page.createElement('article'); row.className = 'history-row';
        const title = entry.action === 'change' ? (operations[entry.operation] || 'Financial change') : `${entry.action[0].toUpperCase()}${entry.action.slice(1)}`;
        row.append(text('strong', title), text('small', `${new Date(entry.time).toLocaleString()} · ${entry.actor}${entry.actorKind === 'agent' ? ' (agent)' : ''} · ${entry.changeCount} changed records`));
        if (entry.batchId) row.append(text('small', `Batch: ${entry.batchId}`));
        const actions = page.createElement('div'); actions.className = 'history-actions';
        actions.append(button('Details', () => show(entry.id)));
        if (entry.reversible && entry.action === 'change') {
          actions.append(button('Undo', () => prepare('undo', entry.id)), button('Redo', () => prepare('redo', entry.id)));
          if (entry.batchId) actions.append(button('Undo batch', () => prepare('undo', entry.batchId)), button('Redo batch', () => prepare('redo', entry.batchId)));
        }
        if (entry.hasSnapshot) actions.append(button('Restore snapshot', () => prepare('restore', entry.id)));
        row.append(actions); rows.append(row);
      }
      if (!rows.children.length) rows.append(text('p', 'No changes recorded yet. History begins with your next financial change.'));
      root.querySelector('[data-history-older]').hidden = !next;
      pending();
    } catch (failure) {if (token === serial) fail(failure);}
  }
  function renderChanges(changes) {
    changesBox.replaceChildren();
    for (const change of changes) {
      const row = page.createElement('article'); row.className = 'history-change';
      const record = change.after || change.before;
      row.append(text('strong', `${change.path[0]} · ${record?.merchant || record?.name || change.path.slice(1).join(' / ') || 'Budget'}`));
      for (const part of changedFields(change)) {
        const line = page.createElement('div'); line.className = 'history-change-values';
        const value = input => input === null ? 'None' : typeof input === 'object' ? JSON.stringify(input) : String(input);
        line.append(text('span', part.field), text('span', value(part.before)), text('span', '→'), text('span', value(part.after)));
        row.append(line);
      }
      changesBox.append(row);
    }
  }
  async function show(target) {
    if (busy) return;
    const token = ++detailSerial;
    error.hidden = true; current = null; pending();
    try {
      const result = await api(`/history?workspace=${encodeURIComponent(workspace())}&target=${encodeURIComponent(target)}`);
      if (token !== detailSerial) return;
      renderChanges(result.data.entries.flatMap(entry => entry.changes)); previewBox.hidden = false;
      status.textContent = 'Recorded changes. Choose Undo or Redo on an entry to preview a new action.';
    } catch (failure) {fail(failure);}
  }
  async function prepare(action, target = null) {
    if (busy || intent) return;
    const token = ++detailSerial;
    error.hidden = true; current = null; pending();
    try {
      await beforeChange();
      const result = await api('/history/preview', {workspace:workspace(), action, target});
      if (token !== detailSerial) return;
      current = {workspace:workspace(), action, target, fingerprint:result.data.fingerprint, revision:result.revision};
      renderChanges(result.data.changes); previewBox.hidden = false;
      status.textContent = action === 'snapshot' ? 'Save a checkpoint of the current financial state.' : `Review ${result.data.changes.length} changed records before ${action}. This will create a new history entry.`;
      apply.textContent = action === 'snapshot' ? 'Save snapshot' : `Apply ${action}`;
      pending(); apply.focus();
    } catch (failure) {fail(failure);}
  }
  async function commit(original = false) {
    if (busy || (!original && !current) || (original && !intent)) return;
    error.hidden = true;
    setBusy(true); ++serial; ++detailSerial;
    try {
      await beforeChange();
      if (!original) {
        intent = {...current, requestId:crypto.randomUUID()};
        sessionStorage.setItem(storageKey(), JSON.stringify(intent));
      }
      await api('/history/apply', intent);
      sessionStorage.removeItem(storageKey()); intent = null; current = null;
      previewBox.hidden = true; status.textContent = 'Saved. The financial state and its history were committed together.';
      onChanged();
    } catch (failure) {fail(failure);}
    finally {setBusy(false);}
    await load();
  }
  root.addEventListener('whab:settings-enter', () => {void load();});
  root.querySelector('[data-history-refresh]').addEventListener('click', () => {void load();});
  root.querySelector('[data-history-older]').addEventListener('click', () => {void load(true);});
  root.querySelector('[data-history-snapshot]').addEventListener('click', () => {void prepare('snapshot');});
  root.querySelector('[data-history-baseline]').addEventListener('click', () => {void prepare('restore', 'baseline');});
  apply.addEventListener('click', () => {void commit();});
  retry.addEventListener('click', () => {void commit(true);});
  pending(); if (!root.hidden) void load();
  return {load, prepare, commit};
}
