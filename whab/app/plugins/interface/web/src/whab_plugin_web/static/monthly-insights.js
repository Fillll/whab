/* Enhance server-rendered monthly reports. The host supplies every value. */

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
