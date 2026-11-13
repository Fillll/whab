/* Web-owned browser preference; run before CSS to avoid an expanded first paint. */
(() => {
  const key = 'whab.sidebar';
  const root = document.documentElement;
  const compact = window.matchMedia('(max-width: 760px)');
  const valid = value => value === 'collapsed' || value === 'expanded';
  let preference = null;
  try {const value = window.localStorage.getItem(key); if (valid(value)) preference = value;} catch { /* Storage is optional. */ }
  function sync() {
    const expanded = (preference || (compact.matches ? 'collapsed' : 'expanded')) === 'expanded';
    root.dataset.sidebar = expanded ? 'expanded' : 'collapsed';
    for (const button of document.querySelectorAll('[data-sidebar-toggle]')) {
      button.hidden = false;
      button.setAttribute('aria-expanded', String(expanded));
      const label = expanded ? 'Collapse sidebar' : 'Expand sidebar';
      button.setAttribute('aria-label', label);
      button.title = label;
    }
  }
  sync();
  document.addEventListener('DOMContentLoaded', sync);
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-sidebar-toggle]')) return;
    preference = root.dataset.sidebar === 'expanded' ? 'collapsed' : 'expanded';
    sync();
    try {window.localStorage.setItem(key, preference);} catch { /* Keep this page usable without persistence. */ }
  });
  compact.addEventListener('change', sync);
  window.addEventListener('storage', event => {
    if (event.key !== key || event.newValue !== null && !valid(event.newValue)) return;
    preference = event.newValue;
    sync();
  });
})();
