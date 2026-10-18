/* Local links connect a summary with its existing table total, without changing money. */
export function initBudgetSourceLinks() {
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('[data-budget-source-link]');
    if (!link) return;
    const planner = link.closest('.budget-planner');
    const total = document.getElementById(link.dataset.budgetSourceLink);
    const overview = document.getElementById(link.dataset.budgetSourceOverview);
    if (!planner || !total || !overview || !planner.contains(total) || !planner.contains(overview)) return;
    event.preventDefault();
    for (const cell of planner.querySelectorAll('.budget-source-highlight')) cell.classList.remove('budget-source-highlight');
    overview.classList.add('budget-source-highlight');
    total.classList.add('budget-source-highlight');
    total.focus({preventScroll: true});
    total.scrollIntoView({block: 'center', inline: 'nearest',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  });
}
