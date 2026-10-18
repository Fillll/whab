/* Draft account choices together, then navigate once when the picker closes. */
import {bindLogos} from './logo-images.js';

export function initAccountPicker(root, onChoose) {
  const picker = root.querySelector('[data-account-picker]');
  const native = root.querySelector('#account-filter');
  if (!picker || !native) return {dispose() {}};
  const button = picker.querySelector('#account-filter-toggle');
  const popup = picker.querySelector('[data-account-picker-popup]');
  const list = picker.querySelector('[role="listbox"]');
  const value = picker.querySelector('[data-account-picker-value]');
  const search = picker.querySelector('[data-account-picker-search]');
  const apply = picker.querySelector('[data-account-picker-apply]');
  const selectAll = picker.querySelector('[data-account-picker-select-all]');
  const selectNone = picker.querySelector('[data-account-picker-select-none]');
  const count = picker.querySelector('[data-account-picker-count]');
  const empty = picker.querySelector('[data-account-picker-empty]');
  const options = [...list.querySelectorAll('[data-account-option]')];
  const accounts = options.filter(option => option.dataset.value !== 'all');
  const allIds = accounts.map(option => option.dataset.value);
  const listeners = [];
  const normalize = selected => selected.size === accounts.length ? new Set() : new Set(selected);
  const expand = selected => new Set(selected.size ? selected : allIds);
  let committed = normalize(new Set(accounts.filter(option => option.getAttribute('aria-selected') === 'true').map(option => option.dataset.value)));
  let draft = expand(committed), active = 0, typed = '', typedAt = 0;
  function listen(target, name, handler) {
    target.addEventListener(name, handler);
    listeners.push(() => target.removeEventListener(name, handler));
  }
  function visible() { return options.filter(option => !option.hidden); }
  function render() {
    const all = draft.size === accounts.length && draft.size > 0;
    options.forEach(option => option.setAttribute('aria-selected', String(option.dataset.value === 'all' ? all : draft.has(option.dataset.value))));
    const source = draft.size === 1 && !all ? accounts.find(option => draft.has(option.dataset.value)) : options[0];
    value.replaceChildren(...[...source.childNodes].map(child => child.cloneNode(true)));
    const label = value.querySelector('[data-account-picker-label]');
    if (!draft.size) label.textContent = 'No accounts selected';
    else if (!all && draft.size > 1) label.textContent = `${draft.size} accounts`;
    count.textContent = draft.size ? `${draft.size} of ${accounts.length} selected` : 'No accounts selected. Select at least one account to apply.';
    apply.disabled = !draft.size;
    for (const image of value.querySelectorAll('[data-bank-logo]')) delete image.dataset.logoBound;
    bindLogos(value);
  }
  function highlight(index) {
    const choices = visible();
    const option = choices.includes(options[index]) ? options[index] : choices[0];
    active = options.indexOf(option);
    options.forEach(item => item.classList.toggle('is-active', item === option && !popup.hidden));
    if (option && !popup.hidden) {
      search.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({block: 'nearest'});
    } else search.removeAttribute('aria-activedescendant');
  }
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    options.forEach(option => {
      option.hidden = option.dataset.value === 'all' ? !!query : !option.querySelector('[data-account-picker-label]').textContent.toLocaleLowerCase().includes(query);
    });
    empty.hidden = accounts.some(option => !option.hidden);
    highlight(active);
    typed = '';
  }
  function move(key) {
    const choices = visible();
    if (!choices.length) return;
    const current = choices.indexOf(options[active]);
    const index = key === 'Home' ? 0 : key === 'End' ? choices.length - 1 : (current + (key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length;
    highlight(options.indexOf(choices[index]));
  }
  function open() {
    draft = expand(committed);
    search.value = '';
    popup.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    active = Math.max(0, options.findIndex(option => draft.has(option.dataset.value)));
    filter();
    render();
    search.focus();
  }
  function close(commit = false, focus = false) {
    if (popup.hidden) return;
    popup.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    search.removeAttribute('aria-activedescendant');
    options.forEach(option => option.classList.remove('is-active'));
    typed = '';
    if (focus) button.focus();
    if (!commit || !draft.size) { draft = expand(committed); render(); return; }
    const next = normalize(draft);
    const changed = next.size !== committed.size || [...next].some(id => !committed.has(id));
    committed = next;
    for (const option of native.options) option.selected = option.value === 'all' ? !committed.size : committed.has(option.value);
    if (changed) onChoose(accounts.filter(option => committed.has(option.dataset.value)).map(option => option.dataset.value));
  }
  function toggle(option) {
    if (!option || option.hidden) return;
    if (option.dataset.value === 'all') draft = new Set(allIds);
    else if (draft.has(option.dataset.value)) draft.delete(option.dataset.value);
    else draft.add(option.dataset.value);
    render();
    highlight(options.indexOf(option));
  }
  listen(button, 'click', () => { if (popup.hidden) open(); else close(true, true); });
  listen(apply, 'click', () => { if (draft.size) close(true, true); });
  listen(selectAll, 'click', () => { draft = new Set(allIds); render(); });
  listen(selectNone, 'click', () => { draft.clear(); render(); });
  listen(search, 'input', filter);
  listen(list, 'click', event => {
    const option = event.target.closest('[data-account-option]');
    if (options.includes(option)) { toggle(option); search.focus(); }
  });
  listen(picker, 'keydown', event => {
    if (event.key === 'Escape' && !popup.hidden) { event.preventDefault(); close(false, true); }
  });
  listen(picker, 'focusout', event => { if (!picker.contains(event.relatedTarget)) close(true); });
  listen(search, 'keydown', event => {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); move(event.key);
    } else if (event.key === 'Enter') { event.preventDefault(); toggle(options[active]); }
  });
  listen(button, 'keydown', event => {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const wasOpen = !popup.hidden;
      if (!wasOpen) open();
      if (wasOpen || event.key === 'Home' || event.key === 'End') move(event.key);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (popup.hidden) open();
      else if (event.key === 'Enter') close(true, true);
      else toggle(options[active]);
    } else if (event.key.length === 1) {
      event.preventDefault();
      if (popup.hidden) open();
      const now = Date.now(), letter = event.key.toLocaleLowerCase();
      typed = now - typedAt > 700 || typed === letter ? letter : typed + letter;
      typedAt = now;
      const choices = visible(), current = choices.indexOf(options[active]);
      const match = Array.from({length: choices.length}, (_, index) => choices[(current + index + 1) % choices.length])
        .find(option => option.querySelector('[data-account-picker-label]').textContent.trim().toLocaleLowerCase().startsWith(typed));
      if (match) highlight(options.indexOf(match));
    }
  });
  listen(document, 'pointerdown', event => { if (!picker.contains(event.target)) close(true); });
  native.hidden = true;
  picker.hidden = false;
  return {dispose() { listeners.forEach(remove => remove()); close(); picker.hidden = true; native.hidden = false; }};
}
