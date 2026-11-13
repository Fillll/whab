/* Proposal reads are on demand. Only an explicit confirmation can write a pair. */
export function initTransferMatches({root, api, context, before, saved}) {
  const find = root.querySelector('[data-transfer-find]');
  if (!find) return {get dirty() {return false;}, flush: async () => true, dispose() {}};
  const panel = root.querySelector('[data-transfer-panel]');
  const list = panel.querySelector('[data-transfer-list]');
  const message = panel.querySelector('[data-transfer-message]');
  const more = panel.querySelector('[data-transfer-more]');
  const close = panel.querySelector('[data-transfer-close]');
  const listeners = [];
  let disposed = false, reading = false, running = null, intent = null, selection = null, next = null, revision = null, sequence = 0;
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
  function lock() {
    find.disabled = reading || Boolean(intent);
    more.disabled = reading || Boolean(intent);
    close.disabled = Boolean(intent);
  }
  function show() {
    panel.hidden = false;
    message.textContent = '';
    panel.focus();
  }
  function leg(row) {
    const digits = new Intl.NumberFormat('en', {style:'currency', currency:row.currency}).resolvedOptions().maximumFractionDigits;
    const amount = new Intl.NumberFormat(undefined, {style:'currency', currency:row.currency}).format(-row.amount / 10 ** digits);
    const block = el('div', undefined, 'transfer-match-leg');
    block.append(el('strong', `${row.accountName} · ${amount}`),
      el('span', `${row.date} · ${row.merchant}`), el('small', row.id));
    return block;
  }
  async function query(operation, parameters) {
    const result = await api('/transfers/query', {workspace:context().workspace, operation, parameters});
    if (!result?.revision || !result.data) throw new Error('The host returned an invalid transfer response. Refresh before confirming.');
    return result;
  }
  async function apply(button) {
    if (running || !intent) return;
    let committed = false;
    button.disabled = true;
    message.textContent = 'Saving this confirmed pair…';
    running = (async () => {
      try {
        const result = await api('/transfers/apply', intent);
        if (!result?.revision || result.data?.count !== 2) throw new Error('Saved transfer response was not confirmed.');
        intent = null;
        message.textContent = 'Saved. Both transactions still need review.';
        panel.hidden = true;
        lock();
        committed = true;
      } catch (failure) {
        if (failure.code === 'transfer_revision_conflict') {
          intent = null;
          message.textContent = 'The budget changed. Nothing was applied by this confirmation. Review the current pair again.';
          button.textContent = 'Review current pair';
          button.onclick = () => preview(selection.ids, selection.action);
        } else {
          message.textContent = `${failure.message} Save not confirmed; it may have committed. Inspect both transactions/history in another tab, or retry this same confirmation. Neither is automatically reviewed.`;
          button.textContent = 'Retry same confirmation';
        }
        button.disabled = false;
      } finally {running = null; lock();}
    })();
    lock();
    await running;
    if (committed && !disposed) await saved();
  }
  async function preview(ids, action = 'match') {
    if (intent || reading || !await before()) return;
    selection = {ids, action};
    show(); list.replaceChildren(); more.hidden = true;
    reading = true; lock();
    message.textContent = 'Reading the exact pair for review…';
    const token = ++sequence;
    try {
      const result = await query('preview_transfer_match', {transactionIds:ids, action});
      if (disposed || token !== sequence) return;
      message.textContent = action === 'match' ? 'Review both sides. Confirming links and excludes this pair as a transfer; both will still need review.' : 'Unmatching removes both links and restores existing bank transfer hints. Both will need review.';
      for (const change of result.data.changes) {
        const block = leg(change.before);
        block.append(el('p', `Memo: ${change.before.note || '(empty)'}`));
        list.append(block);
      }
      const button = el('button', action === 'match' ? 'Confirm transfer match' : 'Confirm unmatch', 'button primary');
      button.type = 'button';
      button.onclick = async () => {
        if (running) return;
        if (!intent) intent = {workspace:context().workspace, transactionIds:ids, action,
          fingerprint:result.data.fingerprint, revision:result.revision, requestId:crypto.randomUUID()};
        await apply(button);
      };
      list.append(button);
    } catch (failure) {if (!disposed) message.textContent = failure.message;}
    finally {reading = false; lock();}
  }
  async function load(append = false) {
    if (intent || reading || !await before()) return;
    show();
    if (!append) {list.replaceChildren(); revision = null; next = 0;}
    reading = true; lock(); more.hidden = true;
    message.textContent = 'Looking for possible pairs. No transactions are changing…';
    const token = ++sequence, current = context();
    try {
      const result = await query('transfer_candidates', {
        currency:current.currency, accountIds:current.selectedAccountIds || [], offset:next || 0,
        ...(current.startDate ? {since:current.startDate} : {}), ...(current.endDate ? {until:current.endDate} : {}),
      });
      if (disposed || token !== sequence) return;
      if (revision && revision !== result.revision) throw new Error('The budget changed between pages. Run Suggest transfers again for one current selection.');
      revision = result.revision; next = result.data.next;
      message.textContent = `${result.data.total} possible pairs. Suggestions are not proof: inspect both accounts, dates and amounts. ${next !== null ? 'More results are available below.' : 'All proposals for this selection are loaded.'}`;
      for (const candidate of result.data.candidates) {
        const block = el('article', undefined, 'transfer-match');
        block.append(el('h3', candidate.ambiguous ? 'Possible transfer · multiple alternatives' : 'Possible transfer'));
        for (const row of candidate.transactions) block.append(leg(row));
        block.append(el('p', `${candidate.daysApart} days apart${candidate.bankHint ? ' · bank transfer hint' : ''}. ${candidate.reason}`));
        const review = el('button', 'Review this pair', 'button secondary');
        review.type = 'button'; review.onclick = () => preview(candidate.transactionIds);
        block.append(review); list.append(block);
      }
      more.hidden = next === null;
    } catch (failure) {if (!disposed) message.textContent = failure.message;}
    finally {reading = false; lock();}
  }
  listen(find, 'click', () => load());
  listen(more, 'click', () => load(true));
  listen(close, 'click', () => {if (!intent) {sequence += 1; panel.hidden = true; find.focus();}});
  // The row action always previews an unmatch rather than silently altering it.
  const unmatch = event => {
    const button = event.target.closest('[data-transfer-unmatch]');
    if (button) {event.stopPropagation(); void preview([button.closest('[data-transaction-id]').dataset.transactionId, button.dataset.transferPeer], 'unmatch');}
  };
  // Capture before register cell editing, while ordinary row clicks stay untouched.
  root.addEventListener('click', unmatch, true);
  listeners.push(() => root.removeEventListener('click', unmatch, true));
  return {
    get dirty() {return Boolean(intent);},
    async flush() {if (running) await running; return !intent;},
    dispose() {disposed = true; sequence += 1; for (const remove of listeners) remove();},
  };
}
