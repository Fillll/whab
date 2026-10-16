/* Load before the stylesheet so the saved appearance is used on first paint. */
(() => {
  const key = 'whab.theme';
  const colors = { light: '#FAF8F2', dark: '#191715' };
  const valid = value => value === 'light' || value === 'dark';
  let saved;
  try { saved = window.localStorage.getItem(key); } catch { /* Storage is optional. */ }
  const initial = valid(saved) ? saved : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');

  function syncControls() {
    for (const input of document.querySelectorAll('[data-appearance-choice]')) {
      input.checked = input.value === document.documentElement.dataset.theme;
    }
  }

  function apply(theme, persist = false) {
    if (!valid(theme)) return;
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = colors[theme];
    syncControls();
    if (persist) {
      let stored = true;
      try { window.localStorage.setItem(key, theme); } catch { stored = false; }
      for (const status of document.querySelectorAll('[data-appearance-status]')) {
        status.textContent = stored
          ? `${theme === 'light' ? 'Light' : 'Dark'} appearance saved for this browser.`
          : 'Browser storage is unavailable. This appearance applies to the current page.';
      }
    }
  }

  apply(initial);
  document.addEventListener('DOMContentLoaded', syncControls);
  document.addEventListener('change', event => {
    if (event.target.matches('[data-appearance-choice]')) apply(event.target.value, true);
  });
  window.addEventListener('storage', event => {
    if (event.key === key && valid(event.newValue)) apply(event.newValue);
  });
})();
