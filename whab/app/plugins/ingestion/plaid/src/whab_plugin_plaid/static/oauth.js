/* Restore a Link continuation to the budget that started it in this browser tab. */
(() => {
  const keys = ['whab-link-token', 'whab-link-item', 'whab-link-budget', 'whab-link-return-url', 'whab-link-redirect-uri', 'whab-link-context'];
  let preserve = false;
  try {
    const budget = sessionStorage.getItem('whab-link-budget');
    const destination = sessionStorage.getItem('whab-link-return-url');
    const context = JSON.parse(sessionStorage.getItem('whab-link-context'));
    // A rejected callback must not erase an existing durable or uncertain operation.
    const scoped = context && context.formatVersion === 1 && context.provider === 'plaid' &&
      ['sandbox', 'production'].includes(context.environment) &&
      typeof budget === 'string' && /^\/budgets\/[a-z0-9]+(?:-[a-z0-9]+)*$/.test(budget) &&
      budget !== '/budgets/demo' && budget.length <= 209;
    preserve = scoped && ((context.legacy === true && context.phase === 'submitted') ||
      (context.legacy === false && typeof context.flow_id === 'string' && /^[a-f0-9]{32}$/.test(context.flow_id) &&
        Number.isSafeInteger(context.generation) && context.generation > 0 &&
        ['link', 'update'].includes(context.kind) && Number.isSafeInteger(context.deadline_ms) && context.deadline_ms > 0));
    const route = /^\/budgets\/([a-z0-9]+(?:-[a-z0-9]+)*)\/accounts\/(current-month|[0-9]{4}-(?:0[1-9]|1[0-2]))\/[a-z]{3}$/;
    const match = destination?.match(route);
    if (!context || context.formatVersion !== 1 || context.provider !== 'plaid' ||
        !['sandbox', 'production'].includes(context.environment) || typeof context.legacy !== 'boolean' ||
        context.phase !== 'ready' ||
        (!context.legacy && (typeof context.flow_id !== 'string' || !/^[a-f0-9]{32}$/.test(context.flow_id) ||
          !Number.isSafeInteger(context.generation) || context.generation < 1 ||
          !['link', 'update'].includes(context.kind) || context.stage !== 'ready' ||
          !Number.isSafeInteger(context.deadline_ms) || context.deadline_ms <= Date.now())) ||
        !match || match[1] === 'demo' || match[1].length > 200 || budget !== `/budgets/${match[1]}` ||
        !sessionStorage.getItem('whab-link-token') ||
        !new URL(location.href).searchParams.has('oauth_state_id') || location.hash) {
      throw new Error('Invalid bank connection session.');
    }
    sessionStorage.setItem('whab-link-redirect-uri', location.href);
    location.replace(destination + location.search);
  } catch {
    if (!preserve) {
      try { for (const key of keys) sessionStorage.removeItem(key); } catch { /* Storage may be unavailable. */ }
    }
    const status = document.getElementById('oauth-status') || document.querySelector('p');
    if (status) status.textContent = preserve
      ? 'This bank connection could not resume. Open its original budget’s Accounts page and check its status. Do not connect again.'
      : 'The bank connection session expired. Open your budget’s Accounts page and connect again.';
  }
})();
