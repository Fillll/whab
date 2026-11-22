/* Enhance server-rendered monthly reports. The host supplies every value. */

const contributorTrigger = '[data-monthly-contributor-trigger]';
let dismissPreview;

function initContributorPreview(plot) {
  const preview = plot.querySelector('[data-monthly-contributor-preview]');
  if (!preview) return;
  const triggers = [...plot.querySelectorAll(contributorTrigger)];
  if (!triggers.length) return;

  const document = plot.ownerDocument;
  const window = document.defaultView;
  const viewport = document.documentElement;
  const scroll = plot.querySelector('.monthly-chart-scroll');
  const context = preview.querySelector('[data-monthly-contributor-context]');
  const table = preview.querySelector('tbody');
  let current = triggers[0];
  let active, pinned, leaveTimer, lifetime, privacy;
  current.setAttribute('tabindex', '0');
  for (const trigger of triggers) trigger.querySelector('title')?.remove();

  function close() {
    window.clearTimeout(leaveTimer);
    if (!active) return;
    active.setAttribute('aria-expanded', 'false');
    active.removeAttribute('aria-describedby');
    if (preview.hidePopover && preview.matches(':popover-open')) preview.hidePopover();
    preview.hidden = true;
    table.replaceChildren();
    context.textContent = '';
    lifetime.abort();
    privacy.disconnect();
    active = null;
    pinned = false;
    dismissPreview = undefined;
  }

  function position() {
    const anchor = active.getBoundingClientRect();
    const chart = scroll.getBoundingClientRect();
    const width = viewport.clientWidth;
    const height = viewport.clientHeight;
    if (anchor.right < Math.max(0, chart.left) || anchor.left > Math.min(width, chart.right)
        || anchor.bottom < 0 || anchor.top > height) { close(); return; }
    const panel = preview.getBoundingClientRect();
    const inset = 8;
    const below = anchor.bottom + inset;
    const above = anchor.top - panel.height - inset;
    preview.style.left = `${Math.max(inset, Math.min(anchor.left, width - panel.width - inset))}px`;
    preview.style.top = `${Math.max(inset, Math.min(below + panel.height <= height - inset ? below : above, height - panel.height - inset))}px`;
  }

  function show(trigger) {
    if (viewport.dataset.amounts === 'hidden') return;
    window.clearTimeout(leaveTimer);
    if (active === trigger) return;
    dismissPreview?.();
    active = trigger;
    dismissPreview = close;
    context.textContent = trigger.getAttribute('aria-label');
    for (const values of JSON.parse(trigger.dataset.monthlyContributorTrigger)) {
      const row = table.insertRow();
      row.setAttribute('data-monthly-contributor-row', '');
      values.forEach((value, index) => {
        const cell = row.insertCell();
        if (index === 2) cell.setAttribute('data-monthly-contributor-amount', '');
        cell.textContent = value;
      });
    }
    preview.hidden = false;
    preview.showPopover?.();
    trigger.setAttribute('aria-expanded', 'true');
    trigger.setAttribute('aria-describedby', preview.id);
    lifetime = new AbortController();
    const signal = lifetime.signal;
    document.addEventListener('pointerdown', event => {
      if (!active.contains(event.target) && !preview.contains(event.target)) close();
    }, {capture: true, signal});
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
    }, {signal});
    document.addEventListener('scroll', position, {capture: true, signal});
    window.addEventListener('resize', position, {signal});
    privacy = new MutationObserver(() => { if (viewport.dataset.amounts === 'hidden') close(); });
    privacy.observe(viewport, {attributes: true, attributeFilter: ['data-amounts']});
    position();
  }

  function toggle(trigger) {
    if (active === trigger && pinned) close();
    else {
      show(trigger);
      pinned = active === trigger;
    }
  }

  plot.addEventListener('pointerover', event => {
    if (preview.contains(event.target)) window.clearTimeout(leaveTimer);
    const trigger = event.target.closest(contributorTrigger);
    if (trigger && !pinned && event.pointerType !== 'touch') show(trigger);
  });
  plot.addEventListener('pointerout', event => {
    if (!pinned && active && document.activeElement !== active
        && (active.contains(event.target) || preview.contains(event.target))
        && !active.contains(event.relatedTarget) && !preview.contains(event.relatedTarget)) {
      window.clearTimeout(leaveTimer);
      leaveTimer = window.setTimeout(close, 180);
    }
  });
  plot.addEventListener('focusin', event => {
    const trigger = event.target.closest(contributorTrigger);
    if (!trigger) return;
    current.setAttribute('tabindex', '-1');
    current = trigger;
    current.setAttribute('tabindex', '0');
    show(trigger);
  });
  plot.addEventListener('focusout', event => { if (event.target === active) close(); });
  plot.addEventListener('click', event => {
    const trigger = event.target.closest(contributorTrigger);
    if (trigger) toggle(trigger);
  });
  plot.addEventListener('keydown', event => {
    const trigger = event.target.closest(contributorTrigger);
    if (!trigger) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggle(trigger);
    }
    const step = {ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1}[event.key];
    if (step) {
      event.preventDefault();
      triggers[triggers.indexOf(trigger) + step]?.focus();
    }
  });
}

function initMonthlyFilter(picker) {
  const summary = picker.querySelector('summary');
  const label = picker.querySelector('[data-monthly-filter-label]');
  const panel = picker.querySelector('[data-monthly-filter-panel]');
  const search = picker.querySelector('[data-monthly-filter-search]');
  const empty = picker.querySelector('[data-monthly-filter-empty]');
  const options = [...picker.querySelectorAll('[data-monthly-filter-option]')];
  const checkboxes = options.map(option => option.querySelector('input'));
  const searchKey = text => text.toLocaleLowerCase().normalize('NFKD').replace(/\p{Mark}/gu, '');
  const names = options.map(option => searchKey(option.textContent));

  function filter() {
    const query = searchKey(search.value.trim());
    options.forEach((option, index) => { option.hidden = !names[index].includes(query); });
    empty.hidden = options.some(option => !option.hidden);
    empty.textContent = query ? 'No matching options.' : 'No options available.';
  }
  function updateLabel() {
    const count = checkboxes.filter(input => input.checked).length;
    label.textContent = count ? `${count} selected` : picker.dataset.allLabel;
  }
  function close() {
    picker.open = false;
    summary.focus();
  }

  picker.setAttribute('data-monthly-filter-enhanced', '');
  picker.querySelector('[data-monthly-filter-tools]').hidden = false;
  picker.addEventListener('toggle', () => {
    if (!picker.open) return;
    search.value = '';
    filter();
    picker.removeAttribute('data-monthly-filter-align-end');
    const viewport = picker.ownerDocument.documentElement.clientWidth;
    picker.toggleAttribute('data-monthly-filter-align-end', panel.getBoundingClientRect().right > viewport - 12);
  });
  picker.addEventListener('change', updateLabel);
  picker.addEventListener('focusout', event => {
    if (!picker.contains(event.relatedTarget)) picker.open = false;
  });
  picker.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'Escape' && picker.open) {
      event.preventDefault();
      close();
    } else if (event.key === 'Enter' && event.target === search) {
      event.preventDefault();
    }
  });
  search.addEventListener('input', filter);
  picker.querySelector('[data-monthly-filter-clear]').addEventListener('click', () => {
    checkboxes.forEach(input => { input.checked = false; });
    updateLabel();
    close();
  });
}

export function initMonthlyInsights({root, navigate}) {
  dismissPreview?.();
  const report = root.querySelector('[data-monthly-chart]');
  if (!report) return;
  const form = report.querySelector('[data-monthly-insight-filters]');
  if (form) {
    const maxFields = Number(form.dataset?.monthlyFilterMaxFields);
    const limitError = maxFields && form.querySelector('[data-monthly-filter-limit-error]');
    for (const picker of report.querySelectorAll('[data-monthly-filter]')) initMonthlyFilter(picker);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const filters = new URLSearchParams(new FormData(form));
      if (limitError) {
        limitError.hidden = filters.size <= maxFields;
        if (!limitError.hidden) return;
      }
      const url = new URL(form.action);
      url.search = filters;
      if (!(await navigate(url))) location.assign(url);
    });
  }

  for (const plot of report.querySelectorAll('.monthly-insight')) {
    initContributorPreview(plot);
    const controls = [...plot.querySelectorAll('[data-chart-emphasis]')];
    const segments = plot.querySelectorAll('[data-chart-series]');
    let selected = null;
    for (const button of controls) {
      button.disabled = false;
      button.addEventListener('click', () => {
        const series = button.dataset.chartEmphasis;
        selected = selected === series ? null : series;
        for (const control of controls) {
          control.setAttribute('aria-pressed', String(control.dataset.chartEmphasis === selected));
        }
        for (const segment of segments) {
          segment.toggleAttribute('data-chart-dimmed', selected !== null && segment.dataset.chartSeries !== selected);
        }
      });
    }
  }
}
