/* Save budget edits in order; Python remains the source of every displayed total. */
import {api} from './client.js';

const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

export function createSaveQueue({save, refresh, onCommit = () => {}, onState = () => {}, onIdle = () => {}}) {
  const pending = new Map();
  const saved = new Map();
  const errors = new Map();
  const waiters = [];
  let active = null;
  let running = false;
  let disposed = false;
  const queue = {
    remember(key, payload) { if (!disposed) saved.set(key, payload); },
    enqueue(key, payload) {
      if (disposed) return Promise.resolve();
      const desired = pending.get(key) ?? (active?.key === key ? active.payload : saved.get(key));
      if (same(desired, payload) && !errors.has(key)) return queue.whenIdle();
      pending.delete(key);
      pending.set(key, payload);
      errors.delete(key);
      onState(key, {status: 'queued'});
      void drain();
      return queue.whenIdle();
    },
    discard(key) { pending.delete(key); errors.delete(key); },
    hasPending() { return running || pending.size > 0; },
    hasErrors() { return errors.size > 0; },
    whenIdle() { return queue.hasPending() ? new Promise(resolve => waiters.push(resolve)) : Promise.resolve(); },
    dispose() {
      if (queue.hasPending() || queue.hasErrors()) return false;
      disposed = true;
      pending.clear();
      saved.clear();
      errors.clear();
      return true;
    },
  };
  async function drain() {
    if (running) return;
    running = true;
    while (pending.size) {
      const [key, payload] = pending.entries().next().value;
      pending.delete(key);
      active = {key, payload};
      onState(key, {status: 'saving'});
      let committed = false;
      try {
        if (!same(saved.get(key), payload)) {
          await save(key, payload);
          saved.set(key, payload);
          onCommit(key, payload);
        }
        committed = true;
        await refresh();
        errors.delete(key);
        if (!pending.has(key)) onState(key, {status: 'saved'});
      } catch (failure) {
        const error = committed ? new Error('Saved, but totals could not be refreshed. Save again to retry the refresh.') : failure;
        errors.set(key, error);
        if (!pending.has(key)) onState(key, {status: 'error', error, committed});
      }
      active = null;
    }
    running = false;
    for (const resolve of waiters.splice(0)) resolve();
    onIdle();
  }
  return queue;
}

function nodeKey(node) {
  return node?.nodeType === 1 ? node.getAttribute('data-budget-key') || (node.id ? `id:${node.id}` : null) : null;
}

// Reconcile existing nodes, including rollover columns, without replacing the table or editor.
export function patchBudget(current, fresh, preserveInput = () => false) {
  function patch(oldNode, newNode) {
    if (oldNode.nodeType !== newNode.nodeType || oldNode.nodeName !== newNode.nodeName) {
      oldNode.replaceWith(newNode.cloneNode(true));
      return;
    }
    if (oldNode.nodeType !== 1) {
      if (oldNode.nodeValue !== newNode.nodeValue) oldNode.nodeValue = newNode.nodeValue;
      return;
    }
    if (oldNode.hasAttribute('data-budget-local')) return;
    const editing = oldNode.matches('input, textarea, select');
    const protectedInput = editing && preserveInput(oldNode, newNode);
    const localAttribute = name => ['open', 'aria-expanded'].includes(name) ||
      oldNode.matches('form') && ['data-save-state', 'aria-busy'].includes(name) ||
      oldNode.hasAttribute('data-budget-group-member') && name === 'hidden' ||
      protectedInput && ['value', 'checked', 'aria-invalid'].includes(name);
    for (const attribute of [...oldNode.attributes]) {
      if (localAttribute(attribute.name)) continue;
      if (!newNode.hasAttribute(attribute.name)) oldNode.removeAttribute(attribute.name);
    }
    for (const attribute of newNode.attributes) {
      if (localAttribute(attribute.name)) continue;
      if (oldNode.getAttribute(attribute.name) !== attribute.value) oldNode.setAttribute(attribute.name, attribute.value);
    }
    if (editing && !protectedInput) {
      if (oldNode.value !== newNode.value) oldNode.value = newNode.value;
      if (oldNode.type === 'radio' || oldNode.type === 'checkbox') oldNode.checked = newNode.checked;
    }
    const oldChildren = [...oldNode.childNodes];
    const keyed = new Map(oldChildren.map(child => [nodeKey(child), child]).filter(([key]) => key));
    const retained = new Set();
    let cursor = oldNode.firstChild;
    for (const newChild of newNode.childNodes) {
      const key = nodeKey(newChild);
      let child = key ? keyed.get(key) : cursor;
      if (!key && (nodeKey(child) || child?.nodeType !== newChild.nodeType || child?.nodeName !== newChild.nodeName)) child = null;
      if (!child) {
        child = newChild.cloneNode(true);
        oldNode.insertBefore(child, cursor);
      } else {
        if (child !== cursor) oldNode.insertBefore(child, cursor);
        patch(child, newChild);
      }
      retained.add(child);
      cursor = child.nextSibling;
    }
    for (const child of oldChildren) if (!retained.has(child)) child.remove();
  }
  patch(current, fresh);
}

export function syncBudgetGroups(root) {
  const groups = new Map([...root.querySelectorAll('[data-budget-group]')].map(button =>
    [button.dataset.budgetGroup, button.getAttribute('aria-expanded') === 'true']));
  for (const row of root.querySelectorAll('[data-budget-group-member]')) {
    if (groups.has(row.dataset.budgetGroupMember)) row.hidden = !groups.get(row.dataset.budgetGroupMember);
  }
}

export function initBudgetEdits({readForm, onDraft = () => {}}) {
  let planner = document.querySelector('.budget-planner');
  if (!planner) return {flush: async () => true, navigate: () => false, dispose: () => true};
  const displayedUrl = location.href;
  const states = new Map();
  const listeners = [];
  let navigation = null;
  let navigating = false;
  let disposed = false;

  function listen(target, event, callback) {
    target.addEventListener(event, callback);
    listeners.push(() => target.removeEventListener(event, callback));
  }
  function editStarted() {
    if (navigation) {
      const previous = navigation;
      navigation = null;
      previous.onCancel();
    }
    onDraft();
  }

  const snapshot = form => [...form.querySelectorAll('input, textarea, select')].map(input =>
    [input.name, input.value, input.checked]);
  function authoritative(form) {
    try { return readForm(form, {authoritative: true}); }
    catch { return null; }
  }
  function stateFor(form) {
    if (!states.has(form)) {
      const state = {saved: authoritative(form), snapshot: snapshot(form), amountValue: form.querySelector('[data-cents]')?.value, status: ''};
      states.set(form, state);
      queue.remember(form, state.saved);
    }
    return states.get(form);
  }
  function formDirty(form) {
    try {
      const state = stateFor(form);
      const payload = readForm(form, {authoritative: true});
      return !same(payload, state.saved) && !same(payload, state.deferredFrom);
    }
    catch { return !same(snapshot(form), stateFor(form).snapshot); }
  }
  function feedback(form, status, message = '') {
    const state = stateFor(form);
    state.status = status;
    form.dataset.saveState = status;
    if (status === 'queued' || status === 'saving') form.setAttribute('aria-busy', 'true');
    else form.removeAttribute('aria-busy');
    const error = form.querySelector('.field-error');
    if (error) {
      error.textContent = message;
      error.hidden = status !== 'error';
    }
    const amount = form.querySelector('[data-cents]');
    if (status === 'error') amount?.setAttribute('aria-invalid', 'true');
    else amount?.removeAttribute('aria-invalid');
    const notice = form.querySelector('[data-budget-save-status]');
    if (notice) {
      notice.textContent = status === 'queued' ? 'Waiting to save…' : status === 'saving' ? 'Saving…' : status === 'saved' ? 'Saved' : status === 'unsaved' ? 'Unsaved changes' : '';
      notice.hidden = !notice.textContent;
    }
  }
  const queue = createSaveQueue({
    save: (form, payload) => api(form.dataset.api, payload, form.dataset.method || 'POST'),
    onCommit(form, payload) {
      stateFor(form).saved = payload;
      stateFor(form).snapshot = null;
    },
    onState(form, {status, error}) { feedback(form, status === 'saved' && formDirty(form) ? 'unsaved' : status, error?.message); },
    async refresh() {
      const response = await fetch(displayedUrl, {headers: {'X-WHAB-Budget-Fragment': '1'}, cache: 'no-store'});
      if (!response.ok) throw new Error('The budget totals could not be refreshed.');
      const html = await response.text();
      const fresh = new DOMParser().parseFromString(html, 'text/html').querySelector('.budget-planner');
      if (!fresh || ['month', 'currency', 'workspace', 'budgetId', 'budgetRoute'].some(key => !fresh.dataset[key] || fresh.dataset[key] !== planner.dataset[key])) {
        throw new Error('The budget context changed.');
      }
      const dirtyForms = new Set([...states.keys()].filter(formDirty));
      const cleanForms = new Set([...states].filter(([form, state]) =>
        !dirtyForms.has(form) && !['error', 'queued', 'saving'].includes(state.status)).map(([form]) => form));
      const scroll = planner.querySelector('.budget-matrix-scroll');
      const scrollLeft = scroll?.scrollLeft;
      const scrollTop = window.scrollY;
      patchBudget(planner, fresh, (input, nextInput) => {
        const form = input.closest('form');
        if (!form || !states.has(form)) return false;
        const state = stateFor(form);
        const dirty = dirtyForms.has(form);
        const focused = input === document.activeElement && input.hasAttribute('data-cents');
        const saving = ['error', 'queued', 'saving'].includes(state.status);
        if (focused && !dirty && !saving && input.value !== nextInput.value) {
          state.deferredFrom = authoritative(form);
          state.deferredValue = nextInput.value;
          state.saved = authoritative(nextInput.closest('form'));
          state.snapshot = snapshot(form);
          state.amountValue = nextInput.value;
          queue.remember(form, state.saved);
        }
        return focused || dirty || saving;
      });
      syncBudgetGroups(planner);
      // A different editor for the same category (the inspector) may also have changed.
      for (const form of states.keys()) {
        if (form.isConnected && cleanForms.has(form) && !form.contains(document.activeElement)) {
          stateFor(form).saved = authoritative(form);
          stateFor(form).snapshot = snapshot(form);
          stateFor(form).amountValue = form.querySelector('[data-cents]')?.value;
          queue.remember(form, stateFor(form).saved);
        }
      }
      if (scroll && scroll.scrollLeft !== scrollLeft) scroll.scrollLeft = scrollLeft;
      if (window.scrollY !== scrollTop) window.scrollTo({top: scrollTop, behavior: 'instant'});
      bindForms();
    },
  });

  function submit(form) {
    try {
      if (!formDirty(form) && !['error', 'queued', 'saving'].includes(stateFor(form).status)) return true;
      const payload = readForm(form);
      stateFor(form);
      void queue.enqueue(form, payload);
      return true;
    } catch (failure) {
      feedback(form, 'error', failure.message);
      return false;
    }
  }
  function bindForms() {
    for (const form of planner.querySelectorAll('form[data-inline-assignment], form[data-budget-mutation]')) {
      if (states.has(form)) continue;
      stateFor(form);
      const amount = form.querySelector('[data-cents]');
      if (amount) listen(amount, 'input', () => {
        editStarted();
        delete stateFor(form).deferredFrom;
        delete stateFor(form).deferredValue;
        if (!['error', 'queued', 'saving'].includes(stateFor(form).status)) feedback(form, formDirty(form) ? 'unsaved' : '');
      });
      listen(form, 'submit', event => { event.preventDefault(); editStarted(); submit(form); });
      if (form.hasAttribute('data-auto')) listen(form, 'change', () => { editStarted(); submit(form); });
      if (!amount) continue;
      const inline = form.hasAttribute('data-inline-assignment');
      if (inline) listen(amount, 'focus', () => amount.select());
      listen(amount, 'blur', () => {
        const state = stateFor(form);
        if (state.deferredValue !== undefined && same(authoritative(form), state.deferredFrom)) amount.value = state.deferredValue;
        delete state.deferredFrom;
        delete state.deferredValue;
        if (inline) {
          if (formDirty(form)) editStarted();
          submit(form);
        }
      });
      listen(amount, 'keydown', event => {
        if (event.key === 'Enter') {
          event.preventDefault();
          editStarted();
          submit(form);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          editStarted();
          const state = stateFor(form);
          const saved = state.saved?.assigned;
          const digits = Number(amount.dataset.minorUnits);
          if (Number.isSafeInteger(saved)) {
            const value = BigInt(saved);
            const sign = value < 0n ? '-' : '';
            const whole = (value < 0n ? -value : value).toString().padStart(digits + 1, '0');
            amount.value = sign + (digits ? `${whole.slice(0, -digits)}.${whole.slice(-digits)}` : whole);
          } else amount.value = state.amountValue;
          queue.discard(form);
          feedback(form, '');
          amount.blur();
        }
      });
    }
  }
  bindForms();
  async function flush() {
    if (disposed) return true;
    let valid = true;
    for (const [form, state] of states) {
      if (!form.isConnected) continue;
      if (state.status === 'error') valid = false;
      else if (formDirty(form)) valid = submit(form) && valid;
    }
    await queue.whenIdle();
    return valid && !queue.hasErrors() && ![...states].some(([form, state]) =>
      form.isConnected && (state.status === 'error' || formDirty(form)));
  }
  function navigate(url, onCancel = () => {}) {
    if (disposed) return false;
    navigation = {url, onCancel};
    if (navigating) return true;
    navigating = true;
    void (async () => {
      const saved = await flush();
      const destination = navigation;
      navigation = null;
      navigating = false;
      if (!destination) return;
      if (saved) location.assign(destination.url);
      else destination.onCancel();
    })();
    return true;
  }
  listen(document, 'click', event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
    if (!queue.hasPending() && !queue.hasErrors() && ![...states.keys()].some(form => form.isConnected && formDirty(form))) return;
    event.preventDefault();
    navigate(link.href);
  });
  listen(window, 'beforeunload', event => {
    if (!queue.hasPending() && !queue.hasErrors() && ![...states.keys()].some(form => form.isConnected && formDirty(form))) return;
    event.preventDefault();
    event.returnValue = '';
  });
  return {
    flush, navigate,
    dispose() {
      if (disposed) return true;
      if ([...states].some(([form, state]) => state.status === 'error' || formDirty(form)) || !queue.dispose()) return false;
      disposed = true;
      navigation = null;
      for (const remove of listeners.splice(0)) remove();
      states.clear();
      planner = null;
      return true;
    },
  };
}
