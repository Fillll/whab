import {isSettingsPageOpen} from './settings-navigation.js';
/* Copy status belongs to the host; this view never infers a successful handover. */
import {getContext} from './client.js';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const names = {sqlite: 'SQLite', json: 'JSON'};
const states = {saved: 'Saved', pending: 'Saved; copy pending', active: 'Copy attached',
  failed: 'Saved; copy failed', unavailable: 'Copy status unavailable', reattach_required: 'New attachment required',
  promoting: 'Promotion in progress', promoted: 'Promoted to primary', disabled: 'Disabled · data retained'};
const errors = {
  copy_apply_failed: 'The primary is saved. Copying failed; retry this copy.',
  copy_status_unavailable: 'This copy could not be validated. Refresh its status before continuing.',
  copy_source_changed: 'The primary changed. Preview and attach a new empty copy.',
  invalid_copy_configuration: 'Saved copy configuration needs repair. Further changes are paused.',
  copy_state_update_failed: 'Copy configuration could not be confirmed. Refresh before continuing.',
  copy_scheduling_failed: 'The primary is saved. Copy work is waiting to be scheduled.',
  copy_capture_failed: 'The primary is saved. Its plugin state could not be copied.',
  invalid_namespace_index: 'Saved plugin state could not be validated for copying.',
  promotion_preparation_failed: 'Promotion could not be prepared. Check status before reviewing it again.',
  promotion_failed: 'Promotion did not complete. Check the primary and recovery status.',
};
const integer = value => Number.isSafeInteger(value) && value >= 0;
const liveCopy = copy => !['disabled', 'promoted'].includes(copy.state);
const label = value => names[value] || 'Other storage';
const fault = code => errors[code] || 'The host could not confirm this copy. Refresh status before continuing.';
const protocolError = () => new Error('invalid_copy_response');

function checkedStatus(value) {
  if (!value || typeof value !== 'object' || typeof value.primary?.supported !== 'boolean' ||
      !Array.isArray(value.copies) || value.copies.length > 8 || !Array.isArray(value.operations) ||
      !value.errors || typeof value.errors !== 'object') throw protocolError();
  const primary = value.primary;
  if (value.copies.some(row => !UUID.test(row?.id) || !Object.hasOwn(states, row.state)) ||
      value.operations.some(row => !UUID.test(row?.id) || !UUID.test(row.copy_id) ||
        !['queued', 'running', 'complete', 'failed', 'recovery_required', 'recovered'].includes(row.state))) throw protocolError();
  return {...value, primary: {...primary, authoritative: primary.authoritative === true &&
    primary.role === 'primary' && primary.admission === 'open' && primary.health === 'ready' &&
    UUID.test(primary.instance_id) && UUID.test(primary.authority_epoch) && integer(primary.generation)}};
}

export function createStorageCopies({request, beforeChange, onState, schedule = callback => setTimeout(callback, 2000),
  cancel = clearTimeout, isCurrent = () => true}) {
  const state = {status: null, busy: false, loading: false, review: null, uncertain: null, ticket: null, message: '', error: ''};
  let active = false, session = 0, timer = null, reading = null, readRevision = 0, readFailed = false, nextPrimary = null;
  const current = token => active && token === session && isCurrent();
  const publish = () => { if (active && isCurrent()) onState({...state}); };
  const stopTimer = () => { cancel(timer); timer = null; };
  const canChange = () => state.status?.primary.authoritative && state.status.primary.supported &&
    !Object.keys(state.status.errors).some(key => ['configuration', 'namespace'].includes(key)) &&
    !state.status.operations.some(row => ['queued', 'running', 'recovery_required'].includes(row.state));
  function reconcile() {
    const pending = state.uncertain || state.ticket;
    if (!pending) return;
    const copy = state.status.copies.find(row => row.id === pending.copyId);
    const operations = state.status.operations.filter(row => row.copy_id === pending.copyId);
    const operation = operations.find(row => row.id === pending.id) || operations.at(-1);
    if (pending.kind === 'attach' && copy) {
      state.uncertain = null;
      state.error = '';
      state.message = 'Copy attached. Follow its status until it is saved.';
    } else if (pending.kind === 'disable' && copy?.state === 'disabled') {
      state.uncertain = null;
      state.error = '';
      state.message = 'Copy disabled. Its saved data is retained.';
    } else if (pending.kind === 'retry') {
      state.uncertain = null;
      state.error = '';
      state.message = 'Status refreshed. Copy results are shown below.';
    } else if (pending.kind === 'promote') {
      if (copy?.state === 'promoted' && state.status.primary.authoritative &&
          state.status.primary.plugin_id === pending.destination &&
          state.status.primary.authority_epoch !== pending.epoch && (!operation || operation.state === 'complete')) {
        state.uncertain = state.ticket = null;
        state.error = '';
        state.message = `${label(pending.destination)} is now the authoritative primary. The previous primary is retained.`;
      } else if (operation && ['failed', 'recovery_required'].includes(operation.state)) {
        state.uncertain = state.ticket = null;
        state.error = operation.state === 'recovery_required' ? 'Storage recovery is required. Copy controls are paused.' : fault(operation.error?.code);
      } else if (operation) {
        state.uncertain = null;
        state.error = '';
        state.ticket = {...pending, id: operation.id};
        state.message = operation.state === 'complete' ? 'Validating the new primary…' : 'Promotion accepted. Waiting for the host to finish…';
      }
    }
  }
  function reviewNextPrimary() {
    if (!nextPrimary || !canChange() || state.uncertain || state.ticket) return;
    const copy = state.status.copies.find(row => row.id === nextPrimary.id);
    if (state.status.primary.authority_epoch !== nextPrimary.epoch) {nextPrimary = null; return;}
    if (!copy || !['saved', 'pending', 'failed'].includes(copy.state)) return;
    state.review = {kind: 'promote', id: copy.id, destination: copy.destination,
      instance: state.status.primary.instance_id, epoch: state.status.primary.authority_epoch};
    nextPrimary = null;
  }
  async function refresh() {
    if (!active || !isCurrent()) return;
    if (reading?.session === session) return reading.promise;
    stopTimer();
    const token = session, revision = ++readRevision;
    state.loading = true; publish();
    const promise = (async () => {
      try {
        const status = checkedStatus(await request('/storage/copies'));
        if (!current(token) || revision !== readRevision) return;
        state.status = status;
        if (readFailed) {state.error = ''; readFailed = false;}
        reconcile();
        reviewNextPrimary();
      } catch (error) {
        if (!current(token) || revision !== readRevision) return;
        state.error = error?.status === 404 ? 'Storage copies are unavailable on this host. Your primary is unchanged.' :
          'Copy status could not be loaded. Your primary save is unaffected. Refresh to check again.';
        readFailed = true;
        state.status = null;
      } finally {
        if (current(token) && revision === readRevision) {
          state.loading = false; reading = null; publish();
          timer = schedule(refresh);
        }
      }
    })();
    reading = {session: token, promise};
    return promise;
  }
  async function change(kind, work) {
    if (!active || !isCurrent() || state.busy || state.uncertain || state.ticket || !canChange()) return;
    state.busy = true; state.error = ''; publish();
    const token = session;
    let saved = false;
    try {
      if (await beforeChange() === false) throw new Error('unsaved');
      if (!current(token)) return;
      saved = true;
      await work(token);
    } catch (error) {
      state.error = !saved ? 'Save or cancel pending edits before changing storage copies.' :
        kind === 'preview' ? 'The empty destination could not be prepared. Check that compatible storage plugins are installed, then preview again.' :
        'This storage change could not be reviewed. Refresh status before continuing.';
    } finally {
      state.busy = false; publish();
    }
  }
  async function mutation(kind, path, body, expected, pending, valid) {
    try {
      const result = await request(path, body, expected);
      if (!valid(result)) throw protocolError();
      state.review = null;
      if (kind === 'promote') state.ticket = {...pending, id: result.id};
      state.message = kind === 'promote' ? 'Promotion accepted. Waiting for the host to finish…' :
        kind === 'attach' ? 'Copy attached; waiting for the first saved copy…' :
        kind === 'retry' ? 'Copy retry requested. Your primary is saved.' : 'Copy disabled. Its saved data is retained.';
    } catch (error) {
      // A transport or malformed-success reply cannot prove the mutation failed.
      if (!error?.status || error.status >= 500) {
        state.uncertain = pending;
        state.review = null;
        state.message = '';
        state.error = 'The outcome is not confirmed. Check status before submitting another change. No automatic retry was made.';
      } else {
        state.error = 'The host declined this change. Refresh status and review it again. No completion was assumed.';
        state.review = null;
      }
    }
    await refresh();
  }
  const controller = {
    start() {active = true; session += 1; state.review = null; nextPrimary = null; state.error = ''; publish(); return refresh();},
    stop() {active = false; session += 1; stopTimer(); state.review = null; nextPrimary = null;},
    refresh,
    dismiss() {if (!state.busy) {state.review = null; nextPrimary = null; publish();}},
    async acknowledgeUnknown() {
      const token = session;
      await refresh();
      if (current(token) && state.status && !state.busy && canChange()) {
        state.uncertain = null;
        state.error = '';
        state.message = 'Status refreshed. No completion was assumed. Review any further change before submitting it.';
        publish();
      }
    },
    preview(destination, role = 'synced') {return change('preview', async token => {
      if (typeof destination !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(destination) || destination === state.status.primary.plugin_id) throw protocolError();
      const result = await request('/storage/copies/preview', {destination}, 200);
      if (!UUID.test(result?.preview_id) || result.destination !== destination || result.supported !== true || !integer(result.source_generation)) throw protocolError();
      if (!current(token)) return;
      state.review = {kind: 'attach', id: result.preview_id, destination, generation: result.source_generation, role};
      state.message = ''; publish();
    });},
    attach() {return change('attach', async () => {
      const review = state.review;
      if (review?.kind !== 'attach') return;
      if (review.role === 'primary') nextPrimary = {id: review.id, epoch: state.status.primary.authority_epoch};
      await mutation('attach', '/storage/copies/transition', {preview_id: review.id}, 202,
        {kind: 'attach', copyId: review.id}, result => result?.id === review.id && result.state === 'pending');
      if (!state.uncertain && state.error) nextPrimary = null;
    });},
    reviewPromotion(id) {return change('review', async token => {
      await refresh();
      if (!current(token) || !canChange()) return;
      const copy = state.status.copies.find(row => row.id === id);
      if (!copy || !['saved', 'pending', 'failed'].includes(copy.state)) return;
      state.review = {kind: 'promote', id, destination: copy.destination,
        instance: state.status.primary.instance_id, epoch: state.status.primary.authority_epoch};
      state.message = ''; publish();
    });},
    promote() {return change('promote', async token => {
      const review = state.review;
      if (review?.kind !== 'promote') return;
      await refresh();
      if (!current(token)) return;
      if (!canChange() || state.status.primary.instance_id !== review.instance || state.status.primary.authority_epoch !== review.epoch ||
          !state.status.copies.some(row => row.id === review.id && ['saved', 'pending', 'failed'].includes(row.state))) {
        state.review = null; state.error = 'The primary or copy changed. Refresh and review promotion again.'; return;
      }
      await mutation('promote', `/storage/copies/${review.id}/promote`, {}, 202,
        {kind: 'promote', copyId: review.id, destination: review.destination, epoch: review.epoch},
        result => UUID.test(result?.id) && result.state === 'queued');
    });},
    retry(id) {return change('retry', async () => {
      if (!UUID.test(id) || !state.status.copies.some(row => row.id === id && ['saved', 'pending', 'failed'].includes(row.state))) return;
      await mutation('retry', `/storage/copies/${id}/retry`, {}, 202, {kind: 'retry', copyId: id},
        result => result?.id === id && result.state === 'pending');
    });},
    disable(id) {return change('disable', async () => {
      if (!UUID.test(id) || !state.status.copies.some(row => row.id === id && ['saved', 'pending', 'failed', 'reattach_required', 'unavailable'].includes(row.state))) return;
      await mutation('disable', `/storage/copies/${id}/disable`, {}, 200, {kind: 'disable', copyId: id},
        result => result?.id === id && result.state === 'disabled');
    });},
    reviewDisable(destination) {return change('review', async token => {
      await refresh();
      if (!current(token) || !canChange() || destination === state.status.primary.plugin_id) return;
      const copies = state.status.copies.filter(copy => copy.destination === destination && liveCopy(copy));
      if (!copies.length || copies.some(copy => !['saved', 'pending', 'failed', 'reattach_required', 'unavailable'].includes(copy.state))) return;
      state.review = {kind: 'disable', id: copies[0].id, destination, copies: copies.map(copy => copy.id)};
      state.message = ''; publish();
    });},
    disableReviewed() {return change('disable', async token => {
      const review = state.review;
      if (review?.kind !== 'disable') return;
      await refresh();
      if (!current(token) || !canChange() || review.destination === state.status.primary.plugin_id ||
          state.status.copies.some(copy => copy.destination === review.destination && liveCopy(copy) && !review.copies.includes(copy.id))) {
        state.review = null; state.error = 'Storage changed. Review the unused role again.'; return;
      }
      for (const id of review.copies) {
        const copy = state.status.copies.find(copy => copy.id === id);
        if (!copy || copy.state === 'disabled') continue;
        if (!current(token) || !canChange() || !['saved', 'pending', 'failed', 'reattach_required', 'unavailable'].includes(copy.state)) break;
        await mutation('disable', `/storage/copies/${id}/disable`, {}, 200, {kind: 'disable', copyId: id},
          result => result?.id === id && result.state === 'disabled');
        if (state.uncertain || state.error) break;
      }
    });},
  };
  return controller;
}

export function initStorageCopies({beforeChange, document: page = document, request: suppliedRequest,
  context = getContext, schedule, cancel} = {}) {
  return [...page.querySelectorAll('[data-storage-plugin]')].map(dialog => initStoragePlugin({
    beforeChange, document: page, request: suppliedRequest, context, schedule, cancel, dialog,
  })).filter(Boolean);
}

export function initStoragePlugin({beforeChange, document: page = document, request: suppliedRequest,
  context = getContext, schedule, cancel, dialog} = {}) {
  if (!dialog || dialog.dataset.workspace !== 'personal') return null;
  const destination = dialog.dataset.storagePlugin;
  const storageLabel = id => id === destination ? dialog.dataset.storageLabel : label(id);
  const scope = context().apiPrefix;
  const panel = dialog.querySelector('[data-storage-copy-panel]');
  const message = dialog.querySelector('[data-storage-copy-message]');
  const error = dialog.querySelector('[data-storage-copy-error]');
  const refresh = dialog.querySelector('[data-storage-copy-refresh]');
  const current = () => context().apiPrefix === scope && context().workspace === 'personal';
  const text = (tag, value, cls) => {
    const node = page.createElement(tag); node.textContent = value;
    if (cls) node.className = cls;
    return node;
  };
  const request = suppliedRequest || (async (path, body, expected = 200) => {
    const response = await fetch(`${scope}${path}`, {method: body === undefined ? 'GET' : 'POST',
      headers: {'Content-Type': 'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    if (!response.ok) {const failure = new Error('copy_request_failed'); failure.status = response.status; throw failure;}
    if (response.status !== expected) throw protocolError();
    return response.json();
  });
  let controller, rendered = null, reviewed = null;
  function render(state) {
    const blocked = state.busy || Boolean(state.uncertain || state.ticket);
    dialog.setAttribute('aria-busy', String(state.busy || state.loading));
    refresh.disabled = state.loading;
    message.textContent = state.message || (state.loading && !state.status ? 'Checking storage status…' : '');
    error.textContent = state.error; error.hidden = !state.error;
    // A poll must not replace a focused destination or confirmation button when
    // the host projection is unchanged. Loading feedback updates separately.
    const signature = JSON.stringify([state.status, state.review, state.uncertain, state.ticket, state.busy]);
    if (signature === rendered) return;
    rendered = signature;
    const reviewKey = state.review ? `${state.review.kind}-${state.review.id}` : null;
    const focusReview = reviewKey !== null && reviewKey !== reviewed;
    reviewed = reviewKey;
    const focused = panel.contains(page.activeElement) ? page.activeElement : null;
    const focusKey = focused?.dataset.storageControl;
    const selection = focused?.tagName === 'SELECT' ? focused.value : null;
    const detailsOpen = [...panel.children].find(node => node.className === 'storage-copy-details')?.open;
    panel.replaceChildren();
    if (!state.status) return;
    const primary = state.status.primary;
    const usable = primary.authoritative && primary.supported && !state.status.operations.some(row =>
      ['queued', 'running', 'recovery_required'].includes(row.state)) && !state.status.errors.configuration && !state.status.errors.namespace;
    const button = (caption, handler, primaryAction = false, key = caption) => {
      const node = text('button', caption, `button ${primaryAction ? 'primary' : 'secondary'}`); node.type = 'button';
      node.dataset.storageControl = key;
      node.disabled = blocked || !usable; node.addEventListener('click', handler); return node;
    };
    const copies = state.status.copies.filter(copy => copy.destination === destination);
    const attached = copies.filter(liveCopy);
    const isPrimary = primary.plugin_id === destination;
    const role = primary.supported && !primary.authoritative ? null : isPrimary ? 'primary' : attached.length ? 'synced' : 'unused';
    const promotable = attached.filter(copy => ['saved', 'pending', 'failed'].includes(copy.state));
    const roles = text('fieldset', '', 'storage-roles');
    roles.append(text('legend', 'Role in this budget'));
    for (const [value, caption] of [['primary', 'Primary'], ['synced', 'Synced copy'], ['unused', 'Not used']]) {
      const choice = text('label', '', 'storage-role-choice');
      const radio = page.createElement('input');
      radio.type = 'radio'; radio.name = `storage-role-${destination}`; radio.value = value;
      radio.checked = role === value;
      radio.dataset.storageControl = `role-${value}`;
      radio.disabled = blocked || !usable || isPrimary && value !== 'primary' || value === 'primary' && attached.length > 0 && promotable.length !== 1 ||
        value === 'unused' && attached.some(copy => !['saved', 'pending', 'failed', 'reattach_required', 'unavailable'].includes(copy.state));
      radio.addEventListener('change', () => {
        // A radio reflects persisted status, never an unconfirmed request.
        radio.checked = value === role;
        for (const input of roles.querySelectorAll('[data-storage-control]')) input.checked = input.value === role;
        if (value === role) return;
        if (value === 'unused') return controller.reviewDisable(destination);
        if (value === 'primary' && promotable.length === 1) return controller.reviewPromotion(promotable[0].id);
        return controller.preview(destination, value);
      });
      choice.append(radio, text('span', caption)); roles.append(choice);
    }
    panel.append(roles);
    if (role === 'primary') panel.append(text('p', 'To stop using this primary, make another synced copy Primary first.', 'settings-notice'));
    const summary = text('section', '', 'storage-primary');
    const health = {ready: 'Ready', degraded: 'Cleanup needs attention', recovery_required: 'Storage recovery required'}[primary.health] || 'Status unavailable';
    summary.append(text('p', `${primary.supported && !primary.authoritative ? 'Storage awaiting confirmation' : 'Current primary'}: ${storageLabel(primary.plugin_id)} · ${health}`, 'settings-notice'));
    if (attached.length) summary.append(text('p', [...new Set(attached.map(copy => states[copy.state]))].join(' · '), 'storage-copy-state'));
    panel.append(summary);
    if (!primary.supported) {
      panel.append(text('p', 'This backend does not support synchronized local storage copies. Your primary remains in use.', 'settings-notice'));
      return;
    }
    if (!usable) panel.append(text('p', 'Copy controls are paused until storage is ready and authoritative.', 'settings-notice'));
    for (const entry of Object.values(state.status.errors)) panel.append(text('p', fault(entry?.code), 'field-error'));
    const list = text('div', '', 'storage-copy-list');
    for (const copy of copies) {
      const row = text('section', '', 'storage-copy-row');
      row.append(text('h4', `${storageLabel(copy.destination)} copy`), text('p', states[copy.state], 'storage-copy-state'));
      const watermark = copy.watermark;
      if (watermark && integer(watermark.source_generation) && integer(watermark.namespace_generation)) {
        row.append(text('p', `Budget revision ${watermark.source_generation} · Plugin state revision ${watermark.namespace_generation}`, 'storage-watermark'));
      } else row.append(text('p', 'No validated saved watermark is available.', 'settings-notice'));
      if (copy.error) row.append(text('p', fault(copy.error.code), 'field-error'));
      const actions = text('div', '', 'storage-copy-actions');
      if (['saved', 'pending', 'failed'].includes(copy.state)) {
        actions.append(button('Retry copy', () => controller.retry(copy.id), false, `retry-${copy.id}`),
          button('Review promotion', () => controller.reviewPromotion(copy.id), false, `promote-${copy.id}`));
      }
      if (['saved', 'pending', 'failed', 'reattach_required', 'unavailable'].includes(copy.state)) {
        actions.append(button('Disable copy', () => controller.disable(copy.id), false, `disable-${copy.id}`));
      }
      if (copy.state === 'reattach_required') row.append(text('p', 'Preview a new empty destination to copy the current primary. This older copy is retained.', 'settings-notice'));
      row.append(actions); list.append(row);
    }
    if (copies.length || integer(primary.generation)) {
      const details = text('details', '', 'storage-copy-details');
      details.open = Boolean(detailsOpen);
      details.append(text('summary', 'Saved copies and status'));
      if (integer(primary.generation)) details.append(text('p', `Budget revision ${primary.generation}`, 'storage-watermark'));
      details.append(text('p', 'A successful primary save stays successful when a copy is pending or fails.', 'settings-notice'), list);
      panel.append(details);
    }
    for (const operation of state.status.operations.filter(row => ['queued', 'running', 'recovery_required', 'failed'].includes(row.state))) {
      panel.append(text('p', operation.state === 'recovery_required' ? 'Promotion requires storage recovery.' :
        operation.state === 'failed' ? 'A promotion failed. The primary status above shows the current authority.' :
        `Promotion ${operation.state === 'queued' ? 'queued' : 'running'}…`, 'settings-notice'));
    }
    if (state.uncertain) {
      const check = button('Review refreshed status', () => controller.acknowledgeUnknown());
      check.disabled = state.busy || state.loading;
      panel.append(check);
    }
    if (state.review) {
      const review = text('section', '', 'storage-copy-review');
      review.tabIndex = -1;
      review.setAttribute('aria-label', state.review.kind === 'attach' ? 'Review empty copy destination' :
        state.review.kind === 'disable' ? 'Review unused storage' : 'Review storage promotion');
      review.append(text('h3', state.review.kind === 'attach' ? 'Review empty destination' :
        state.review.kind === 'disable' ? 'Stop syncing this plugin?' : 'Review promotion'));
      review.append(text('p', state.review.kind === 'attach' ?
        `${storageLabel(state.review.destination)} is ready as a new empty destination. Attaching copies the complete current budget and plugin state. The active primary stays in use.${state.review.role === 'primary' ? ' Next, review and confirm making this copy Primary separately.' : ''}` :
        state.review.kind === 'disable' ? 'All synced copies for this plugin will stop updating. Their saved data is retained. The primary stays in use.' :
        `${storageLabel(state.review.destination)} will become the authoritative primary after the host finishes current work, catches up this copy, and validates it. The previous primary is retained and stops accepting writes.`, 'settings-notice'));
      const actions = text('div', '', 'storage-copy-actions');
      actions.append(button(state.review.kind === 'attach' ? 'Attach this empty copy' : state.review.kind === 'disable' ? 'Stop syncing' : 'Promote this copy', () =>
        state.review.kind === 'attach' ? controller.attach() : state.review.kind === 'disable' ? controller.disableReviewed() : controller.promote(), true));
      const dismiss = button('Cancel review', () => controller.dismiss()); dismiss.disabled = state.busy;
      actions.append(dismiss); review.append(actions); panel.append(review);
      if (focusReview || focused?.className === 'storage-copy-review') review.focus({preventScroll: true});
    }
    if (focusKey && !focusReview) {
      const next = [...panel.querySelectorAll('[data-storage-control]')].find(node => node.dataset.storageControl === focusKey);
      if (next && !next.disabled) {
        if (selection !== null && [...next.options].some(option => option.value === selection)) next.value = selection;
        next.focus({preventScroll: true});
      }
    }
  }
  controller = createStorageCopies({request, beforeChange, onState: render, isCurrent: current, schedule, cancel});
  refresh.addEventListener('click', controller.refresh);
  dialog.addEventListener('whab:settings-enter', controller.start);
  dialog.addEventListener('close', controller.stop);
  page.addEventListener('whab:before-navigate', event => {
    if (isSettingsPageOpen(dialog) && event.detail?.source !== 'storage-copies') event.preventDefault();
  });
  return controller;
}
