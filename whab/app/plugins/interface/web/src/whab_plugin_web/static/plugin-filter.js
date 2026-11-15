/* Filtering changes visibility only; hidden checkboxes keep their saved draft. */
export function initPluginFilter({page = document} = {}) {
  const form = page.querySelector('[data-plugin-selection]');
  const category = page.querySelector('[data-plugin-category-filter]');
  const search = page.querySelector('[data-plugin-search]');
  if (!form || !category) return null;
  function filter() {
    const query = (search?.value || '').trim().toLocaleLowerCase();
    let visible = 0;
    for (const group of form.querySelectorAll('[data-plugin-category]')) {
      let matches = 0;
      for (const row of group.querySelectorAll('[data-plugin-name]')) {
        row.hidden = !row.dataset.pluginName.toLocaleLowerCase().includes(query);
        if (!row.hidden) matches += 1;
      }
      group.hidden = (category.value !== 'all' && group.dataset.pluginCategory !== category.value) || (Boolean(query) && matches === 0);
      if (!group.hidden) visible += matches;
    }
    form.querySelector('[data-plugin-category-empty]').hidden = visible > 0 || !query && category.value === 'all';
  }
  category.addEventListener('change', filter);
  search?.addEventListener('input', filter);
  return filter;
}
