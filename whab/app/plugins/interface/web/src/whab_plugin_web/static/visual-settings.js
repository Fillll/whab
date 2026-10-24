/* Selection status is host-owned. A 202 response never proves activation. */
import {getContext, notify} from './client.js';

const ID = /^[a-z][a-z0-9-]{0,63}$/;
const UUID = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
const HASH = /^[a-f0-9]{64}$/;
const phases = new Set(['queued', 'running', 'complete', 'failed']);
const pending = row => ['queued', 'running'].includes(row.state);
const plugin = value => value === null || typeof value === 'string' && ID.test(value);
const invalid = () => new Error('invalid_selection_response');
const messages = {
  restart_required: 'This selection includes a plugin that requires restart. Save it for restart, or review a supported live selection.',
  ui_transition_required: 'This interface requires restart or does not support live browser coordination. The current interface remains selected.',
  ui_selection_conflict: 'The selected interface changed. Refresh status and prepare a new preview.',
  ui_preview_unavailable: 'The preview expired or is unavailable. Refresh status and prepare a new preview.',
  continuation_busy: 'Finish or cancel this plugin’s unfinished work before changing the selection.',
  commit_uncertain: 'The handover outcome is uncertain. Preserve the current view and inspect host recovery before continuing.',
  recovery_required: 'The host requires recovery. Preserve the current view and inspect recovery before continuing.',
  ui_recovery_required: 'The visual selection requires recovery. Preserve its resources and use the host recovery controls.',
  prepare_failed: 'The candidate could not be prepared. The current selection remains in use.',
  readiness_failed: 'The candidate did not pass readiness checks. The current selection remains in use.',
  unsaved: 'Save or cancel unfinished edits and close other dialogs before applying a plugin change.',
};
const available = runtime => runtime?.admission === 'open' && ['ready', 'degraded'].includes(runtime.health) &&
  (runtime.cleanup_pending ?? 0) === 0 && (runtime.cleanup_failed ?? 0) === 0;
const authored = code => messages[code] || 'The host declined or could not finish this change. Refresh status before reviewing it again.';

function checked(value, visual) {
  if (!value || !value.runtime || !Number.isSafeInteger(value.runtime.generation) || value.runtime.generation < 1 ||
      !['ready', 'degraded', 'recovery_required'].includes(value.runtime.health) ||
      !['open', 'gated', 'closed'].includes(value.runtime.admission) ||
      ['cleanup_pending', 'cleanup_failed'].some(key => value.runtime[key] !== undefined &&
        (!Number.isSafeInteger(value.runtime[key]) || value.runtime[key] < 0)) ||
      !Array.isArray(value.operations) || value.operations.length > 128 ||
      value.operations.some(row => !UUID.test(row?.id) || !phases.has(row.state))) throw invalid();
  if (visual && (!value.active || !value.configured || !plugin(value.active.plugin_id) ||
      !plugin(value.configured.plugin_id) || !HASH.test(value.active.resource_generation) ||
      !HASH.test(value.configured.resource_generation) || !HASH.test(value.selection_revision))) throw invalid();
  return {...value, operations: value.operations.map(row => ({...row, error: row.error ? {code: row.error.code} : null}))};
}

export function createVisualSettings({request, beforeChange = async () => true, beforeCommit = async () => true,
  onState, isCurrent = () => true, schedule = callback => setTimeout(callback, 1000), cancel = clearTimeout}) {
  const state = {visual: null, optional: null, review: null, ticket: null, unknown: false,
    busy: false, loading: false, error: '', message: '', completed: null};
  let active = false, session = 0, timer = null, reading = null, draft = 0, readFailed = false;
  const current = token => active && session === token && isCurrent();
  const publish = () => {if (active && isCurrent()) onState({...state});};
  const stopTimer = () => {cancel(timer); timer = null;};
  const healthy = () => available(state.visual?.runtime) && available(state.optional?.runtime);
  const inFlight = () => [state.visual, state.optional].some(status => status?.operations.some(pending));
  const canChange = () => healthy() && !inFlight() && !state.busy && !state.ticket && !state.unknown;
  function reconcile() {
    const ticket = state.ticket;
    if (!ticket) return;
    const status = ticket.kind === 'visual' ? state.visual : state.optional;
    const operation = ticket.id ? status.operations.find(row => row.id === ticket.id) :
      ticket.kind === 'visual' ? status.operations.find(row => UUID.test(row.preview_id) && row.preview_id === ticket.previewId) : null;
    if (!operation) return;
    ticket.id = operation.id;
    if (operation.state === 'failed') {
      state.ticket = null; state.unknown = false; state.error = authored(operation.error?.code); state.message = '';
    } else if (operation.state === 'complete') {
      if (ticket.kind === 'visual' && (status.active.plugin_id !== ticket.pluginId ||
          status.configured.plugin_id !== ticket.pluginId ||
          status.active.resource_generation !== status.configured.resource_generation)) return;
      state.ticket = null; state.unknown = false; state.error = '';
      state.message = ticket.kind === 'visual' ? 'The host confirmed the selected visual interface.' : 'The host completed the optional plugin selection.';
      state.completed = {kind: ticket.kind, id: operation.id};
    } else {
      state.unknown = false; state.error = '';
      state.message = `Change ${operation.state === 'queued' ? 'queued' : 'running'}. Waiting for the host to finish…`;
    }
  }
  async function refresh() {
    if (!active || !isCurrent()) return;
    if (reading?.session === session) return reading.promise;
    stopTimer();
    const token = session;
    state.loading = true; publish();
    const promise = (async () => {
      try {
        const [visual, optional] = await Promise.all([request('/ui/status'), request('/plugins/runtime')]);
        if (!current(token)) return;
        state.visual = checked(visual, true); state.optional = checked(optional, false);
        if (readFailed) {state.error = state.unknown ? 'The outcome is unconfirmed. Refresh status; no automatic submission retry was made.' : ''; readFailed = false;}
        reconcile();
      } catch (_error) {
        if (!current(token)) return;
        state.visual = state.optional = null; readFailed = true;
        state.error = 'Plugin status could not be validated. Refresh before changing the selection.';
      } finally {
        if (current(token)) {
          state.loading = false; reading = null; publish(); if (current(token)) timer = schedule(refresh);
        }
      }
    })();
    reading = {session: token, promise};
    return promise;
  }
  async function review(work) {
    if (!active || !isCurrent() || !canChange()) return;
    const token = session, version = ++draft;
    state.busy = true; state.review = null; state.error = ''; state.completed = null; publish();
    try {
      if (await beforeChange() === false) throw {code: 'unsaved'};
      if (current(token)) {await work(token); if (draft !== version) state.review = null;}
    } catch (error) {
      if (current(token)) state.error = authored(error?.code);
    } finally {
      if (current(token)) {state.busy = false; publish();}
    }
  }
  const controller = {
    start() {active = true; session += 1; draft += 1; state.review = null; publish(); return refresh();},
    stop() {active = false; session += 1; draft += 1; stopTimer(); state.review = null; state.busy = false;},
    refresh,
    invalidate() {draft += 1; state.review = null; publish();},
    preview(pluginId) {return review(async token => {
      if (!plugin(pluginId)) throw invalid();
      const result = await request('/ui/preview', {pluginId, useInstalled: pluginId !== null}, 200);
      if (!UUID.test(result?.preview_id) || result.plugin_id !== pluginId || !HASH.test(result.selection_revision) ||
          result.requires_saved_views !== true || !['installed', 'saved-worker', 'approved-wheels'].includes(result.code)) throw invalid();
      if (current(token)) state.review = {kind: 'visual', id: result.preview_id, pluginId,
        revision: result.selection_revision, code: result.code};
    });},
    reviewOptional(plugins) {return review(async () => {
      if (!Array.isArray(plugins) || plugins.length > 100 || plugins.some(id => typeof id !== 'string' || !ID.test(id)) ||
          new Set(plugins).size !== plugins.length) throw invalid();
      state.review = {kind: 'optional', plugins: [...plugins]};
    });},
    async apply() {
      if (!active || !isCurrent() || !canChange() || !state.review) return;
      const token = session, plan = state.review;
      state.busy = true; state.error = ''; state.completed = null; publish();
      try {
        if (await beforeCommit(plan.kind) === false) throw {code: 'unsaved'};
        if (!current(token)) return;
        state.ticket = {kind: plan.kind, pluginId: plan.pluginId, previewId: plan.id, id: null};
        state.unknown = true; state.review = null;
        state.message = 'Submitted. Waiting for host confirmation…'; publish();
        try {
          const result = await request(plan.kind === 'visual' ? '/ui/commit' : '/plugins/management/transition',
            plan.kind === 'visual' ? {previewId: plan.id} : {plugins: plan.plugins}, 202);
          if (!current(token)) return;
          if (!UUID.test(result?.id) || result.accepted !== true) throw invalid();
          state.ticket.id = result.id; state.unknown = false;
          state.message = 'Change accepted. Waiting for the host to finish…';
        } catch (error) {
          if (!current(token)) return;
          if (error?.status && error.status < 500) {
            state.ticket = null; state.unknown = false; state.error = authored(error.code); state.message = '';
          } else {
            state.error = 'The outcome is unconfirmed. Refresh status; no automatic submission retry was made.';
            state.message = '';
          }
        }
        await refresh();
      } catch (error) {
        if (current(token)) state.error = authored(error?.code);
      } finally {
        if (current(token)) {state.busy = false; publish();}
      }
    },
    async acknowledgeUnknown() {
      await refresh();
      if (active && isCurrent() && healthy() && !inFlight() && !state.busy) {
        state.ticket = null; state.unknown = false; state.error = '';
        state.message = 'Status reviewed. No completion was assumed. Prepare a fresh review before another change.'; publish();
      }
    },
    async retryCleanup() {
      if (!active || !isCurrent() || state.busy || !(state.visual?.runtime.cleanup_failed > 0)) return;
      const token = session;
      state.busy = true; state.error = ''; publish();
      try {
        await request('/plugins/runtime/cleanup', {}, 200);
      } catch (_error) {
        if (current(token)) state.error = 'Cleanup is not confirmed. Refresh status before another explicit attempt.';
      } finally {
        if (current(token)) {state.busy = false; await refresh(); publish();}
      }
    },
    canChange,
  };
  return controller;
}

export function initVisualSettings({page = document, beforeChange, beforeCommit, onOptionalComplete,
  request: suppliedRequest, schedule, cancel} = {}) {
  const dialog = page.getElementById('plugins-dialog');
  const pane = dialog?.querySelector('[data-visual-settings]');
  if (!pane || pane.dataset.workspace !== 'personal') return null;
  const scope = getContext().apiPrefix;
  const isCurrent = () => getContext().apiPrefix === scope;
  const request = suppliedRequest || (async (path, body, expected = 200) => {
    const response = await fetch(`${scope}${path}`, {method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
      headers: {'Content-Type': 'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw Object.assign(new Error('selection_request_failed'), {status: response.status, code: result.code});
    }
    if (response.status !== expected) throw invalid();
    return response.json();
  });
  const find = name => pane.querySelector(`[data-visual-${name}]`);
  const form = find('form'), mode = find('mode'), target = find('target'), apply = find('apply');
  let controller, transferring = false, completed = null, notice = '', reviewed = null;
  function render(state) {
    const usable = controller?.canChange() ?? false;
    const active = state.visual?.active;
    find('current').textContent = active ? `${active.plugin_id || 'Headless'} · generation ${active.resource_generation.slice(0, 12)}` : 'Status unavailable';
    find('runtime').textContent = state.visual ? `Runtime ${state.visual.runtime.generation} · ${state.visual.runtime.health} · requests ${state.visual.runtime.admission}` : '';
    find('message').textContent = state.message || (state.loading ? 'Checking host status…' : state.review ? 'Review the target above, then apply or cancel.' : 'Preview a target before applying a change.');
    find('error').textContent = state.error; find('error').hidden = !state.error;
    find('refresh').disabled = state.loading;
    find('cleanup').hidden = !(state.visual?.runtime.cleanup_failed > 0);
    find('cleanup').disabled = state.busy || state.loading;
    form.querySelector('button').disabled = !usable;
    mode.disabled = !usable; target.disabled = !usable || mode.value === 'headless';
    apply.disabled = !usable || !state.review; apply.hidden = !state.review;
    find('dismiss').hidden = !state.review; find('dismiss').disabled = state.busy;
    find('unknown').hidden = !state.unknown; find('unknown').disabled = state.busy || state.loading;
    const plan = state.review;
    find('review').textContent = !plan ? '' : plan.kind === 'visual' ?
      `${plan.pluginId || 'Headless'} will replace the visual interface using ${plan.pluginId ? 'installed code' : 'no browser interface'}. Open views must save and acknowledge. ${plan.pluginId ? 'Readiness runs before activation.' : 'The browser interface will close; use the local host controls to restore one.'}` :
      `Optional selection: ${plan.plugins.join(', ') || 'none'}. The host will check live support and unfinished work before applying it. Storage and the visual interface are retained.`;
    find('review').hidden = !plan;
    const reviewKey = plan && (plan.id || plan.plugins.join(','));
    if (plan && reviewKey !== reviewed) {find('review').tabIndex = -1; find('review').focus();}
    reviewed = reviewKey;
    for (const button of dialog.querySelectorAll('[data-plugin-live-review]')) button.disabled = !usable;
    if (active && !target.dataset.edited && active.plugin_id) target.value = active.plugin_id;
    if (!dialog.open && state.error && state.error !== notice) {notice = state.error; notify(state.error);}
    if (state.completed && state.completed.id !== completed) {
      completed = state.completed.id;
      if (state.completed.kind === 'optional') onOptionalComplete?.();
    }
    pane.setAttribute('aria-busy', String(state.busy || state.loading));
    if (transferring && !dialog.open && !state.ticket && !state.busy) {transferring = false; controller.stop();}
  }
  controller = createVisualSettings({request, beforeChange, beforeCommit: async kind => {
    if (await beforeCommit?.(kind) === false) return false;
    if (kind === 'visual') {
      transferring = true;
      for (const opened of page.querySelectorAll('#settings-dialog[open], #plugins-dialog[open]')) opened.close();
    }
    return true;
  }, onState: render, isCurrent, schedule, cancel});
  find('refresh').addEventListener('click', controller.refresh);
  find('cleanup').addEventListener('click', controller.retryCleanup);
  find('dismiss').addEventListener('click', controller.invalidate);
  find('unknown').addEventListener('click', controller.acknowledgeUnknown);
  apply.addEventListener('click', controller.apply);
  for (const input of [mode, target]) input.addEventListener('input', () => {target.dataset.edited = 'true'; controller.invalidate();});
  form.addEventListener('submit', event => {event.preventDefault(); void controller.preview(mode.value === 'headless' ? null : target.value.trim());});
  for (const button of dialog.querySelectorAll('[data-plugin-live-review]')) button.addEventListener('click', () => {
    void controller.reviewOptional(new FormData(dialog.querySelector('[data-plugin-selection]')).getAll('plugin'));
  });
  dialog.querySelector('[data-plugin-selection]')?.addEventListener('change', controller.invalidate);
  for (const opener of page.querySelectorAll('[data-dialog="plugins-dialog"]')) opener.addEventListener('click', () => {transferring = false; void controller.start();});
  dialog.addEventListener('close', () => {if (!transferring) controller.stop();});
  page.defaultView?.addEventListener('pagehide', controller.stop);
  page.addEventListener('whab:before-navigate', event => {
    if (dialog.open && event.detail?.source !== 'plugin-settings') event.preventDefault();
  });
  if (dialog.open) void controller.start();
  return controller;
}
