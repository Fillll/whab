/* A read-only workspace until the user confirms an exact pair. */
const wholeUnits = new Intl.NumberFormat('en-US');

function displayedAmount(row, exact, digits) {
  const units = 10n ** BigInt(digits);
  const signed = -BigInt(row.amount);
  const amount = signed < 0n ? -signed : signed;
  const whole = exact ? amount / units : (amount + units / 2n) / units;
  const fraction = exact && digits ? `.${String(amount % units).padStart(digits, '0')}` : '';
  const sign = signed < 0n && (exact || whole) ? '−' : signed > 0n && (exact || whole) ? '+' : '';
  return `${sign}${row.currency === 'USD' ? '$' : ''}${wholeUnits.format(whole)}${fraction}`;
}

function matchesReceipt(result, intent) {
  const {action, changes, count, fingerprint} = result?.data || {};
  return Boolean(result?.revision && action === intent.action && count === 2 && fingerprint === intent.fingerprint &&
    Array.isArray(changes) && changes.length === 2 && intent.transactionIds.every((id, index) => {
      const saved = changes.find(change => change.id === id)?.after;
      return saved?.id === id && saved.reviewed === false && (action === 'match'
        ? saved.isTransfer === true && saved.transferPeerId === intent.transactionIds[1 - index]
        : !saved.transferPeerId);
    }));
}

export function initTransferMatches({root, api, context, before, saved, selectedRows = () => []}) {
  const find = root.querySelector('[data-transfer-find]');
  if (!find) return {open: false, dirty: false, flush: async () => true, dispose() {}};
  const panel = root.querySelector('[data-transfer-panel]');
  const selected = root.querySelector('[data-transfer-selected]');
  const heading = panel.querySelector('h2');
  const list = panel.querySelector('[data-transfer-list]');
  const message = panel.querySelector('[data-transfer-message]');
  const more = panel.querySelector('[data-transfer-more]');
  const close = panel.querySelector('[data-transfer-close]');
  const suggestions = panel.querySelector('[data-transfer-suggestions]');
  const manual = panel.querySelector('[data-transfer-manual]');
  const listeners = [];
  let disposed = false, opening = false, running = null, intent = null, uncertain = false;
  let sequence = 0, opener = null, mode = 'suggestions', rows = [], current = null;
  let next = null, revision = null, pairing = null, proposals = [], proposalTotal = 0;
  let currencyDigits;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function listen(node, event, callback) {
    node.addEventListener(event, callback);
    listeners.push(() => node.removeEventListener(event, callback));
  }
  function button(label, callback, primary = false) {
    const control = el('button', label, `button ${primary ? 'primary' : 'secondary'}`);
    control.type = 'button';
    control.onclick = callback;
    return control;
  }
  function lock(active) {
    close.disabled = suggestions.disabled = manual.disabled = more.disabled = active;
  }
  function view(text) {
    message.textContent = text;
    more.hidden = true;
    list.replaceChildren();
    panel.scrollTop = 0;
  }
  function leg(row, direction, exact) {
    const block = el('div', undefined, 'transfer-leg');
    const top = el('div', undefined, 'transfer-leg-heading');
    top.append(el('span', direction, 'transfer-leg-direction'), el('strong', row.accountName));
    const detail = el('div', undefined, 'transfer-leg-detail');
    detail.append(el('span', row.date), el('span', row.merchant || 'No payee'));
    const money = el('div', undefined, 'transfer-leg-money');
    currencyDigits ||= JSON.parse(document.getElementById('account-currency-choices').textContent);
    const amount = el('strong', displayedAmount(row, exact, currencyDigits[row.currency]));
    amount.dataset.privateAmount = '';
    amount.dataset.privateCurrency = row.currency;
    money.append(amount, el('small', row.currency));
    block.append(top, detail, money);
    if (exact) {
      block.append(el('small', row.reviewed ? 'Currently reviewed' : 'Needs review', 'transfer-leg-review'));
      if (row.note) block.append(el('p', row.note, 'transfer-leg-memo'));
    }
    return block;
  }
  function pair(transactions, exact = false) {
    const grouped = el('div', undefined, 'transfer-pair');
    const sorted = [...transactions].sort((left, right) => Math.sign(right.amount) - Math.sign(left.amount));
    grouped.append(...sorted.map((row, index) => leg(row,
      row.amount === 0 ? (index === 0 ? 'First entry' : 'Second entry') : row.amount > 0 ? 'From' : 'To', exact)));
    return grouped;
  }
  async function query(operation, parameters) {
    const result = await api('/transfers/query', {workspace: current.workspace, operation, parameters});
    if (!result?.revision || !result.data) throw new Error('The transfer read could not be verified. Close and reopen Transfers.');
    return result;
  }
  function active(token) {return !disposed && panel.open && token === sequence;}
  function closePanel() {
    if (!panel.open || intent) return;
    sequence += 1;
    panel.close();
    opener?.focus();
  }
  function appendCandidate(candidate) {
    const block = el('article', undefined, 'transfer-candidate');
    block.append(pair(candidate.transactions));
    const days = candidate.daysApart === 0 ? 'Same day' : `${candidate.daysApart} ${candidate.daysApart === 1 ? 'day' : 'days'} apart`;
    const reasons = [days, 'Equal and opposite', candidate.bankHint && 'Bank transfer hint',
      candidate.ambiguous && 'Other possible counterparts'].filter(Boolean);
    const actions = el('div', undefined, 'transfer-candidate-actions');
    actions.append(el('p', reasons.join(' · ')), button('Review pair', () => preview(candidate.transactionIds)));
    block.append(actions);
    list.append(block);
  }
  function drawResults() {
    if (mode === 'suggestions') {
      const count = `${proposalTotal} ${proposalTotal === 1 ? 'possible pair' : 'possible pairs'}`;
      view(proposalTotal ? `${count} for the selected dates and accounts. A counterpart can be outside those filters. Text search and review filters do not limit suggestions.`
        : 'No possible pairs for these dates and accounts. Select one or two entries in the register to match manually.');
    } else {
      const count = `${proposals.length} ${proposals.length === 1 ? 'possible counterpart' : 'possible counterparts'}`;
      view(proposals.length ? `${count} for the selected transaction. Compare accounts, dates and payees before choosing a pair.`
        : 'No compatible counterpart found. Check for a posted entry in another account with the opposite amount and same currency within seven days. You can record a missing entry with Add transaction, then select both entries.');
    }
    for (const candidate of proposals) appendCandidate(candidate);
    if (mode === 'suggestions') more.hidden = next === null;
  }
  async function loadSuggestions(append = false) {
    const token = ++sequence;
    if (!append) {
      proposals = []; proposalTotal = 0; revision = null; next = 0;
      view('Finding possible pairs…');
    }
    more.disabled = true;
    try {
      const result = await query('transfer_candidates', {
        currency: current.currency, accountIds: current.selectedAccountIds || [], offset: next, limit: 8,
        ...(current.startDate ? {since: current.startDate} : {}), ...(current.endDate ? {until: current.endDate} : {}),
      });
      if (!active(token)) return;
      if (revision && revision !== result.revision) {
        view('The budget changed while loading suggestions. Check the current suggestions again.');
        list.append(button('Refresh suggestions', () => loadSuggestions()));
        return;
      }
      revision = result.revision;
      next = result.data.next;
      proposals.push(...result.data.candidates);
      proposalTotal = result.data.total;
      if (append) {
        for (const candidate of result.data.candidates) appendCandidate(candidate);
        more.hidden = next === null;
        if (more.hidden) list.lastElementChild?.querySelector('button')?.focus();
      } else drawResults();
    } catch (failure) {
      if (!active(token)) return;
      view(`${failure.message} Narrow the dates or accounts, or select two entries to match manually.`);
      list.append(button('Refresh suggestions', () => loadSuggestions()));
    } finally {if (active(token)) more.disabled = false;}
  }
  async function findCounterparts(anchor) {
    if (anchor.pending) {
      view('This transaction is pending. Choose posted entries to match a transfer.');
      return;
    }
    if (anchor.matched) {
      view('This entry is already matched. Use its link in the register to inspect or unmatch it.');
      return;
    }
    if (anchor.amount === 0) {
      view('To match a zero amount, select both posted zero-amount entries in the register.');
      return;
    }
    if (anchor.categoryId && anchor.categoryKind !== 'transfer') {
      view('This entry has a spending, income or opening balance category. Clear that category first if this was an account transfer.');
      return;
    }
    const token = ++sequence;
    view('Finding compatible entries in other accounts…');
    const found = [];
    try {
      let offset = 0, seenRevision = null;
      while (offset !== null) {
        const result = await query('transfer_candidates', {accountIds: [anchor.accountId], currency: anchor.currency,
          since: anchor.date, until: anchor.date, offset, limit: 100});
        if (!active(token)) return;
        if (seenRevision && seenRevision !== result.revision) throw new Error('The budget changed while reading possible counterparts.');
        seenRevision = result.revision;
        found.push(...result.data.candidates.filter(candidate => candidate.transactionIds.includes(anchor.id)));
        const upcoming = result.data.next;
        if (upcoming !== null && (!Number.isInteger(upcoming) || upcoming <= offset || upcoming >= 1000)) {
          throw new Error('The counterpart list could not be read completely.');
        }
        offset = upcoming;
      }
      proposals = found;
      drawResults();
    } catch (failure) {
      if (!active(token)) return;
      view(`${failure.message} Select the two known entries in the register, or search for compatible entries again.`);
      list.append(button('Find counterparts again', () => findCounterparts(anchor)));
    }
  }
  function showMode(nextMode, reuse = false) {
    if (intent) return;
    sequence += 1;
    mode = nextMode;
    suggestions.setAttribute('aria-pressed', String(mode === 'suggestions'));
    manual.setAttribute('aria-pressed', String(mode === 'manual'));
    more.disabled = false;
    if (reuse) {
      drawResults();
      list.querySelector('button')?.focus();
      return;
    }
    proposals = []; proposalTotal = 0; next = null;
    if (mode === 'suggestions') return loadSuggestions();
    if (rows.length === 2) return preview(rows.map(row => row.id));
    if (rows.length === 1) return findCounterparts(rows[0]);
    view(rows.length > 2 ? 'Select just two entries to compare them, or one to find possible counterparts.'
      : 'Select one transaction to find possible counterparts across accounts, or select two known entries and choose Match transfer.');
    list.append(el('p', 'If an entry is missing, record it with Add transaction first.', 'transfer-help'));
    list.append(button('Back to transactions', closePanel));
  }
  async function preview(ids, action = 'match') {
    if (intent) return;
    pairing = {ids, action};
    const token = ++sequence;
    const canGoBack = action === 'match' && (mode === 'suggestions' || rows.length === 1);
    view('Reading both transactions…');
    const title = el('h3', action === 'match' ? 'Review transfer' : 'Review unmatch', 'transfer-review-title');
    title.tabIndex = -1;
    list.append(title);
    title.focus();
    try {
      const result = await query('preview_transfer_match', {transactionIds: ids, action});
      if (!active(token)) return;
      const {fingerprint, changes} = result.data;
      if (typeof fingerprint !== 'string' || !Array.isArray(changes) || changes.length !== 2 ||
          !ids.every(id => changes.some(change => change.before?.id === id))) {
        throw new Error('The preview did not contain the two selected transactions.');
      }
      message.textContent = action === 'match'
        ? 'Confirm to link these entries as a transfer. Both will need separate review in the register.'
        : 'Confirm to separate these entries and restore any bank hints. Both will need review.';
      list.append(pair(changes.map(change => change.before), true));
      const actions = el('div', undefined, 'transfer-confirm-actions');
      actions.append(button(canGoBack ? 'Back to pairs' : 'Back to transactions',
        () => canGoBack ? showMode(mode, true) : closePanel()));
      const confirm = button(action === 'match' ? 'Confirm transfer' : 'Confirm unmatch', async () => {
        if (running) return;
        intent ||= {workspace: current.workspace, transactionIds: ids, action, fingerprint,
          revision: result.revision, requestId: crypto.randomUUID()};
        await apply(confirm, actions.firstElementChild);
      }, true);
      actions.append(confirm);
      list.append(actions);
    } catch (failure) {
      if (!active(token)) return;
      message.textContent = failure.message;
      list.append(button(canGoBack ? 'Back to pairs' : 'Back to transactions',
        () => canGoBack ? showMode(mode, true) : closePanel()));
    }
  }
  async function apply(confirm, back) {
    confirm.disabled = back.disabled = true;
    lock(true);
    message.textContent = 'Saving this pair…';
    let committed = false;
    running = (async () => {
      try {
        const result = await api('/transfers/apply', intent);
        if (!matchesReceipt(result, intent)) throw new Error('The saved pair could not be verified.');
        intent = null;
        uncertain = false;
        committed = true;
      } catch (failure) {
        if (failure.code === 'transfer_revision_conflict' && !uncertain) {
          intent = null;
          back.disabled = false;
          message.textContent = 'The budget changed. This confirmation saved no changes. Review the current pair.';
          confirm.textContent = 'Review current pair';
          confirm.onclick = () => preview(pairing.ids, pairing.action);
        } else {
          uncertain = true;
          message.textContent = `${failure.message} The pair may have saved. Check the transactions or Budget History in another tab, then retry this same confirmation.`;
          confirm.textContent = 'Retry same confirmation';
        }
        confirm.disabled = false;
        confirm.focus();
      } finally {
        running = null;
        lock(Boolean(intent));
      }
    })();
    await running;
    if (committed && !disposed) {
      closePanel();
      await saved();
    }
  }
  async function open(button, unmatchIds = null) {
    if (opening || panel.open || disposed || intent) return;
    opening = true;
    try {
      if (!await before() || disposed) return;
      opener = button;
      current = context();
      rows = selectedRows();
      panel.showModal();
      heading.focus();
      if (unmatchIds) {
        mode = 'manual';
        suggestions.setAttribute('aria-pressed', 'false');
        manual.setAttribute('aria-pressed', 'true');
        return preview(unmatchIds, 'unmatch');
      }
      return showMode(rows.length ? 'manual' : 'suggestions');
    } finally {opening = false;}
  }
  listen(find, 'click', () => open(find));
  if (selected) listen(selected, 'click', () => open(selected));
  listen(suggestions, 'click', () => showMode('suggestions'));
  listen(manual, 'click', () => showMode('manual'));
  listen(more, 'click', () => loadSuggestions(true));
  listen(close, 'click', closePanel);
  listen(panel, 'cancel', event => {event.preventDefault(); closePanel();});
  listen(panel, 'click', event => {if (event.target === panel) closePanel();});
  // Capture before register cell editing, while ordinary row clicks stay untouched.
  const unmatch = event => {
    const button = event.target.closest('[data-transfer-unmatch]');
    if (button) {
      event.stopPropagation();
      void open(button, [button.closest('[data-transaction-id]').dataset.transactionId, button.dataset.transferPeer]);
    }
  };
  root.addEventListener('click', unmatch, true);
  listeners.push(() => root.removeEventListener('click', unmatch, true));
  return {
    get open() {return opening || panel.open;},
    get dirty() {return Boolean(intent);},
    async flush() {if (running) await running; return !intent;},
    dispose() {disposed = true; sequence += 1; for (const remove of listeners) remove();},
  };
}
