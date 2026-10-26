/* Presentation only. The host resolves dependencies and prepares exact packages. */
export const categories = {interface: 'Interfaces', ingestion: 'Ingestion', insight: 'Insights', storage: 'Storage', automation: 'Automation', extension: 'Extensions'};

export function initMarketplace({api, beforeChange = async () => {}, onInstalled = () => {}}) {
  const dialog = document.querySelector('[data-marketplace]');
  if (!dialog) return;
  const list = dialog.querySelector('[data-marketplace-list]');
  const error = dialog.querySelector('[data-marketplace-error]');
  const status = dialog.querySelector('[data-marketplace-status]');
  const review = dialog.querySelector('[data-marketplace-review]');
  const packages = dialog.querySelector('[data-marketplace-packages]');
  const install = dialog.querySelector('[data-marketplace-install]');
  const cancel = dialog.querySelector('[data-marketplace-cancel]');
  let token = null, busy = false, generation = 0;
  const text = (tag, value, className = '') => {
    const node = document.createElement(tag);
    node.textContent = value;
    node.className = className;
    return node;
  };
  const reset = () => {token = null; review.hidden = true; packages.replaceChildren();};
  const task = async action => {
    if (busy) return;
    busy = true;
    error.hidden = true;
    dialog.setAttribute('aria-busy', 'true');
    for (const button of dialog.querySelectorAll('[data-marketplace-action], [data-marketplace-install]')) button.disabled = true;
    try {await action();} catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      reset();
    } finally {
      busy = false;
      dialog.removeAttribute('aria-busy');
      for (const button of dialog.querySelectorAll('[data-marketplace-action]')) button.disabled = button.dataset.unavailable === 'true';
      install.disabled = !token;
      status.textContent = '';
    }
  };
  function render(rows) {
    list.replaceChildren();
    for (const [category, label] of Object.entries(categories)) {
      const section = text('section', '', 'plugin-category-group');
      section.append(text('h4', label));
      const plugins = rows.filter(row => row.category === category);
      for (const row of plugins) {
        const element = text('div', '', 'marketplace-row');
        const copy = text('div', '');
        copy.append(text('strong', row.display_name), text('small', `Version ${row.version}`, 'plugin-meta'));
        if (row.dependencies.length) copy.append(text('small', `Requires: ${row.dependencies.map(dependency => `${dependency.id} ${dependency.version}`).join(', ')}`, 'plugin-meta'));
        const button = text('button', row.installed_version ? `Installed · ${row.installed_version}` : row.compatible ? 'Review installation' : 'Incompatible', 'button secondary');
        button.type = 'button';
        button.dataset.marketplaceAction = row.id;
        button.dataset.unavailable = String(Boolean(row.installed_version || !row.compatible || dialog.dataset.workspace === 'demo'));
        button.disabled = button.dataset.unavailable === 'true';
        button.addEventListener('click', () => task(async () => {
          reset();
          await beforeChange();
          status.textContent = 'Preparing plugins and checking dependencies…';
          const current = generation;
          const result = await api('/plugins/marketplace/review', {pluginId: row.id});
          if (current !== generation) return;
          token = result.token;
          for (const plugin of result.plugins) packages.append(text('li', `${plugin.display_name} ${plugin.version}${plugin.installed ? ' · already installed' : ''}`));
          const pluginNames = new Set(result.plugins.map(plugin => plugin.id.replaceAll('-', '_')));
          for (const item of result.packages.filter(item => !pluginNames.has(item.name.replace(/^whab[-_]plugin[-_]/, '').replaceAll('-', '_')))) packages.append(text('li', `${item.name} ${item.version}`));
          review.hidden = false;
          install.disabled = false;
          install.focus();
        }));
        element.append(copy, button);
        section.append(element);
      }
      if (!plugins.length) section.append(text('p', 'No plugins available', 'settings-notice plugin-category-empty'));
      list.append(section);
    }
  }
  dialog.addEventListener('whab:dialog-open', () => task(async () => {
    reset();
    const current = ++generation;
    status.textContent = 'Loading marketplace…';
    const result = await api('/plugins/marketplace');
    if (current === generation) render(result.plugins);
  }));
  dialog.addEventListener('close', () => {generation++; reset();});
  cancel.addEventListener('click', () => {generation++; reset();});
  install.addEventListener('click', () => task(async () => {
    if (!token) return;
    await beforeChange();
    const reviewed = token;
    reset();
    status.textContent = 'Installing reviewed packages…';
    await api('/plugins/marketplace/install', {token: reviewed});
    onInstalled();
  }));
}
