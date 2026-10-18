/* Presets resolve on the server; custom fields send inclusive calendar dates. */
export function initPeriodPicker(root, onChoose) {
  const picker = root.querySelector('[data-period-picker]');
  if (!picker) return {dispose() {}};
  const button = picker.querySelector('#transaction-period-toggle');
  const popup = picker.querySelector('[data-period-popup]');
  const form = picker.querySelector('[data-period-form]');
  const error = picker.querySelector('[data-period-error]');
  const start = form.elements.start_date, end = form.elements.end_date;
  const initial = [start.value, end.value];
  const listeners = [];
  function listen(target, name, handler) {
    target.addEventListener(name, handler);
    listeners.push(() => target.removeEventListener(name, handler));
  }
  function close(focus = false) {
    popup.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    start.value = initial[0];
    end.value = initial[1];
    error.hidden = true;
    if (focus) button.focus();
  }
  listen(button, 'click', () => {
    if (!popup.hidden) { close(); return; }
    popup.hidden = false;
    button.setAttribute('aria-expanded', 'true');
  });
  for (const preset of picker.querySelectorAll('[data-period-choice]')) {
    listen(preset, 'click', () => {
      close(true);
      onChoose({period: preset.dataset.periodChoice, month: 'current-month'});
    });
  }
  listen(form, 'submit', event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    if (start.value && end.value && start.value > end.value) {
      error.textContent = 'Start date must be on or before end date.';
      error.hidden = false;
      return;
    }
    const range = {period: 'custom', startDate: start.value, endDate: end.value};
    close(true);
    onChoose(range);
  });
  listen(picker, 'keydown', event => {
    if (event.key === 'Escape' && !popup.hidden) { event.preventDefault(); close(true); }
  });
  listen(picker, 'focusout', event => { if (!picker.contains(event.relatedTarget)) close(); });
  listen(document, 'pointerdown', event => { if (!picker.contains(event.target)) close(); });
  return {dispose() { listeners.forEach(remove => remove()); close(); }};
}
