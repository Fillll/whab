/* Append guarded, bounded HTML chunks without replacing mounted editors. */
export function createTransactionLoader({next, previous, revision, load, append, ready = () => true, changed = () => {}, state = () => {}}) {
  let links = {next, previous}, snapshot = revision, generation = 0, running = null, disposed = false;
  let failed = false;
  function cancel() {generation += 1; running?.controller.abort(); running = null; state('idle');}
  async function more(direction = 'next', retry = false) {
    if (disposed || running || failed && !retry || !links[direction] || !ready()) return false;
    const ticket = {generation, controller: new AbortController()}; running = ticket;
    state('loading');
    try {
      const chunk = await load(links[direction], ticket.controller.signal);
      if (disposed || generation !== ticket.generation) return false;
      if (!ready()) {state('idle'); return false;}
      if (snapshot && chunk.revision && snapshot !== chunk.revision) {changed(); return false;}
      append(chunk, direction);
      snapshot = chunk.revision;
      links[direction] = chunk[direction];
      failed = false;
      state(links.next || links.previous ? 'idle' : 'done');
      return true;
    } catch (error) {
      if (disposed || generation !== ticket.generation || error.name === 'AbortError') return false;
      failed = true; state('error', error); return false;
    } finally {if (running === ticket) running = null;}
  }
  return {more, cancel,
    mutation(restart) {cancel(); snapshot = null; failed = false; if (restart) links = {next: restart, previous: null};},
    get next() {return links.next;}, get previous() {return links.previous;},
    dispose() {disposed = true; cancel();}};
}

export function initTransactionStream({root, ready, append, changed, context = {}}) {
  const scroller = root.querySelector('.transactions-scroll');
  const status = root.querySelector('[data-transaction-stream]');
  if (!scroller || !status) return {cancel() {}, mutation() {}, check() {}, dispose() {}};
  const label = status.querySelector('[data-stream-status]'), retry = status.querySelector('[data-stream-retry]');
  const loaded = root.querySelector('[data-stream-loaded]');
  const scope = value => {const url = new URL(value, location.href); url.hash = ''; url.searchParams.delete('tx_page'); url.searchParams.sort(); return url.href;};
  const first = scope(root.dataset.streamNext || root.dataset.streamPrevious || location.href);
  let disposed = false, queued = null, lastDirection = 'next', restoreTarget = null;
  async function load(value, signal) {
    const target = new URL(value, location.href);
    if (target.origin !== location.origin || scope(target) !== first) throw new Error('Transaction address changed. Reload this view.');
    const response = await fetch(target, {signal, cache: 'no-store', headers: {'X-WHAB-Transactions-Fragment': '1', 'X-WHAB-Transactions-Sidebar': 'omit'}});
    if (!response.ok || response.headers.get('X-WHAB-Transactions-Fragment') !== '1' || response.url && scope(response.url) !== first) throw new Error('More transactions could not be loaded.');
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const fresh = JSON.parse(page.querySelector('[data-transactions-fragment]')?.dataset.pageContext || 'null');
    if (!fresh || ['budgetId', 'apiPrefix', 'workspace', 'uiGeneration', 'currency'].some(key => context[key] && fresh[key] !== context[key])) {
      throw new Error('The transaction view changed. Reload this view.');
    }
    const register = page.querySelector('[data-transaction-register]');
    if (!register || register.dataset.workspace !== root.dataset.workspace) throw new Error('The transaction response did not match this view.');
    const rows = [...register.querySelectorAll('.transaction-row')];
    if (rows.length > 100 || rows.some(row => !row.dataset.transactionId) || new Set(rows.map(row => row.dataset.transactionId)).size !== rows.length) {
      throw new Error('The transaction response was invalid.');
    }
    return {rows, next: register.dataset.streamNext,
      previous: register.dataset.streamPrevious, revision: register.dataset.streamRevision, count: register.dataset.streamCount, summary: page.querySelector('#transaction-count')?.textContent};
  }
  const loader = createTransactionLoader({next: root.dataset.streamNext, previous: root.dataset.streamPrevious,
    revision: root.dataset.streamRevision, load, ready, changed,
    append(chunk, direction) {
      const height = scroller.scrollHeight;
      append(chunk.rows, direction);
      if (direction === 'previous') scroller.scrollTop += scroller.scrollHeight - height;
      root.dataset.streamCount = chunk.count;
      if (chunk.summary) root.querySelector('#transaction-count').textContent = chunk.summary;
      loaded.textContent = `${root.querySelectorAll('.transaction-row:not([hidden])').length} loaded`;
      check();
    },
    state(value, error) {
      retry.hidden = value !== 'error';
      status.dataset.state = value;
      label.textContent = value === 'loading' ? 'Loading more transactions…' : value === 'error' ? error.message || 'More transactions could not be loaded.' : '';
    },
  });
  function check() {
    clearTimeout(queued);
    queued = setTimeout(async () => {
      if (disposed) return;
      if (restoreTarget && (root.querySelectorAll('.transaction-row').length >= restoreTarget.loaded || !loader.next && !loader.previous)) {
        scroller.scrollTop = restoreTarget.top; scroller.scrollLeft = restoreTarget.left; restoreTarget = null;
      }
      const previous = loader.previous && (restoreTarget || scroller.scrollTop < 80);
      if (!restoreTarget && !previous && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight > 320) return;
      lastDirection = previous ? 'previous' : 'next';
      if (await loader.more(lastDirection)) check();
    }, 0);
  }
  const restore = event => {const value = event.detail; if (value && Number.isInteger(value.loaded) && value.loaded >= 0 && value.loaded <= 250000 && Number.isFinite(value.top) && Number.isFinite(value.left)) {restoreTarget = value; check();}};
  root.addEventListener('whab:restore-register', restore);
  const clickRetry = () => {void loader.more(lastDirection, true).then(check);};
  scroller.addEventListener('scroll', check, {passive: true});
  retry.addEventListener('click', clickRetry);
  check();
  return {cancel() {restoreTarget = null; loader.cancel();},
    mutation(restart = false) {loader.mutation(restart ? first : null); loaded.textContent = `${root.querySelectorAll('.transaction-row:not([hidden])').length} loaded`; check();}, check,
    dispose() {disposed = true; clearTimeout(queued); loader.dispose(); root.removeEventListener('whab:restore-register', restore); scroller.removeEventListener('scroll', check); retry.removeEventListener('click', clickRetry);}};
}
