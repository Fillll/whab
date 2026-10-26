/* One native modal; settings pages retain their DOM and plugin lifecycles. */
export function isSettingsPageOpen(page) {
  return page.dataset.settingsPage !== undefined ? !page.hidden && Boolean(page.closest('dialog')?.open) : page.open;
}

export function initSettingsNavigation({document: page = document, beforeOpen = () => {}} = {}) {
  const window = page.getElementById('settings-dialog');
  const pages = [...window.querySelectorAll('[data-settings-page]')];
  const home = page.getElementById('settings-home');
  const busySelector = '[data-budget-new][data-busy], [data-budget-open][data-busy], [data-budget-switch][data-busy], [data-account-classification][data-busy]';
  let current = home, history = [], origin = null;
  const busy = () => Boolean(window.querySelector(busySelector));
  function leave() {
    current.hidden = true;
    // Existing plugin controllers cancel training and stop polling on close.
    current.dispatchEvent(new Event('close'));
  }
  function show(target, {focus = null, scroll = 0} = {}) {
    current = target;
    for (const item of pages) item.hidden = item !== target;
    window.setAttribute('aria-label', target.getAttribute('aria-label'));
    current.dispatchEvent(new Event('whab:settings-enter'));
    window.scrollTop = scroll;
    const heading = current.querySelector('h2');
    if (heading) heading.tabIndex = -1;
    (focus || heading)?.focus({preventScroll: true});
  }
  function parents(target) {
    const path = [];
    for (let parent = page.getElementById(target.dataset.settingsParent); parent;
      parent = page.getElementById(parent.dataset.settingsParent)) path.unshift({target: parent});
    return path;
  }
  function open(id, opener = null) {
    const target = id === window.id ? home : page.getElementById(id);
    if (!pages.includes(target) || busy()) return false;
    if (window.open && current === target) return true;
    beforeOpen();
    if (window.open) {
      history.push({target: current, focus: opener, scroll: window.scrollTop});
      leave();
    } else {
      const previous = opener?.closest('dialog');
      if (previous && previous !== window) {
        if (previous.querySelector(busySelector)) return false;
        origin = {window: previous, opener};
        previous.close();
      }
      history = parents(target);
      for (const item of pages) item.hidden = item !== target;
      window.showModal();
    }
    show(target);
    return true;
  }
  function back() {
    if (busy()) return;
    const previous = history.pop();
    if (!previous) return;
    leave();
    show(previous.target, previous);
  }
  page.addEventListener('click', event => {
    const opener = event.target.closest('[data-dialog]');
    if (opener && !opener.disabled && (opener.dataset.dialog === window.id || pages.some(item => item.id === opener.dataset.dialog))) {
      event.preventDefault();
      open(opener.dataset.dialog, opener);
    }
    const backButton = event.target.closest('[data-settings-back]');
    if (backButton && !backButton.disabled && window.contains(backButton)) {
      event.preventDefault();
      back();
    }
  });
  window.addEventListener('cancel', event => {if (busy()) event.preventDefault();});
  window.addEventListener('close', () => {
    if (window.open) return;
    leave();
    history = [];
    if (origin) {
      const previous = origin; origin = null;
      previous.window.showModal();
      previous.opener.focus({preventScroll: true});
    }
  });
  return {open, back};
}
