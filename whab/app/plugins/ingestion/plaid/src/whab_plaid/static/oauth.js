/* Restore a Link continuation to the budget that started it in this browser tab. */
(() => {
  const keys = ['whab-link-token', 'whab-link-item', 'whab-link-budget', 'whab-link-return-url', 'whab-link-redirect-uri'];
  try {
    const budget = sessionStorage.getItem('whab-link-budget');
    const destination = sessionStorage.getItem('whab-link-return-url');
    const route = /^\/budgets\/(home|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/accounts\/(current-month|[0-9]{4}-(?:0[1-9]|1[0-2]))\/[a-z]{3}$/;
    const match = destination?.match(route);
    if (!match || budget !== `/budgets/${match[1]}` ||
        !sessionStorage.getItem('whab-link-token') ||
        !new URL(location.href).searchParams.has('oauth_state_id') || location.hash) {
      throw new Error('Invalid bank connection session.');
    }
    sessionStorage.setItem('whab-link-redirect-uri', location.href);
    location.replace(destination + location.search);
  } catch {
    try { for (const key of keys) sessionStorage.removeItem(key); } catch { /* Storage may be unavailable. */ }
    const status = document.getElementById('oauth-status') || document.querySelector('p');
    if (status) status.textContent = 'The bank connection session expired. Open your budget’s Accounts page and connect again.';
  }
})();
