/* Cell navigation stays separate from native field editing and financial saves. */
export function parseRegisterAmount(value, digits, allowZero = false) {
  const clean = value.trim();
  const match = clean.match(new RegExp(`^([+-]?)(\\d+)(?:\\.(\\d{1,${digits || 1}}))?$`));
  if (!match || !digits && match[3]) throw new Error(`Enter an amount with ${digits} decimal places or fewer.`);
  const minor = BigInt(match[2]) * 10n ** BigInt(digits) + BigInt((match[3] || '').padEnd(digits, '0'));
  if (!minor && !allowZero || minor > 1_000_000_000_000n) throw new Error('Enter a nonzero amount within the supported range.');
  // Register amounts show spending as negative; core uses positive spending.
  return match[1] === '-' ? Number(minor) : -Number(minor);
}

export function gridDestination(rows, cell, key, shift = false) {
  const columns = row => [...row.cells].filter(candidate => candidate.querySelector('input:not([type="hidden"]),select,textarea,button:not(:disabled)'));
  const row = cell.parentElement, current = columns(row), index = current.indexOf(cell);
  if (key === 'ArrowUp' || key === 'ArrowDown') {
    const next = rows[rows.indexOf(row) + (key === 'ArrowUp' ? -1 : 1)];
    return next && columns(next).find(candidate => candidate.cellIndex === cell.cellIndex);
  }
  if (key === 'ArrowLeft') return current[index - 1];
  if (key === 'ArrowRight') return current[index + 1];
  if (key === 'Home') return current[0];
  if (key === 'End') return current.at(-1);
  if (key === 'Tab') {const all = rows.flatMap(columns); return all[all.indexOf(cell) + (shift ? -1 : 1)];}
}

export function initTransactionGrid(table) {
  if (!table) return {focus() {}, dispose() {}};
  let active = null, editing = null, nativeArrows = false;
  const controller = new AbortController();
  const listen = (event, callback) => table.addEventListener(event, callback, {capture: true, signal: controller.signal});
  const cells = row => [...row.cells].filter(cell => cell.querySelector('input:not([type="hidden"]),select,textarea,button:not(:disabled)'));
  const rows = () => [...table.tBodies[0].rows].filter(row => !row.hidden);
  const field = cell => cell.querySelector('input:not([type="hidden"]),select,textarea,button:not(:disabled)');
  function finish() {
    if (!editing) return;
    const control = editing;
    editing = null;
    control.dispatchEvent(new Event('change', {bubbles: true}));
    delete control.dataset.gridEditing;
  }
  function focus(cell) {
    if (!cell) return;
    finish();
    if (active) {active.tabIndex = -1; active.classList.remove('grid-current');}
    active = cell; cell.tabIndex = 0; cell.classList.add('grid-current');
    cell.focus({preventScroll: true}); cell.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  function edit(cell, replacement, native = true) {
    const control = field(cell);
    if (!control) return;
    if (control.matches('button,input[type="checkbox"]')) {control.click(); return;}
    editing = control; nativeArrows = native || control.matches('select,input[type="date"]'); control.dataset.gridEditing = 'true';
    control.readOnly = false; control.focus();
    if (replacement !== undefined && !control.matches('select')) {
      control.value = replacement; control.dispatchEvent(new Event('input', {bubbles: true}));
    } else if (control.matches('input:not([type="date"]),textarea')) control.select();
  }
  for (const row of table.tBodies[0].rows) for (const cell of cells(row)) {
    cell.tabIndex = -1;
  }
  const first = rows().flatMap(cells)[0];
  if (first) first.tabIndex = 0;
  listen('pointerdown', event => {
    const cell = event.target.closest('td');
    if (!cell || !cells(cell.parentElement).includes(cell) || event.target === editing || event.target.closest('button,input[type="checkbox"]')) return;
    focus(cell); edit(cell, undefined, false);
  });
  listen('dblclick', event => {const cell = event.target.closest('td'); if (cell && field(cell)) {event.preventDefault(); focus(cell); edit(cell);}});
  listen('focusout', event => {
    if (event.target === editing && !event.target.closest('td').contains(event.relatedTarget)) finish();
  });
  listen('keydown', event => {
    if (event.isComposing || event.altKey || event.metaKey) return;
    const cell = event.target.closest('td');
    if (!cell || !field(cell)) return;
    if (event.key === 'Enter' && event.ctrlKey) {
      const form = cell.parentElement.matches('[data-new-transaction]') && document.querySelector('[data-transaction-create]');
      if (form) {event.preventDefault(); finish(); form.requestSubmit();}
      return;
    }
    if (event.ctrlKey) return;
    if (editing && event.key === 'F2') {event.preventDefault(); nativeArrows = true; return;}
    if (editing && event.key === 'Enter' && event.shiftKey && editing.matches('textarea')) return;
    if (editing && !['Enter', 'Escape', 'Tab'].includes(event.key)) {
      if (nativeArrows || event.shiftKey || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)) return;
      if (['ArrowLeft','ArrowRight'].includes(event.key) && (editing.selectionStart !== editing.selectionEnd ||
          event.key === 'ArrowLeft' && editing.selectionStart > 0 ||
          event.key === 'ArrowRight' && editing.selectionEnd < editing.value.length)) return;
    }
    if (editing && event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); focus(cell); return;}
    if (!editing && ['Enter', 'F2'].includes(event.key)) {event.preventDefault(); event.stopPropagation(); edit(cell); return;}
    const navigation = editing && event.key === 'Enter' ? 'ArrowDown' : event.key;
    if (!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','Tab'].includes(navigation)) {
      if (!editing && event.key.length === 1 && !event.shiftKey && !field(cell).matches('select,button,input[type="checkbox"]')) {
        event.preventDefault(); edit(cell, event.key);
      }
      return;
    }
    const target = gridDestination(rows(), cell, navigation, event.shiftKey);
    if (navigation === 'Tab' && !target) {finish(); return;}
    event.preventDefault(); event.stopPropagation(); focus(target || cell);
  });
  return {focus(control) {focus(control.closest('td')); edit(control.closest('td'));},
    dispose() {controller.abort();}};
}
