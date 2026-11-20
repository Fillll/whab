/* Enhance server-rendered monthly reports. The host supplies every value. */

export function initMonthlyInsights({root, navigate}) {
  const report = root.querySelector('[data-monthly-chart]');
  if (!report) return;
  const form = report.querySelector('[data-monthly-insight-filters]');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const url = new URL(form.action);
    url.search = new URLSearchParams(new FormData(form));
    if (!(await navigate(url))) location.assign(url);
  });

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
