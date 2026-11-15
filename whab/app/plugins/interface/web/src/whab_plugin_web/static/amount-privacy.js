/* Display-only amount privacy. Load before CSS; never rewrite financial text or input values. */
(() => {
  const key = 'whab.amounts';
  const root = document.documentElement;
  const targets = '[data-private-amount], [data-private-content]';
  const numericAttributes = new Set(['aria-valuenow', 'aria-valuemin', 'aria-valuemax']);
  const privateAttributes = ['title', 'aria-label', 'aria-valuetext', ...numericAttributes];
  const states = new Map(), attributes = new Map();
  let hidden = false;
  try {hidden = window.localStorage.getItem(key) === 'hidden';} catch { /* Keep a usable page without storage. */ }
  writeAttribute(root, 'data-amounts', hidden ? 'hidden' : 'shown');
  const observer = new MutationObserver(changes => {
    // Remember genuine UI updates while masked; our own writes are not observed.
    for (const change of changes) {
      if (change.type !== 'attributes') continue;
      const saved = attributes.get(change.target)?.get(change.attributeName);
      if (saved) saved.original = change.target.getAttribute(change.attributeName);
    }
    synchronize();
  });
  function observe() {
    observer.observe(root, {subtree: true, childList: true, attributes: true,
      attributeFilter: [...privateAttributes, 'hidden', 'style', 'data-private-amount', 'data-private-content', 'data-private-currency', 'data-private-amount-attributes', 'data-private-amount-label']});
  }
  function writeAttribute(element, name, value) {
    if (element.getAttribute(name) === value) return;
    if (value === null) element.removeAttribute(name);
    else element.setAttribute(name, value);
  }
  function maskAttributes(element, names) {
    let saved = attributes.get(element);
    if (!saved) {saved = new Map(); attributes.set(element, saved);}
    for (const name of names) {
      if (!privateAttributes.includes(name)) continue;
      if (!saved.has(name)) saved.set(name, {original: element.getAttribute(name)});
      const value = saved.get(name).original;
      if (value === null) continue;
      writeAttribute(element, name, numericAttributes.has(name) ? null : element.dataset.privateAmountLabel || 'Amount hidden');
    }
  }
  function ownedAttributes(element) {
    return new Set([
      ...(element.matches(targets) ? privateAttributes : []),
      ...(element.getAttribute('data-private-amount-attributes') || '').split(/\s+/),
    ]);
  }
  function maskText(element) {
    if (element.hasAttribute('data-private-content')) return element.getAttribute('data-private-content') || 'XX';
    const explicit = element.getAttribute('data-private-amount');
    if (explicit) return explicit;
    // Currency is presentation metadata, never inferred from the amount or its length.
    // Draft currency choices apply to editors, not a committed balance beside them.
    const choice = element.matches('input, textarea, select') ? element.closest('form')?.querySelector('[name="currency"]') : null;
    const currency = choice?.value || element.closest('[data-private-currency]')?.dataset.privateCurrency;
    return currency === 'USD' ? '$X,XXX' : /^[A-Z]{3}$/.test(currency || '') ? `${currency} X,XXX` : 'X,XXX';
  }
  function createMask(element) {
    const svg = element.namespaceURI === 'http://www.w3.org/2000/svg';
    const input = element.matches('input, textarea, select');
    const inline = ['strong', 'b', 'em', 'i', 'small', 'code', 'samp'].includes(element.localName) ? element.localName : 'span';
    const mask = svg ? document.createElementNS(element.namespaceURI, 'text') : document.createElement(input ? 'button' : inline);
    mask.setAttribute('data-private-mask', '');
    mask.classList.add('amount-privacy-mask');
    if (svg) {
      for (const name of ['x', 'y', 'dx', 'dy', 'transform', 'text-anchor', 'dominant-baseline', 'class']) {
        if (element.hasAttribute(name)) mask.setAttribute(name, element.getAttribute(name));
      }
      mask.classList.add('amount-privacy-mask');
    } else if (input) {
      mask.type = 'button';
      mask.classList.add('amount-privacy-reveal');
      mask.setAttribute('aria-label', 'Show amounts to edit');
      mask.title = 'Show amounts to edit';
    } else if (element.hasAttribute('data-private-content')) mask.classList.add('amount-privacy-content-mask');
    element.after(mask);
    const state = {element, mask, input};
    states.set(element, state);
    return state;
  }
  function synchronize() {
    observer.disconnect();
    // An application fragment may remove a marker without removing its element.
    for (const [element, saved] of attributes) {
      const owned = hidden && element.isConnected ? ownedAttributes(element) : new Set();
      for (const [name, value] of saved) if (!owned.has(name)) {
        writeAttribute(element, name, value.original); saved.delete(name);
      }
      if (!saved.size || !element.isConnected) attributes.delete(element);
    }
    for (const [element, state] of states) {
      if (!element.isConnected || !element.matches(targets) || element.parentElement?.closest(targets)) {
        state.mask.remove(); states.delete(element);
      }
    }
    for (const element of document.querySelectorAll(targets)) {
      if (element.parentElement?.closest(targets)) continue;
      const state = states.get(element) || createMask(element);
      if (state.mask.previousElementSibling !== element) element.after(state.mask);
      const text = maskText(element);
      if (state.mask.textContent !== text) state.mask.textContent = text;
      writeAttribute(state.mask, 'hidden', !hidden || element.hasAttribute('hidden') || element.style.display === 'none' ? '' : null);
      if (hidden) maskAttributes(element, privateAttributes);
    }
    if (hidden) {
      for (const element of document.querySelectorAll('[data-private-amount-attributes]')) {
        maskAttributes(element, element.dataset.privateAmountAttributes.split(/\s+/));
      }
    }
    for (const button of document.querySelectorAll('[data-amount-privacy-toggle]')) {
      writeAttribute(button, 'hidden', null);
      writeAttribute(button, 'aria-pressed', String(hidden));
      writeAttribute(button, 'aria-label', hidden ? 'Show amounts' : 'Hide amounts');
      writeAttribute(button, 'title', hidden ? 'Show amounts' : 'Hide amounts');
    }
    observe();
  }
  function apply(value, persist = false) {
    hidden = value;
    writeAttribute(root, 'data-amounts', hidden ? 'hidden' : 'shown');
    synchronize();
    let stored = true;
    if (persist) {
      try {window.localStorage.setItem(key, hidden ? 'hidden' : 'shown');} catch {stored = false;}
      for (const status of document.querySelectorAll('[data-amount-privacy-status]')) {
        status.textContent = stored ? (hidden ? 'Amounts hidden.' : 'Amounts shown.') :
          `${hidden ? 'Amounts hidden' : 'Amounts shown'} for this page. Browser storage is unavailable.`;
      }
    }
  }
  // Hiding a focused money editor must not turn its unfinished draft into a save.
  // Pointer focus otherwise leaves the editor before the toggle click is handled.
  document.addEventListener('pointerdown', event => {
    if (event.target.closest('[data-amount-privacy-toggle], button[data-private-mask]')) event.preventDefault();
  }, true);
  for (const type of ['blur', 'focusout', 'change']) document.addEventListener(type, event => {
    if (hidden && event.target.matches('input, textarea, select') && event.target.closest(targets)) event.stopImmediatePropagation();
  }, true);
  document.addEventListener('click', event => {
    const toggle = event.target.closest('[data-amount-privacy-toggle]');
    const mask = event.target.closest('button[data-private-mask]');
    if (!toggle && !mask) return;
    event.preventDefault(); event.stopPropagation();
    if (toggle) {apply(!hidden, true); toggle.focus({preventScroll: true});}
    else {
      const state = [...states.values()].find(value => value.mask === mask);
      if (!state) return;
      apply(false, true);
      if (state.element.hasAttribute('data-money-rounded')) {
        const activation = new Event('money-display-activate', {bubbles: true, cancelable: true});
        if (state.element.dispatchEvent(activation)) state.element.dispatchEvent(new Event('money-edit', {bubbles: true}));
      }
      state.element.focus({preventScroll: true});
    }
  }, true);
  document.addEventListener('DOMContentLoaded', synchronize);
  document.addEventListener('change', event => {
    if (event.target.matches('[name="currency"]')) synchronize();
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    if (event.newValue !== null && !['shown', 'hidden'].includes(event.newValue)) return;
    apply(event.newValue === 'hidden');
  });
  synchronize();
})();
