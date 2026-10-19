/* Archive bytes stay in this page; the host parses and validates every import. */
const budgetUrl = location.pathname.split('/').slice(0, 3).join('/');
const apiPrefix = `${budgetUrl}/api`;
const bound = new WeakSet();
let activeImports = 0;

function bindPanel(panel) {
  const form = panel.querySelector('[data-import-form]');
  const archive = panel.querySelector('[data-import-file]');
  const name = panel.querySelector('[data-import-name]');
  const usd = panel.querySelector('[data-import-usd]');
  const previewButton = panel.querySelector('[data-import-preview-button]');
  const commitButton = panel.querySelector('[data-import-commit]');
  const previewSection = panel.querySelector('[data-import-preview]');
  const summary = panel.querySelector('[data-import-summary]');
  const month = panel.querySelector('[data-import-month]');
  const warnings = panel.querySelector('[data-import-warnings]');
  const reason = panel.querySelector('[data-import-reason]');
  const error = panel.querySelector('[data-import-error]');
  const progress = panel.querySelector('[data-import-progress]');
  let revision = 0;
  let busy = false;
  let preview = null;
  let commitStarted = false;
  let suggestedName = '';

  function setBusy(value) {
    if (busy === value) return;
    busy = value;
    activeImports += value ? 1 : -1;
  }

  function controls() {
    archive.disabled = busy;
    name.disabled = busy || commitStarted;
    usd.disabled = busy;
    previewButton.disabled = busy || !archive.files[0] || !usd.checked || !name.value.trim() || name.value.trim().length > 100;
    commitButton.disabled = busy || preview?.canImport !== true;
  }

  function invalidate() {
    revision += 1;
    preview = null;
    previewSection.hidden = true;
    error.hidden = true;
    controls();
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  async function request(action, file, fingerprint, operation, budgetName) {
    let response;
    try {
      response = await fetch(`${apiPrefix}/plugins/${encodeURIComponent(panel.dataset.fileImport)}/import/new/${action}`, {
        method: 'POST',
        body: file,
        headers: {
          'Content-Type': 'application/zip',
          'X-Import-Currency': 'USD',
          'X-Import-Budget-Name': encodeURIComponent(budgetName),
          'X-Import-Operation': operation,
          ...(fingerprint ? {'X-Import-Fingerprint': fingerprint} : {}),
        },
      });
    } catch {
      throw new Error('The import request could not be completed. Please try again.');
    }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof result?.error === 'string' ? result.error : 'The import request could not be completed.');
    if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('The import response could not be read. Please try again.');
    return result;
  }

  archive.addEventListener('change', () => {
    if (!commitStarted && (!name.value.trim() || name.value === suggestedName)) {
      suggestedName = (archive.files[0]?.name || '').replace(/\.zip$/i, '')
        .replace(/^YNAB Export\s*-\s*/i, '').replace(/\s+as of \d{4}-\d{2}-\d{2}.*$/i, '').trim().slice(0, 100);
      name.value = suggestedName;
    }
    invalidate();
  });
  name.addEventListener('input', invalidate);
  usd.addEventListener('change', invalidate);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    invalidate();
    const file = archive.files[0];
    const budgetName = name.value.trim();
    if (!budgetName || budgetName.length > 100) {
      showError('Choose a budget name of up to 100 characters.');
      return;
    }
    if (!file || !usd.checked) {
      showError('Choose a ZIP export and confirm that it uses US dollars.');
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      showError('Choose a ZIP export no larger than 10 MiB.');
      return;
    }
    const current = revision;
    const operation = crypto.randomUUID();
    setBusy(true);
    progress.textContent = 'Checking the export…';
    progress.hidden = false;
    controls();
    try {
      const result = await request('preview', file, null, operation, budgetName);
      if (current !== revision || archive.files[0] !== file || !usd.checked) return;
      if (!/^[a-f0-9]{64}$/.test(result.fingerprint) ||
          !['accounts', 'categories', 'transactions', 'assignments'].every(key => Number.isSafeInteger(result[key]) && result[key] >= 0) ||
          result.createsNewBudget !== true || typeof result.canImport !== 'boolean' || typeof result.reason !== 'string' ||
          typeof result.budgetStartMonth !== 'string' ||
          !Array.isArray(result.warnings) || !result.warnings.every(warning => typeof warning === 'string')) {
        throw new Error('The import preview could not be read. Please try again.');
      }
      preview = {...result, file, operation, budgetName, revision: current};
      summary.textContent = `${result.accounts} accounts · ${result.categories} categories · ${result.transactions} transactions · ${result.assignments} monthly assignments`;
      month.textContent = `Budget starts in ${result.budgetStartMonth}. Earlier transactions are kept as history.`;
      warnings.replaceChildren();
      for (const warning of result.warnings) {
        const item = document.createElement('li');
        item.textContent = warning;
        warnings.append(item);
      }
      reason.textContent = result.reason;
      previewSection.hidden = false;
    } catch (failure) {
      if (current === revision) showError(failure instanceof Error && failure.message ? failure.message : 'The import request could not be completed.');
    } finally {
      setBusy(false);
      progress.hidden = true;
      controls();
    }
  });

  commitButton.addEventListener('click', async () => {
    if (busy || !preview?.canImport) return;
    const selected = preview;
    if (selected.revision !== revision || archive.files[0] !== selected.file || !usd.checked || name.value.trim() !== selected.budgetName) {
      invalidate();
      return;
    }
    setBusy(true);
    error.hidden = true;
    commitStarted = true;
    progress.textContent = 'Creating your imported budget…';
    progress.hidden = false;
    controls();
    try {
      const result = await request('commit', selected.file, selected.fingerprint, selected.operation, selected.budgetName);
      const target = new URL(result.url, location.origin);
      if (target.origin !== location.origin || typeof result.id !== 'string' ||
          !/^[a-f0-9-]{36}$/.test(result.id) || !/^\/budgets\/[a-z0-9]+(?:-[a-z0-9]+)*\/accounts\/current-month\/usd$/.test(target.pathname) ||
          target.search || target.hash) {
        throw new Error('The imported budget could not be opened. Retry to open the same budget.');
      }
      location.assign(target.href);
    } catch (failure) {
      showError(failure instanceof Error && failure.message ? failure.message : 'The import request could not be completed. Retry to open the same budget.');
      setBusy(false);
      progress.hidden = true;
      controls();
    }
  });
  controls();
}

function bind() {
  for (const panel of document.querySelectorAll('[data-file-import]')) {
    if (bound.has(panel)) continue;
    bound.add(panel);
    bindPanel(panel);
  }
}
bind();
document.addEventListener('whab:page-ready', bind);
document.addEventListener('whab:before-navigate', event => {
  if (activeImports) event.preventDefault();
});
