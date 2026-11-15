/* Account order is a browser layout preference, isolated by budget and workspace. */
export function sidebarOrderKey({environment, budgetId, workspace}) {
  return `whab.sidebar-order.v1:${[environment, budgetId, workspace].map(value => encodeURIComponent(value || '')).join(':')}`;
}

export function parseSidebarOrder(value) {
  try {
    const ids = JSON.parse(value);
    return Array.isArray(ids) && ids.length <= 10000 &&
      ids.every(id => typeof id === 'string' && id.length > 0 && id.length <= 512) &&
      new Set(ids).size === ids.length ? ids : null;
  } catch {return null;}
}

export function reconcileSidebarOrder(saved, defaults) {
  const known = new Set(defaults);
  const order = (saved || []).filter(id => known.has(id));
  const ranked = new Set(order);
  return [...order, ...defaults.filter(id => !ranked.has(id))];
}

// Replace only the visible group's slots; hidden accounts retain their positions.
export function mergeSidebarOrder(order, visible) {
  const changed = new Set(visible);
  let next = 0;
  return order.map(id => changed.has(id) ? visible[next++] : id);
}

export function initSidebarOrder({root = document, storage} = {}) {
  const states = new WeakMap(), preferences = new Map();
  const listeners = [];
  let dragging = null, dropTarget = null;
  function listen(target, name, handler, options) {
    target.addEventListener(name, handler, options);
    listeners.push(() => target.removeEventListener(name, handler, options));
  }
  function browserStorage() {return storage === undefined ? window.localStorage : storage;}
  function read(key) {
    if (!preferences.has(key)) {
      let value = null;
      try {value = parseSidebarOrder(browserStorage().getItem(key));} catch { /* This page still works. */ }
      preferences.set(key, value);
    }
    return preferences.get(key);
  }
  function rows(group) {return [...group.querySelectorAll('[data-account-order-row]')];}
  function announce(state, message) {state.element.querySelector('[data-account-order-status]').textContent = message;}
  function render(state) {
    const order = reconcileSidebarOrder(read(state.key), state.defaults);
    const rank = new Map(order.map((id, index) => [id, index]));
    const active = root.activeElement;
    state.element.dataset.reordering = String(state.editing);
    const toggle = state.element.querySelector('[data-account-reorder]');
    toggle.hidden = !state.element.querySelector('[data-account-order-row]');
    toggle.setAttribute('aria-expanded', String(state.editing));
    state.element.querySelector('[data-account-order-tools]').hidden = !state.editing;
    for (const group of state.element.querySelectorAll('[data-account-order-group]')) {
      const before = rows(group), sorted = [...before].sort((a, b) => rank.get(a.dataset.accountOrderRow) - rank.get(b.dataset.accountOrderRow));
      if (sorted.some((row, index) => row !== before[index])) group.append(...sorted);
      sorted.forEach((row, index) => {
        row.draggable = state.editing;
        const link = row.querySelector('a.sidebar-account');
        link.draggable = false;
        if (state.editing) link.setAttribute('tabindex', '-1');
        else link.removeAttribute('tabindex');
        row.querySelector('.sidebar-account-drag').hidden = !state.editing;
        row.querySelector('.sidebar-account-moves').hidden = !state.editing;
        row.querySelector('[data-account-move="up"]').disabled = index === 0;
        row.querySelector('[data-account-move="down"]').disabled = index === sorted.length - 1;
      });
    }
    if (active && state.element.contains(active)) {
      const row = active.closest('[data-account-order-row]');
      const alternative = row && [...row.querySelectorAll('[data-account-move]')].find(button => !button.disabled);
      (active.disabled ? alternative || toggle : active).focus({preventScroll: true});
    }
  }
  function discover() {
    for (const element of root.querySelectorAll('[data-sidebar-order]')) {
      if (states.has(element)) continue;
      const defaults = parseSidebarOrder(element.dataset.defaultOrder);
      if (!defaults || !element.dataset.budgetId || !element.dataset.workspace) continue;
      const state = {element, key: sidebarOrderKey(element.dataset), defaults, editing: false};
      states.set(element, state);
      render(state);
    }
  }
  function save(state, value) {
    preferences.set(state.key, value);
    let persisted = true;
    try {
      if (value === null) browserStorage().removeItem(state.key);
      else browserStorage().setItem(state.key, JSON.stringify(value));
    } catch {persisted = false;}
    render(state);
    return persisted ? 'Order saved in this browser.' : 'Order changed for this page. Browser storage is unavailable.';
  }
  function move(state, ordered, name) {
    const order = reconcileSidebarOrder(read(state.key), state.defaults);
    const result = save(state, mergeSidebarOrder(order, ordered.map(row => row.dataset.accountOrderRow)));
    announce(state, `${name} moved. ${result}`);
  }
  function find(target) {
    const element = target.closest?.('[data-sidebar-order]');
    return element && states.get(element);
  }
  function clearDrag() {
    dragging?.row.classList.remove('is-dragging');
    if (dropTarget) delete dropTarget.dataset.dropPosition;
    dropTarget = null;
    dragging = null;
  }
  function click(event) {
    const state = find(event.target);
    if (!state) return;
    const button = event.target.closest('button');
    if (button?.matches('[data-account-reorder], [data-account-order-done]')) {
      event.preventDefault();
      state.editing = !state.editing;
      announce(state, '');
      clearDrag();
      render(state);
      (state.editing ? state.element.querySelector('[data-account-order-done]') : state.element.querySelector('[data-account-reorder]')).focus({preventScroll: true});
    } else if (button?.matches('[data-account-order-reset]')) {
      event.preventDefault();
      announce(state, save(state, null));
    } else if (state.editing && button?.matches('[data-account-move]') && !button.disabled) {
      event.preventDefault();
      const row = button.closest('[data-account-order-row]'), group = row.closest('[data-account-order-group]');
      const ordered = rows(group), index = ordered.indexOf(row), direction = button.dataset.accountMove === 'up' ? -1 : 1;
      const destination = index + direction;
      if (destination < 0 || destination >= ordered.length) return;
      [ordered[index], ordered[destination]] = [ordered[destination], ordered[index]];
      move(state, ordered, row.querySelector('.sidebar-account-name').textContent);
      const focus = button.disabled ? row.querySelector(`[data-account-move="${direction === -1 ? 'down' : 'up'}"]`) : button;
      focus.focus({preventScroll: true});
    } else if (state.editing && event.target.closest('a.sidebar-account')) {
      // Capturing this event also blocks the register's in-place navigation listener.
      event.preventDefault();
      event.stopPropagation();
    }
  }
  function dragstart(event) {
    const state = find(event.target), row = event.target.closest('[data-account-order-row]');
    if (!state) return;
    if (!state?.editing || !row || event.target.closest('button')) {event.preventDefault(); return;}
    dragging = {state, row, group: row.closest('[data-account-order-group]')};
    row.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', row.dataset.accountOrderRow);
  }
  function dragover(event) {
    if (!dragging) return;
    const row = event.target.closest('[data-account-order-row]');
    if (!row || row === dragging.row || row.closest('[data-account-order-group]') !== dragging.group) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    if (dropTarget && dropTarget !== row) delete dropTarget.dataset.dropPosition;
    dropTarget = row;
    const rect = row.getBoundingClientRect();
    row.dataset.dropPosition = event.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
  }
  function drop(event) {
    if (!dragging) return;
    event.preventDefault();
    const row = event.target.closest('[data-account-order-row]');
    if (!row || row !== dropTarget || row.closest('[data-account-order-group]') !== dragging.group) {clearDrag(); return;}
    const {state, group, row: moved} = dragging;
    const ordered = rows(group).filter(value => value !== moved);
    ordered.splice(ordered.indexOf(row) + (row.dataset.dropPosition === 'after' ? 1 : 0), 0, moved);
    clearDrag();
    move(state, ordered, moved.querySelector('.sidebar-account-name').textContent);
  }
  listen(root, 'click', click, true);
  listen(root, 'keydown', event => {
    const state = find(event.target);
    if (event.key !== 'Escape' || !state?.editing) return;
    event.preventDefault();
    state.editing = false;
    clearDrag();
    render(state);
    state.element.querySelector('[data-account-reorder]').focus({preventScroll: true});
  });
  listen(root, 'dragstart', dragstart);
  listen(root, 'dragover', dragover);
  listen(root, 'drop', drop);
  listen(root, 'dragend', clearDrag);
  listen(root, 'whab:page-ready', discover);
  listen(window, 'storage', event => {
    if (event.key !== null && !event.key.startsWith('whab.sidebar-order.v1:')) return;
    if (event.key === null) preferences.clear();
    else preferences.set(event.key, parseSidebarOrder(event.newValue));
    for (const element of root.querySelectorAll('[data-sidebar-order]')) {
      const state = states.get(element);
      if (state && (event.key === null || state.key === event.key)) {clearDrag(); render(state);}
    }
  });
  discover();
  const observer = new MutationObserver(discover);
  // Fragment navigation and account saves replace the content of this mounted shell.
  for (const sidebar of root.querySelectorAll('.sidebar-accounts')) observer.observe(sidebar, {childList: true, subtree: true});
  return {refresh: discover, dispose() {clearDrag(); observer.disconnect(); listeners.forEach(remove => remove());}};
}
