/* Run with node --test plugins/interface/web/tests/account_settings.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/account-settings.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

function events(properties = {}) {
  const listeners = {};
  return Object.assign({
    addEventListener(type, callback) {(listeners[type] ||= []).push(callback);},
    async emit(type, detail = {}) {
      const event = {prevented: false, preventDefault() {this.prevented = true;}, ...detail};
      await Promise.all((listeners[type] || []).map(callback => callback(event)));
      return event;
    },
  }, properties);
}

function currencySelect(code) {
  let value = code, lazy = true;
  return {name: 'currency', disabled: true, replacements: 0,
    options: [{value: code, dataset: {minorUnits: '2'}, defaultSelected: true}],
    get value() {return value;},
    set value(next) {value = this.options.some(option => option.value === next) ? next : '';},
    get selectedOptions() {return this.options.filter(option => option.value === value);},
    hasAttribute: name => name === 'data-currency-choices-lazy' && lazy,
    removeAttribute() {lazy = false;},
    replaceChildren(...options) {this.replacements += 1; this.options = options; this.reset();},
    reset() {this.value = this.options.find(option => option.defaultSelected)?.value || this.options[0]?.value || '';},
  };
}

function harness({flush = async () => true, logo = true, lazyCurrency = false, initialCurrency = 'USD'} = {}) {
  const requests = [], refreshed = [], updates = [], order = [];
  const error = {hidden: true, textContent: ''}, status = {textContent: ''}, submit = {disabled: true};
  const elements = Object.fromEntries(Object.entries({workspace: 'personal', name: '', onBudget: 'true',
    type: 'depository', subtype: '', currency: initialCurrency, ...(logo ? {bankWebsite: ''} : {})})
    .map(([name, value]) => [name, {name, value, disabled: name === 'currency',
      focus() {this.focused = true;}}]));
  if (lazyCurrency) elements.currency = currencySelect(initialCurrency);
  const original = Object.fromEntries(Object.entries(elements).map(([name, element]) => [name, element.value]));
  const dialog = events({open: true, closes: 0, close() {
    this.open = false; this.closes += 1; order.push('close'); void this.emit('close');
  }});
  const form = events({dataset: {}, elements,
    reset() {for (const [name, field] of Object.entries(elements)) {
      if (field.reset) field.reset();
      else field.value = original[name];
    }},
    closest: () => dialog,
    querySelector: selector => ({'.field-error': error, '[data-account-settings-status]': status,
      '[type="submit"]': submit})[selector],
  });
  let metadataReads = 0;
  const document = events({querySelector: () => form,
    getElementById() {metadataReads += 1; return {textContent: JSON.stringify({USD: 2, EUR: 2, JPY: 0, KWD: 3, CLF: 4})};},
    createElement: () => ({dataset: {}}),
  });
  const context = {document, encodeURIComponent, Error};
  vm.runInNewContext(source.replaceAll('export function ', 'function '), context);
  context.initAccountSettings({
    api(path, body, method) {
      const pending = deferred();
      requests.push({path, body: body === undefined ? undefined : plain(body), method, ...pending});
      order.push(method || 'GET');
      return pending.promise;
    },
    readForm() {
      return Object.fromEntries(Object.values(elements).filter(field => !field.disabled)
        .map(field => [field.name, field.name === 'onBudget' ? field.value === 'true' :
          field.name === 'subtype' ? field.value || null : field.value]));
    },
    updateFields() {updates.push({onBudget: elements.onBudget.value, type: elements.type.value});},
    flush,
    refresh: async value => {order.push('refresh'); refreshed.push(plain(value));},
  });
  function open(id = 'checking') {
    dialog.open = true;
    const button = {dataset: {editAccount: id}};
    return document.emit('click', {target: {closest: selector => selector === '[data-edit-account]' ? button : null}});
  }
  function metadata(index = 0, override = {}) {
    requests[index].resolve({account: {name: 'Everyday checking', onBudget: true, type: 'depository',
      subtype: 'checking', currency: 'USD', bankWebsite: 'https://bank.example', ...override.account},
    currencyEditable: false, ...override});
  }
  return {requests, refreshed, updates, order, error, status, submit, elements, dialog, form, open, metadata,
    initSettingsCurrencyChoices: context.initSettingsCurrencyChoices, hydrateCurrencyChoices: context.hydrateCurrencyChoices, metadataReads: () => metadataReads};
}

test('lazy currency choices preserve current selection, reset defaults and unrelated form drafts', () => {
  const state = harness({lazyCurrency: true});
  const currency = state.elements.currency;
  state.elements.name.value = 'Keep this account name draft';
  assert.equal(currency.options.length, 1);
  state.hydrateCurrencyChoices(currency);
  assert.equal(currency.value, 'USD');
  assert.equal(currency.options.length, 5);
  assert.equal(currency.disabled, true);
  assert.equal(state.elements.name.value, 'Keep this account name draft');
  currency.value = 'JPY';
  const options = currency.options;
  state.hydrateCurrencyChoices(currency);
  assert.equal(currency.options, options);
  assert.equal(currency.value, 'JPY');
  assert.equal(currency.replacements, 1);
  assert.equal(state.metadataReads(), 1);
  currency.reset();
  assert.equal(currency.value, 'USD');
  const second = currencySelect('EUR');
  state.hydrateCurrencyChoices(second);
  assert.equal(second.value, 'EUR');
  second.value = 'JPY'; second.reset();
  assert.equal(second.value, 'EUR');
  assert.equal(state.metadataReads(), 1);
});

test('hydrated manual currency choices submit the selected JPY, KWD and CLF minor units', () => {
  const state = harness({lazyCurrency: true});
  const currency = state.elements.currency;
  state.hydrateCurrencyChoices(currency);
  currency.disabled = false;
  const app = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
  const parser = {};
  vm.runInNewContext(app.slice(app.indexOf('function minorAmount('), app.indexOf('\nlet budgetEdits =')), parser);
  const balance = {name: 'balance', value: '', disabled: false, dataset: {minorUnits: '2'},
    hasAttribute: name => name === 'data-cents'};
  currency.hasAttribute = () => false;
  const elements = [currency, balance];
  elements.namedItem = name => name === 'currency' ? currency : balance;
  for (const [code, input, expected] of [['JPY', '123', 123], ['KWD', '1.234', 1234], ['CLF', '1.2345', 12345]]) {
    currency.value = code; balance.value = input;
    assert.deepEqual(plain(parser.formBody({elements})), {currency: code, balance: expected});
  }
  currency.value = 'JPY'; balance.value = '1.01';
  assert.throws(() => parser.formBody({elements}), /Enter a whole-number amount/);
});

test('the dialog opener hydrates currency choices before showModal without resetting form fields', () => {
  const app = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/app.js'), 'utf8');
  const snippet = app.slice(app.indexOf("document.addEventListener('click', event => {"), app.indexOf("for (const button of document.querySelectorAll('[data-close-dialog]')"));
  const order = [], currency = currencySelect('USD');
  let click;
  const dialog = {querySelectorAll: () => [currency], showModal: () => order.push('open')};
  const button = {dataset: {dialog: 'account-dialog'}};
  vm.runInNewContext(snippet, {
    document: {addEventListener: (_, callback) => {click = callback;}, getElementById: () => dialog},
    accountNavigation: {cancelPending() {}},
    hydrateCurrencyChoices: select => {assert.equal(select, currency); order.push('hydrate');},
  });
  click({target: {closest: () => button}});
  assert.deepEqual(order, ['hydrate', 'open']);
  button.disabled = true;
  click({target: {closest: () => button}});
  assert.deepEqual(order, ['hydrate', 'open']);
});

test('settings hydrate before applying another account currency and preserve its history lock', async () => {
  const state = harness({lazyCurrency: true});
  const currency = state.elements.currency;
  const loading = state.open('yen-account');
  assert.equal(currency.options.length, 1);
  state.metadata(0, {account: {name: 'Yen account', onBudget: true, type: 'depository', currency: 'JPY'}});
  await loading;
  assert.equal(currency.value, 'JPY');
  assert.equal(currency.options.length, 5);
  assert.equal(currency.disabled, true);
  state.elements.name.value = 'Renamed yen account';
  const saving = state.form.emit('submit');
  await tick();
  assert.equal('currency' in state.requests[1].body, false);
  state.requests[1].resolve({ok: true}); await saving;
  const options = currency.options;
  const next = state.open('euro-account');
  state.metadata(2, {currencyEditable: true, account: {name: 'Euro account', onBudget: true, type: 'depository', currency: 'EUR'}});
  await next;
  assert.equal(currency.value, 'EUR');
  assert.equal(currency.options, options);
  assert.equal(currency.disabled, false);
  assert.equal(currency.replacements, 1);
});

test('opening settings loads only the addressed account and keeps Save blocked until metadata arrives', async () => {
  const state = harness();
  const loading = state.open('bank/account');
  assert.equal(state.requests[0].path, '/accounts/bank%2Faccount/settings?workspace=personal');
  assert.equal(state.submit.disabled, true);
  assert.equal(state.elements.currency.disabled, true);
  assert.equal(state.status.textContent, 'Loading account settings…');
  state.metadata();
  await loading;
  assert.equal(state.elements.name.value, 'Everyday checking');
  assert.equal(state.elements.bankWebsite.value, 'https://bank.example');
  assert.equal(state.elements.subtype.value, 'checking');
  assert.equal(state.elements.currency.disabled, true);
  assert.equal(state.submit.disabled, false);
  assert.equal(state.elements.name.focused, true);
  assert.equal(state.updates.length, 2);
});

test('a slower metadata response cannot overwrite the account opened most recently', async () => {
  const state = harness({lazyCurrency: true});
  const first = state.open('first');
  const second = state.open('second');
  state.metadata(1, {account: {name: 'Second account', onBudget: false, type: 'other', currency: 'EUR'}});
  await second;
  state.metadata(0, {account: {name: 'First account'}});
  await first;
  assert.equal(state.elements.name.value, 'Second account');
  assert.equal(state.elements.currency.value, 'EUR');
  assert.equal(state.form.dataset.api, '/accounts/second');
  assert.equal(state.updates.length, 2);
});

test('closing a dialog invalidates an outstanding metadata request', async () => {
  const state = harness({lazyCurrency: true});
  const pending = state.open();
  state.dialog.close();
  state.metadata();
  await pending;
  assert.equal(state.elements.name.value, '');
  assert.equal(state.submit.disabled, true);
  assert.equal(state.updates.length, 0);
  assert.equal(state.elements.currency.options.length, 1);
  assert.equal(state.metadataReads(), 0);
});

test('a failed metadata request keeps Save disabled and cannot submit reset defaults', async () => {
  let flushes = 0;
  const state = harness({flush: async () => {flushes += 1; return true;}});
  const pending = state.open();
  state.requests[0].reject(new Error('Account settings are unavailable.'));
  await pending;
  assert.equal(state.error.hidden, false);
  assert.equal(state.error.textContent, 'Account settings are unavailable.');
  assert.equal(state.submit.disabled, true);
  const event = await state.form.emit('submit');
  assert.equal(event.prevented, true);
  assert.equal(state.requests.length, 1);
  assert.equal(flushes, 0);
});

test('one save PATCH includes classification and an editable currency, then refreshes without a reload', async () => {
  const savedEdits = deferred();
  const state = harness({flush: () => savedEdits.promise});
  const opening = state.open();
  state.metadata(0, {currencyEditable: true});
  await opening;
  Object.assign(state.elements.name, {value: 'Household savings'});
  state.elements.onBudget.value = 'false';
  state.elements.type.value = 'investment';
  state.elements.subtype.value = '';
  state.elements.currency.value = 'EUR';
  state.elements.bankWebsite.value = 'https://savings.example';
  const saving = state.form.emit('submit');
  await state.form.emit('submit');
  assert.equal(state.requests.length, 1);
  assert.equal(state.submit.disabled, true);
  assert.equal((await state.dialog.emit('cancel')).prevented, true);
  savedEdits.resolve(true);
  await tick();
  assert.deepEqual(state.requests[1].body, {workspace: 'personal', name: 'Household savings',
    onBudget: false, type: 'investment', subtype: null, currency: 'EUR', bankWebsite: 'https://savings.example'});
  assert.equal(state.requests[1].path, '/accounts/checking');
  assert.equal(state.requests[1].method, 'PATCH');
  assert.equal(state.dialog.open, true);
  state.requests[1].resolve({ok: true});
  await saving;
  assert.deepEqual(state.refreshed, [{currency: 'EUR', changedCurrency: true}]);
  assert.deepEqual(state.order, ['GET', 'PATCH', 'close', 'refresh']);
  assert.equal(state.dialog.closes, 1);
  assert.equal(state.requests.length, 2);
  assert.equal(state.form.dataset.busy, undefined);
});

test('history-locked currency is omitted and saving works without the optional logo plugin', async () => {
  const state = harness({logo: false});
  const opening = state.open();
  state.metadata();
  await opening;
  state.elements.name.value = 'Renamed account';
  const saving = state.form.emit('submit');
  await tick();
  assert.equal('currency' in state.requests[1].body, false);
  assert.equal('bankWebsite' in state.requests[1].body, false);
  state.requests[1].resolve({ok: true});
  await saving;
  assert.deepEqual(state.refreshed, [{currency: 'USD', changedCurrency: false}]);
});

test('a rejected PATCH retains edits and the open dialog, and retry submits the same account once', async () => {
  const state = harness();
  const opening = state.open();
  state.metadata();
  await opening;
  state.elements.name.value = 'New account label';
  const saving = state.form.emit('submit');
  await tick();
  state.requests[1].reject(new Error('Name could not be saved.'));
  await saving;
  assert.equal(state.dialog.open, true);
  assert.equal(state.elements.name.value, 'New account label');
  assert.equal(state.error.hidden, false);
  assert.equal(state.error.textContent, 'Name could not be saved.');
  assert.equal(state.submit.disabled, false);
  assert.equal(state.refreshed.length, 0);
  const retry = state.form.emit('submit');
  await tick();
  assert.equal(state.error.hidden, true);
  assert.deepEqual(state.requests[2].body, state.requests[1].body);
  assert.equal(state.requests[2].path, '/accounts/checking');
  state.requests[2].resolve({ok: true});
  await retry;
  assert.equal(state.dialog.closes, 1);
});

test('unsaved transaction failures block account mutation and retain the settings dialog', async () => {
  const state = harness({flush: async () => false});
  const opening = state.open();
  state.metadata();
  await opening;
  await state.form.emit('submit');
  assert.equal(state.requests.length, 1);
  assert.equal(state.dialog.open, true);
  assert.equal(state.error.textContent, 'Save the transaction or budget edit before continuing.');
  assert.equal(state.submit.disabled, false);
});


test('budget settings load currency choices on entry and preserve the selected draft on reopening', async () => {
  const state = harness();
  const currency = currencySelect('USD');
  const page = events({hidden: true, querySelectorAll: () => [currency]});
  state.initSettingsCurrencyChoices(page);
  assert.equal(currency.options.length, 1);
  await page.emit('whab:settings-enter');
  assert.ok(currency.options.some(option => option.value === 'JPY'));
  assert.ok(currency.options.some(option => option.value === 'KWD'));
  currency.value = 'JPY';
  await page.emit('whab:settings-enter');
  assert.equal(currency.value, 'JPY');
  assert.equal(currency.replacements, 1);
});

test('an addressed settings page already open at startup hydrates its currency picker', () => {
  const state = harness();
  const currency = currencySelect('KWD');
  state.initSettingsCurrencyChoices(events({hidden: false, querySelectorAll: () => [currency]}));
  assert.ok(currency.options.length > 1);
  assert.equal(currency.value, 'KWD');
});
