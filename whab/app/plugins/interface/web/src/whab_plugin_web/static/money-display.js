/* Inline money keeps its exact draft in the input; only the idle label is rounded. */
(() => {
  const selector = 'input[data-money-rounded]';
  const states = new Map();
  const integers = new Intl.NumberFormat('en-US', {maximumFractionDigits: 0});
  function rounded(value) {
    const match = value.trim().match(/^([+-]?)(\d+)(?:\.(\d+))?$/);
    if (!match) return value.trim() ? 'Invalid amount' : '—';
    const magnitude = BigInt(match[2]) + (Number(match[3]?.[0] || '0') >= 5 ? 1n : 0n);
    return integers.format(match[1] === '-' && magnitude ? -magnitude : magnitude);
  }
  function setAttribute(node, name, value) {
    if (value === null) {if (node.hasAttribute(name)) node.removeAttribute(name);}
    else if (node.getAttribute(name) !== value) node.setAttribute(name, value);
  }
  function update(input, state) {
    const editing = document.activeElement === input || input.dataset.gridEditing === 'true';
    setAttribute(input, 'data-money-idle', editing ? null : '');
    setAttribute(state.display, 'hidden', editing || input.hidden ? '' : null);
    setAttribute(state.display, 'disabled', input.disabled ? '' : null);
    const value = rounded(input.value);
    if (state.display.textContent !== value) state.display.textContent = value;
    const label = input.getAttribute('aria-label') || 'Amount';
    setAttribute(state.display, 'aria-label', `${label}: ${value}${input.disabled ? '' : '. Edit exact amount'}`);
    setAttribute(state.display, 'title', input.title || null);
  }
  function edit(input) {
    if (input.disabled || document.documentElement.dataset.amounts === 'hidden') return;
    if (!states.has(input)) synchronize();
    setAttribute(input, 'data-money-idle', null);
    setAttribute(states.get(input).display, 'hidden', '');
    input.focus({preventScroll: true});
    input.select();
  }
  const observer = new MutationObserver(synchronize);
  function synchronize() {
    observer.disconnect();
    for (const [input, state] of states) {
      if (!input.isConnected || !input.matches(selector)) {state.display.remove(); states.delete(input);}
    }
    for (const input of document.querySelectorAll(selector)) {
      let state = states.get(input);
      if (!state) {
        const display = document.createElement('button');
        display.type = 'button'; display.className = 'amount-display'; display.setAttribute('data-money-display', '');
        display.addEventListener('click', () => {
          const activation = new Event('money-display-activate', {bubbles: true, cancelable: true});
          if (input.dispatchEvent(activation)) edit(input);
        });
        state = {display}; states.set(input, state);
      }
      // Privacy owns the immediate sibling too. Do not move already attached labels.
      if (state.display.parentElement !== input.parentElement) input.after(state.display);
      update(input, state);
    }
    observer.observe(document.documentElement, {subtree: true, childList: true, attributes: true,
      attributeFilter: ['value', 'disabled', 'hidden', 'data-money-rounded', 'data-minor-units', 'data-money-idle', 'data-amounts', 'aria-label', 'title']});
  }
  for (const type of ['input', 'change', 'focusin', 'focusout', 'money-refresh']) {
    document.addEventListener(type, event => {
      if (!event.target.matches(selector)) return;
      // The native focusout runs before activeElement has settled in some browsers.
      if (type === 'focusout') queueMicrotask(synchronize);
      else synchronize();
    }, true);
  }
  document.addEventListener('invalid', event => {if (event.target.matches(selector)) edit(event.target);}, true);
  document.addEventListener('money-edit', event => {if (event.target.matches(selector)) edit(event.target);}, true);
  document.addEventListener('DOMContentLoaded', synchronize);
  synchronize();
})();
