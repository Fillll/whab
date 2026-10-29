/* Exercise save acknowledgement, newer drafts and visibility failures with fictional accounts. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/account-list.js'), 'utf8')
  .replace(/^import .*;\n/gm, '').replaceAll('export function ', 'function ');
const tick = () => new Promise(resolve => setImmediate(resolve));
function events(properties = {}) {
  const listeners = new Map();
  return Object.assign({addEventListener(name, callback) {if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push(callback);},
    async emit(name, extra = {}) {for (const callback of listeners.get(name) || []) await callback({preventDefault() {}, ...extra});},
  }, properties);
}
function harness() {
  const requests = [], errors = {hidden: true}, status = {hidden: true}, actions = {hidden: true}, submit = {};
  const elements = Object.fromEntries(Object.entries({name: 'Fictional checking', onBudget: 'true', type: 'depository', subtype: '', currency: 'USD', bankWebsite: ''})
    .map(([name, value]) => [name, events({name, value, disabled: ['currency', 'bankWebsite'].includes(name)})]));
  const visibility = events({checked: true, disabled: false}), cancel = events(), toggle = events({setAttribute() {}}), extra = {hidden: true};
  let valid = true;
  const form = events({elements, dataset: {inlineAccount: 'fictional-one'}, reportValidity: () => valid,
    querySelector: selector => ({'[data-account-actions]': actions, '[type="submit"]': submit, '[data-account-extra]': extra,
      '[data-sidebar-visible]': visibility, '[data-account-cancel]': cancel, '.account-logo-toggle': toggle,
      '[data-account-status]': status, '.field-error': errors})[selector],
    querySelectorAll: selector => selector === '[data-account-more]' ? [toggle] : [],
  });
  const list = {dataset: {workspace: 'personal'}, querySelectorAll: () => [form],
    querySelector: selector => selector === '[data-sidebar-visible]:disabled' && visibility.disabled ? visibility : null};
  const context = {AbortController, Error, encodeURIComponent, document: {querySelector: () => list}, window: events(),
    location: {href: 'https://localhost/budgets/demo/accounts/2026-10/usd'}, getContext: () => ({budgetId:'fictional-budget'}),
    updateContext() {}, bindLogos() {}, hydrateCurrencyChoices() {}, initAccountLogoPreview: () => ({ready() {}, reset() {}}),
    fetch: async () => {throw new Error('Saved. Reload to update account totals.');}};
  vm.runInNewContext(source, context);
  const control = context.initAccountList({api(route, body, method) {return new Promise((resolve, reject) => requests.push({route, body: JSON.parse(JSON.stringify(body || null)), method, resolve, reject}));}, updateFields() {}});
  return {control, requests, form, elements, visibility, cancel, toggle, errors, status, actions, invalid() {valid = false;}};
}
test('account save retains a newer draft and failed retry stays editable', async () => {
  const h = harness();
  h.elements.name.value = 'First edit'; await h.form.emit('input');
  await h.form.emit('submit');
  assert.deepEqual(h.requests[0].body, {workspace:'personal', name:'First edit'});
  h.elements.name.value = 'Newer edit'; await h.form.emit('input');
  h.requests[0].resolve({ok:true}); await tick();
  assert.equal(h.elements.name.value, 'Newer edit'); assert.equal(h.control.dirty, true);
  const flush = h.control.flush(); await tick();
  assert.equal(h.requests[1].body.name, 'Newer edit'); h.requests[1].reject(new Error('Could not save.'));
  assert.equal(await flush, false); assert.equal(h.control.dirty, true);
  assert.equal(h.errors.textContent, 'Could not save.'); assert.equal(h.actions.hidden, false);
  const retry = h.control.flush(); await tick(); h.requests[2].resolve({ok:true});
  assert.equal(await retry, true); assert.equal(h.control.dirty, false);
  assert.equal(h.errors.textContent, 'Saved. Reload to update account totals.');
});
test('sidebar visibility uses only visual preferences and restores its confirmed choice on failure', async () => {
  const h = harness(); h.visibility.checked = false;
  const saving = h.visibility.emit('change');
  assert.equal(h.control.dirty, true);
  assert.equal(h.requests[0].route, '/ui/preferences/accounts/fictional-one');
  assert.deepEqual(h.requests[0].body, {workspace:'personal', visible:false});
  h.requests[0].reject(new Error('Preference unavailable.')); await saving;
  assert.equal(h.visibility.checked, true); assert.equal(h.visibility.disabled, false); assert.equal(h.control.dirty, false);
  h.visibility.checked = false; const retry = h.visibility.emit('change');
  h.requests[1].resolve({hiddenAccountIds:['fictional-one']}); await retry;
  assert.equal(h.visibility.checked, false); assert.equal(h.control.dirty, false);
});
test('lazy currency and logo reads preserve an account name already being edited', async () => {
  const h = harness(); await h.toggle.emit('click');
  h.elements.name.value = 'Typing while loading'; await h.form.emit('input');
  h.requests[0].resolve({account:{name:'Older name', currency:'USD', bankWebsite:'example.com'}, currencyEditable:false}); await tick();
  assert.equal(h.elements.name.value, 'Typing while loading'); assert.equal(h.elements.currency.disabled, true);
  assert.equal(h.elements.bankWebsite.disabled, false);
  const saving = h.control.flush(); await tick();
  assert.deepEqual(h.requests[1].body, {workspace:'personal', name:'Typing while loading'});
  h.requests[1].resolve({ok:true}); assert.equal(await saving, true);
});
test('cancel restores classification without writes and invalid names prevent a save', async () => {
  const h = harness(); h.elements.name.value = ''; await h.form.emit('input'); h.invalid();
  assert.equal(await h.control.flush(), false); assert.equal(h.requests.length, 0);
  await h.cancel.emit('click'); assert.equal(h.elements.name.value, 'Fictional checking'); assert.equal(h.control.dirty, false);
  h.control.dispose();
});
test('disposing ignores a late metadata response', async () => {
  const h = harness(); await h.toggle.emit('click'); h.control.dispose();
  h.requests[0].resolve({account:{currency:'EUR', bankWebsite:'example.com'}, currencyEditable:true}); await tick();
  assert.equal(h.elements.currency.value, 'USD'); assert.equal(h.elements.bankWebsite.disabled, true);
});
