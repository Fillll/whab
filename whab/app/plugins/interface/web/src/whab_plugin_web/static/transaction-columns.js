/* Browser-only layout preferences: no ledger reads, writes or financial data. */
const STORAGE_KEY = 'whab.transaction-column-widths.v1';
const MIN_WIDTH = 100;
const MAX_WIDTH = 1000;
const KEYS = ['account', 'merchant', 'category', 'note'];
const clamp = width => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(width)));

export function initTransactionColumns(table) {
  if (!table?.tHead) return {dispose() {}};
  const headers = [...table.tHead.rows[0].cells];
  const handles = [...table.querySelectorAll('[data-resize-column]')];
  const listeners = [];
  let widths = {}, group = null, drag = null;
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY));
    for (const key of KEYS) {
      if (typeof saved?.[key] === 'number' && Number.isFinite(saved[key])) widths[key] = clamp(saved[key]);
    }
  } catch { /* Disabled storage or invalid preferences must not disable the register. */ }

  function listen(target, type, callback) {
    target.addEventListener(type, callback);
    listeners.push(() => target.removeEventListener(type, callback));
  }
  function persist() {
    try {
      if (Object.keys(widths).length) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch { /* Resizing still works for this view when storage is unavailable. */ }
  }
  function announce() {
    for (const handle of handles) {
      const width = Math.round(handle.parentElement.getBoundingClientRect().width);
      handle.setAttribute('aria-valuenow', String(width));
      handle.setAttribute('aria-valuetext', `${width} pixels`);
    }
  }
  function freeze() {
    if (group) return;
    const measured = headers.map(header => header.getBoundingClientRect().width);
    group = document.createElement('colgroup');
    for (const width of measured) {
      const column = document.createElement('col');
      column.style.width = `${width}px`;
      group.append(column);
    }
    table.prepend(group);
    table.dataset.columnWidths = 'true';
    table.style.width = `${measured.reduce((total, width) => total + width, 0)}px`;
  }
  function resize(handle, width) {
    freeze();
    group.children[headers.indexOf(handle.parentElement)].style.width = `${clamp(width)}px`;
    table.style.width = `${[...group.children].reduce((total, column) => total + parseFloat(column.style.width), 0)}px`;
    announce();
  }
  function restoreLayout() {
    group?.remove();
    group = null;
    delete table.dataset.columnWidths;
    table.style.width = '';
    for (const handle of handles) {
      const width = widths[handle.dataset.resizeColumn];
      if (width !== undefined) resize(handle, width);
    }
    announce();
  }
  function reset(handle) {
    delete widths[handle.dataset.resizeColumn];
    restoreLayout();
    persist();
  }
  function finish(commit) {
    if (!drag) return;
    const {handle, pointerId, previous} = drag;
    drag = null;
    table.classList.remove('is-resizing-columns');
    if (commit) {
      widths[handle.dataset.resizeColumn] = clamp(handle.parentElement.getBoundingClientRect().width);
      persist();
    } else {
      if (previous === undefined) delete widths[handle.dataset.resizeColumn];
      else widths[handle.dataset.resizeColumn] = previous;
      restoreLayout();
    }
    if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
  }

  for (const handle of handles) {
    listen(handle, 'pointerdown', event => {
      if (event.button !== 0 || drag) return;
      event.preventDefault();
      event.stopPropagation();
      handle.focus();
      freeze();
      drag = {handle, pointerId: event.pointerId, x: event.clientX,
        width: handle.parentElement.getBoundingClientRect().width, previous: widths[handle.dataset.resizeColumn]};
      handle.setPointerCapture(event.pointerId);
      table.classList.add('is-resizing-columns');
    });
    listen(handle, 'pointermove', event => {
      if (drag?.handle === handle && drag.pointerId === event.pointerId) resize(handle, drag.width + event.clientX - drag.x);
    });
    listen(handle, 'pointerup', event => {if (drag?.pointerId === event.pointerId) finish(true);});
    listen(handle, 'pointercancel', () => finish(false));
    listen(handle, 'lostpointercapture', () => finish(false));
    listen(handle, 'click', event => {event.preventDefault(); event.stopPropagation();});
    listen(handle, 'dblclick', event => {event.preventDefault(); event.stopPropagation(); reset(handle);});
    listen(handle, 'keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'Escape'].includes(event.key)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape') {finish(false); return;}
      if (drag) finish(false);
      if (event.key === 'Home') {reset(handle); return;}
      const step = (event.shiftKey ? 50 : 10) * (event.key === 'ArrowRight' ? 1 : -1);
      const width = clamp(handle.parentElement.getBoundingClientRect().width + step);
      widths[handle.dataset.resizeColumn] = width;
      resize(handle, width);
      persist();
    });
  }
  restoreLayout();
  return {dispose() {finish(false); for (const remove of listeners) remove();}};
}
