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
  const options = [...list.querySelectorAll('[data-account-option]')];
  const listeners = [];
  let committed = new Set(options.filter(option => option.dataset.value !== 'all' && option.getAttribute('aria-selected') === 'true').map(option => option.dataset.value));
  let draft = new Set(committed), active = 0, typed = '', typedAt = 0;
  function listen(target, name, handler) {
    target.addEventListener(name, handler);
    listeners.push(() => target.removeEventListener(name, handler));
  }
  function render() {
    options.forEach(option => option.setAttribute('aria-selected', String(option.dataset.value === 'all' ? !draft.size : draft.has(option.dataset.value))));
    const source = draft.size === 1 ? options.find(option => draft.has(option.dataset.value)) : options[0];
    value.replaceChildren(...[...source.childNodes].map(child => child.cloneNode(true)));
    if (draft.size > 1) value.querySelector('[data-account-picker-label]').textContent = `${draft.size} accounts`;
    for (const image of value.querySelectorAll('[data-bank-logo]')) delete image.dataset.logoBound;
    bindLogos(value);
  }
  function highlight(index) {
    active = (index + options.length) % options.length;
    options.forEach((option, offset) => option.classList.toggle('is-active', offset === active));
    button.setAttribute('aria-activedescendant', options[active].id);
    options[active].scrollIntoView({block: 'nearest'});
  }
  function open() {
    draft = new Set(committed);
    popup.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    highlight(Math.max(0, options.findIndex(option => committed.has(option.dataset.value))));
    typed = '';
  }
  function close(commit = false, focus = false) {
    if (popup.hidden) return;
    popup.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    button.removeAttribute('aria-activedescendant');
    typed = '';
    if (focus) button.focus();
    const changed = draft.size !== committed.size || [...draft].some(id => !committed.has(id));
    if (!commit) { draft = new Set(committed); render(); return; }
    committed = new Set(draft);
    for (const option of native.options) option.selected = option.value === 'all' ? !draft.size : draft.has(option.value);
    if (changed) onChoose(options.filter(option => committed.has(option.dataset.value)).map(option => option.dataset.value));
  }
  function toggle(option) {
    if (option.dataset.value === 'all') draft.clear();
    else if (draft.has(option.dataset.value)) draft.delete(option.dataset.value);
    else draft.add(option.dataset.value);
    render();
    highlight(options.indexOf(option));
    button.focus();
  }
  listen(button, 'click', () => { if (popup.hidden) open(); else close(true, true); });
  listen(picker.querySelector('[data-account-picker-apply]'), 'click', () => close(true, true));
  listen(list, 'click', event => {
    const option = event.target.closest('[data-account-option]');
    if (options.includes(option)) toggle(option);
  });
  listen(button, 'keydown', event => {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'Escape' || event.key === 'Tab') {
      if (!popup.hidden) {
        if (event.key === 'Escape') event.preventDefault();
        close(event.key === 'Tab', event.key === 'Escape');
      }
      return;
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const wasOpen = !popup.hidden;
      if (!wasOpen) open();
      if (event.key === 'Home') highlight(0);
      else if (event.key === 'End') highlight(options.length - 1);
      else if (wasOpen) highlight(active + (event.key === 'ArrowDown' ? 1 : -1));
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
      const offset = Array.from({length: options.length}, (_, index) => (active + index + 1) % options.length)
        .find(index => options[index].querySelector('[data-account-picker-label]').textContent.trim().toLocaleLowerCase().startsWith(typed));
      if (offset !== undefined) highlight(offset);
    }
  });
  listen(document, 'pointerdown', event => { if (!picker.contains(event.target)) close(true); });
  native.hidden = true;
  picker.hidden = false;
  return {dispose() { listeners.forEach(remove => remove()); close(); picker.hidden = true; native.hidden = false; }};
}
