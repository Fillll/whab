/* Editable account rows use public ledger commands; visibility is a view preference. */
import {getContext, updateContext} from './client.js';
import {hydrateCurrencyChoices, initAccountLogoPreview} from './account-settings.js';
import {bindLogos} from './logo-images.js';

export function accountChanges(current, committed) {
  return Object.fromEntries(Object.entries(current).filter(([key, value]) => value !== committed[key]));
}

export function initAccountList({root = document, api, updateFields, onDraft = () => {}}) {
  const list = root.querySelector('[data-account-management]');
  if (!list) return {dirty: false, flush: async () => true, dispose() {}};
  const context = {...getContext()}, workspace = list.dataset.workspace;
  const controller = new AbortController(), editors = new Map();
  let disposed = false, refresh = Promise.resolve();
  const listen = (target, event, callback) => target.addEventListener(event, callback, {signal: controller.signal});
  function feedback(form, message = '', failure = false) {
    const status = form.querySelector('[data-account-status]'), error = form.querySelector('.field-error');
    status.hidden = failure || !message; status.textContent = failure ? '' : message;
    error.hidden = !failure; error.textContent = failure ? message : '';
  }
  // Reconcile server totals, grouping and sidebar without replacing live row editors.
  async function sync() {
    const response = await fetch(location.href, {cache: 'no-store', signal: controller.signal, headers: {'X-WHAB-Page-Fragment': '1'}});
    if (!response.ok || response.headers.get('X-WHAB-Page-Fragment') !== '1' || response.url && response.url !== location.href) throw new Error('Saved. Reload to update account totals.');
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const fragment = page.querySelector('[data-page-fragment]');
    const fresh = JSON.parse(fragment?.dataset.pageContext || 'null');
    if (disposed) return;
    if (!fresh || fresh.page !== 'accounts' || ['budgetId', 'apiPrefix', 'workspace', 'uiGeneration', 'currency'].some(key => context[key] && fresh[key] !== context[key])) throw new Error('Saved. Reload to update account totals.');
    const sidebar = document.querySelector('.sidebar-accounts'), template = page.querySelector('template[data-account-list]');
    if (sidebar && template) {
      const top = sidebar.scrollTop; sidebar.replaceChildren(template.content.cloneNode(true)); sidebar.scrollTop = top;
    }
    const totals = list.querySelector('[data-account-totals]'), nextTotals = page.querySelector('[data-account-totals]');
    if (totals && nextTotals) totals.replaceChildren(...nextTotals.childNodes);
    for (const group of list.querySelectorAll('[data-account-group]')) {
      const nextGroup = [...page.querySelectorAll('[data-account-group]')].find(value => value.dataset.accountGroup === group.dataset.accountGroup);
      if (!nextGroup) continue;
      for (const freshForm of nextGroup.querySelectorAll('[data-inline-account]')) {
        const form = [...editors.keys()].find(value => value.dataset.inlineAccount === freshForm.dataset.inlineAccount);
        if (!form) continue;
        const editor = editors.get(form);
        if (!editor.dirty && form.closest('[data-account-group]') !== group) {
          const active = form.contains(document.activeElement) && document.activeElement;
          group.querySelector('.account-list').append(form); active?.focus({preventScroll: true});
        }
        for (const selector of ['[data-account-facts]', '[data-account-balance]']) form.querySelector(selector).replaceChildren(...freshForm.querySelector(selector).childNodes);
        const logoButton = form.querySelector('.account-logo-toggle'), freshLogo = freshForm.querySelector('.account-logo-toggle');
        logoButton.replaceChildren(...freshLogo.childNodes);
        logoButton.setAttribute('aria-label', freshLogo.getAttribute('aria-label'));
        const currencyButton = form.querySelector('[data-account-more]:not(.account-logo-toggle)');
        const freshCurrency = freshForm.querySelector('[data-account-more]:not(.account-logo-toggle)');
        currencyButton.textContent = freshCurrency.textContent;
        currencyButton.setAttribute('aria-label', freshCurrency.getAttribute('aria-label'));
        form.querySelector('[data-sidebar-visible]').setAttribute('aria-label', freshForm.querySelector('[data-sidebar-visible]').getAttribute('aria-label'));
        for (const name of ['name', 'onBudget', 'type', 'subtype', 'currency', 'bankWebsite']) {
          const label = freshForm.elements[name]?.getAttribute('aria-label');
          if (label) form.elements[name]?.setAttribute('aria-label', label);
        }
        const register = form.querySelector('[data-account-register]');
        register.href = freshForm.querySelector('[data-account-register]').href;
      }
    }
    for (const group of list.querySelectorAll('[data-account-group]')) {
      const count = group.querySelectorAll('[data-inline-account]').length;
      group.querySelector('[data-account-group-count]').textContent = String(count);
      group.querySelector('[data-account-group-empty]').hidden = count > 0;
    }
    updateContext({accountSlugs: fresh.accountSlugs}); bindLogos();
  }
  function synchronize(form) {
    refresh = refresh.catch(() => {}).then(sync).catch(error => {if (!disposed && error.name !== 'AbortError') feedback(form, error.message, true);});
    return refresh;
  }
  for (const form of list.querySelectorAll('[data-inline-account]')) {
    const actions = form.querySelector('[data-account-actions]'), submit = form.querySelector('[type="submit"]');
    const currency = form.elements.currency, website = form.elements.bankWebsite;
    const extra = form.querySelector('[data-account-extra]'), logo = initAccountLogoPreview(form, api);
    const visibility = form.querySelector('[data-sidebar-visible]');
    let visible = visibility.checked, pending = null, loading = null, loaded = false;
    let committed = values();
    function values() {
      return {name: form.elements.name.value, onBudget: form.elements.onBudget.value === 'true',
        type: form.elements.type.value, subtype: form.elements.subtype.value || null,
        ...(loaded ? {currency: currency.value, ...(website ? {bankWebsite: website.value} : {})} : {})};
    }
    function changed() {return Object.keys(accountChanges(values(), committed)).length > 0;}
    function draft() {onDraft(); actions.hidden = !changed(); submit.disabled = Boolean(pending);}
    async function details() {
      if (loaded || loading) return loading;
      feedback(form, 'Loading currency and logo…');
      loading = (async () => {
        try {
          const result = await api(`/accounts/${encodeURIComponent(form.dataset.inlineAccount)}/settings?workspace=${encodeURIComponent(workspace)}`);
          if (disposed) return;
          hydrateCurrencyChoices(currency); currency.value = result.account.currency;
          currency.disabled = !result.currencyEditable;
          if (website) {website.value = result.account.bankWebsite || ''; website.disabled = false;}
          committed = {...committed, currency: currency.value, ...(website ? {bankWebsite: website.value} : {})};
          loaded = true; logo.ready(); feedback(form); draft();
        } catch (error) {if (!disposed) feedback(form, error.message, true);}
        finally {loading = null;}
      })();
      return loading;
    }
    async function save() {
      if (pending) {if (!(await pending)) return false; return changed() ? save() : true;}
      const patch = accountChanges(values(), committed);
      if (!Object.keys(patch).length) return true;
      if (!form.reportValidity()) return false;
      onDraft(); feedback(form, 'Saving account…');
      pending = (async () => {
        try {
          await api(`/accounts/${encodeURIComponent(form.dataset.inlineAccount)}`, {workspace, ...patch}, 'PATCH');
          if (disposed) return true;
          committed = {...committed, ...patch}; feedback(form, 'Saved.');
          return true;
        } catch (error) {if (!disposed) feedback(form, error.message, true); return false;}
        finally {pending = null; if (!disposed) draft();}
      })();
      const result = await pending;
      if (result && !disposed) await synchronize(form);
      return result;
    }
    const editor = {get dirty() {return changed() || Boolean(pending);}, save};
    editors.set(form, editor);
    updateFields(form);
    listen(form, 'input', draft);
    listen(form.elements.onBudget, 'change', () => {updateFields(form); draft();});
    listen(form.elements.type, 'change', () => {updateFields(form); draft();});
    listen(form, 'change', draft);
    listen(form, 'submit', event => {event.preventDefault(); void save();});
    listen(form.querySelector('[data-account-cancel]'), 'click', () => {
      if (pending) return;
      for (const name of ['name', 'onBudget', 'type', 'subtype']) {
        form.elements[name].value = committed[name] === null ? '' : String(committed[name]);
        if (name === 'onBudget' || name === 'type') updateFields(form);
      }
      if (loaded) {currency.value = committed.currency; if (website) website.value = committed.bankWebsite;}
      logo.reset(); if (loaded) logo.ready();
      feedback(form); draft();
    });
    for (const toggle of form.querySelectorAll('[data-account-more]')) listen(toggle, 'click', () => {
      extra.hidden = !extra.hidden;
      form.querySelector('.account-logo-toggle').setAttribute('aria-expanded', String(!extra.hidden));
      if (!extra.hidden) void details();
    });
    listen(visibility, 'change', async () => {
      if (visibility.disabled) return;
      onDraft(); const desired = visibility.checked; visibility.disabled = true;
      feedback(form, 'Saving sidebar visibility…');
      try {
        await api(`/ui/preferences/accounts/${encodeURIComponent(form.dataset.inlineAccount)}`, {workspace, visible: desired}, 'PATCH');
        if (disposed) return;
        visible = desired; feedback(form, 'Saved.'); await synchronize(form);
      } catch (error) {if (!disposed) {visibility.checked = visible; feedback(form, error.message, true);}}
      finally {if (!disposed) visibility.disabled = false;}
    });
  }
  const dirty = () => [...editors.values()].some(editor => editor.dirty) || !!list.querySelector('[data-sidebar-visible]:disabled');
  listen(window, 'beforeunload', event => {if (dirty()) {event.preventDefault(); event.returnValue = '';}});
  return {get dirty() {return dirty();}, async flush() {
    if (list.querySelector('[data-sidebar-visible]:disabled')) return false;
    for (const editor of editors.values()) if (!(await editor.save())) return false;
    return true;
  }, dispose() {disposed = true; controller.abort();}};
}
