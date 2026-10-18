/* Run with node --test plugins/interface/web/tests/account_logo_preview.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/account-settings.js'), 'utf8');
const localLogo = '/budgets/home/plugins/bank-logos/robinhood/logo.png';
const tick = () => new Promise(resolve => setImmediate(resolve));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

function events(properties = {}) {
  const listeners = {};
  return Object.assign({
    addEventListener(type, callback) {(listeners[type] ||= []).push(callback);},
    async emit(type, event) {await Promise.all((listeners[type] || []).map(callback => callback(event)));},
  }, properties);
}

function keyboardEvent(key, properties = {}) {
  return {key, isComposing: false, defaultPrevented: false,
    preventDefault() {this.defaultPrevented = true;}, ...properties};
}

function harness({website = 'Robinhood.com', available = true, complete = false} = {}) {
  const requests = [];
  const button = events({disabled: true});
  const field = events({value: website});
  const submit = {disabled: false};
  const status = {textContent: ''};
  const image = {hidden: true, onload: null, onerror: null, complete, naturalWidth: complete ? 48 : 0,
    removeAttribute(name) {delete this[name];}};
  const form = {dataset: {}, elements: {bankWebsite: field}, querySelector(selector) {
    return {'[data-account-logo-retrieve]': available ? button : null,
      '[data-account-logo-preview]': image, '[data-account-logo-status]': status,
      '[type="submit"]': submit}[selector];
  }};
  const location = new URL('https://localhost:5173/budgets/home/transactions/current-month/usd');
  const context = {URL, location};
  vm.runInNewContext(source.replaceAll('export function ', 'function '), context);
  const preview = context.initAccountLogoPreview(form, (url, body, method) => {
    const pending = deferred();
    requests.push({url, body: JSON.parse(JSON.stringify(body)), method, ...pending});
    return pending.promise;
  });
  const success = (index = 0, overrides = {}) => requests[index].resolve({available: true,
    website: 'https://robinhood.com', logoUrl: localLogo,
    provider: {provider_name: 'Icon Horse'}, ...overrides});
  return {requests, button, field, submit, image, status, form, preview, success};
}

test('logo retrieval is optional and unavailable plugin settings need no extra controls', async () => {
  const state = harness({available: false});
  state.preview.reset();
  state.preview.ready();
  await state.button.emit('click');
  assert.equal(state.requests.length, 0);
  assert.equal(state.submit.disabled, false);
});

test('retrieval posts the entered schemeless website without saving account metadata', async () => {
  const state = harness();
  await state.button.emit('click');
  assert.equal(state.requests.length, 0, 'account metadata must be ready first');
  state.preview.ready();
  const retrieving = state.button.emit('click');
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, '/plugins/bank-logo-preview');
  assert.deepEqual(state.requests[0].body, {website: 'Robinhood.com'});
  assert.equal(state.requests[0].method, undefined, 'the API helper defaults body requests to POST');
  assert.equal(state.status.textContent, 'Retrieving logo…');
  assert.equal(state.submit.disabled, false, 'an optional logo lookup cannot block Save');
  state.success();
  await retrieving;
  assert.equal(state.field.value, 'https://robinhood.com');
  assert.equal(state.image.src, localLogo);
  assert.equal(state.image.hidden, true, 'wait for the actual image to load before showing it');
  assert.equal(state.status.textContent, 'Loading preview…');
  state.image.onload();
  assert.equal(state.image.hidden, false);
  assert.equal(state.status.textContent, 'Logo retrieved from Icon Horse.');
  assert.equal(state.requests.length, 1, 'preview must not PATCH or otherwise save an account');
});

test('repeated clicks share the active lookup and cannot issue duplicate requests', async () => {
  const state = harness();
  state.preview.ready();
  const pending = state.button.emit('click');
  assert.equal(state.button.disabled, true);
  await state.button.emit('click');
  assert.equal(state.requests.length, 1);
  state.success();
  await pending;
  assert.equal(state.button.disabled, false);
});

test('Enter in the website field retrieves the preview instead of submitting account settings', async () => {
  const state = harness();
  state.preview.ready();
  const enter = keyboardEvent('Enter');
  const pending = state.field.emit('keydown', enter);
  assert.equal(enter.defaultPrevented, true, 'Enter must not submit the account form');
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, '/plugins/bank-logo-preview');
  assert.deepEqual(state.requests[0].body, {website: 'Robinhood.com'});
  assert.equal(state.requests[0].method, undefined, 'preview uses POST without saving metadata');
  assert.equal(state.submit.disabled, false);
  state.success();
  await pending;
  await tick();
  state.image.onload();
  assert.equal(state.field.value, 'https://robinhood.com');
  assert.equal(state.image.src, localLogo);
  assert.equal(state.image.hidden, false);
  assert.equal(state.requests.length, 1, 'Enter must never also save the account');
});

test('repeated Enter and a click cannot duplicate an active keyboard lookup', async () => {
  const state = harness();
  state.preview.ready();
  const pending = state.field.emit('keydown', keyboardEvent('Enter'));
  assert.equal(state.button.disabled, true);
  const repeated = keyboardEvent('Enter');
  await state.field.emit('keydown', repeated);
  await state.button.emit('click');
  assert.equal(repeated.defaultPrevented, true, 'a pending lookup must not allow implicit Save');
  assert.equal(state.requests.length, 1);
  state.success();
  await pending;
  await tick();
  assert.equal(state.button.disabled, false);
});

test('Enter cannot retrieve or submit before metadata is ready, after reset or during Save', async () => {
  for (const mode of ['not-ready', 'reset', 'saving']) {
    const state = harness();
    if (mode !== 'not-ready') state.preview.ready();
    if (mode === 'reset') state.preview.reset();
    if (mode === 'saving') state.form.dataset.busy = 'true';
    const enter = keyboardEvent('Enter');
    await state.field.emit('keydown', enter);
    await tick();
    assert.equal(enter.defaultPrevented, true, `${mode} must not permit an implicit Save`);
    assert.equal(state.requests.length, 0, `${mode} must not begin a logo lookup`);
  }
});

test('other keys and IME composition keep their normal website-field behavior', async () => {
  const state = harness();
  state.preview.ready();
  for (const event of [keyboardEvent('Tab'), keyboardEvent('Escape'), keyboardEvent('r'),
    keyboardEvent('Enter', {isComposing: true})]) {
    await state.field.emit('keydown', event);
    assert.equal(event.defaultPrevented, false, `${event.key} must remain available to the input`);
    assert.equal(state.requests.length, 0);
  }
});

test('a cached image that is already complete immediately shows the preview', async () => {
  const state = harness({complete: true});
  state.preview.ready();
  const pending = state.button.emit('click');
  state.success(0, {provider: null});
  await pending;
  assert.equal(state.image.hidden, false);
  assert.equal(state.status.textContent, 'Logo retrieved from the logo provider.');
});

test('a missing logo and an API error both remain neutral and leave account Save available', async () => {
  for (const failure of ['not-found', 'request-error']) {
    const state = harness({website: 'not a website'});
    state.preview.ready();
    const pending = state.button.emit('click');
    if (failure === 'not-found') state.requests[0].resolve({available: false});
    else state.requests[0].reject(new Error('404: optional provider not available'));
    await pending;
    assert.equal(state.status.textContent, 'No logo found. You can still save this account.');
    assert.equal(state.image.hidden, true);
    assert.equal(state.image.src, undefined);
    assert.equal(state.field.value, 'not a website');
    assert.equal(state.submit.disabled, false);
    assert.equal(state.button.disabled, false);
    const retry = state.button.emit('click');
    state.success(1);
    await retry;
    assert.equal(state.requests.length, 2);
  }
});

test('changing website text permits a new request and ignores the earlier out-of-order response', async () => {
  const state = harness();
  state.preview.ready();
  const old = state.button.emit('click');
  state.field.value = 'chase.com';
  await state.field.emit('input');
  assert.equal(state.status.textContent, '');
  assert.equal(state.button.disabled, false);
  const latest = state.button.emit('click');
  state.success(1, {website: 'https://chase.com', logoUrl: '/logos/chase.png'});
  await latest;
  state.image.onload();
  state.success(0);
  await old;
  assert.equal(state.field.value, 'https://chase.com');
  assert.equal(state.image.src, '/logos/chase.png');
  assert.equal(state.image.hidden, false);
  assert.equal(state.status.textContent, 'Logo retrieved from Icon Horse.');
});

test('closing settings resets an outstanding lookup without re-enabling its button', async () => {
  const state = harness();
  state.preview.ready();
  const pending = state.button.emit('click');
  state.preview.reset();
  state.success();
  await pending;
  assert.equal(state.image.src, undefined);
  assert.equal(state.image.hidden, true);
  assert.equal(state.status.textContent, '');
  assert.equal(state.button.disabled, true);
  assert.equal(state.field.value, 'Robinhood.com');
});

test('an earlier account lookup cannot overwrite the next account settings', async () => {
  const state = harness();
  state.preview.ready();
  const previous = state.button.emit('click');
  state.preview.reset();
  state.field.value = 'second.example';
  state.preview.ready();
  const current = state.button.emit('click');
  state.success(0);
  await previous;
  assert.equal(state.image.src, undefined);
  assert.equal(state.button.disabled, true, 'an old response cannot enable the active request button');
  assert.equal(state.field.value, 'second.example');
  state.success(1, {website: 'https://second.example', logoUrl: '/logos/second.png'});
  await current;
  assert.equal(state.image.src, '/logos/second.png');
});

test('late image load or error callbacks cannot resurrect a preview after typing or closing', async () => {
  for (const invalidate of ['input', 'reset']) {
    const state = harness();
    state.preview.ready();
    const pending = state.button.emit('click');
    state.success();
    await pending;
    const loaded = state.image.onload, failed = state.image.onerror;
    if (invalidate === 'input') {
      state.field.value = 'another.example';
      await state.field.emit('input');
    } else state.preview.reset();
    loaded();
    failed();
    assert.equal(state.image.src, undefined);
    assert.equal(state.image.hidden, true);
    assert.equal(state.status.textContent, '');
  }
});

test('an image HTTP or decoding failure does not turn optional artwork into a form error', async () => {
  const state = harness();
  state.preview.ready();
  const pending = state.button.emit('click');
  state.success();
  await pending;
  state.image.onerror();
  assert.equal(state.image.hidden, true);
  assert.equal(state.status.textContent, 'No logo found. You can still save this account.');
  assert.equal(state.submit.disabled, false);
  assert.equal(state.button.disabled, false);
});

test('remote, scheme-relative and browser-normalized external image destinations are rejected', async () => {
  for (const logoUrl of ['https://external.example/logo.png', '//external.example/logo.png',
    '/\\external.example/logo.png', 'javascript:alert(1)', 'data:image/png;base64,AAAA', '', null]) {
    const state = harness();
    state.preview.ready();
    const pending = state.button.emit('click');
    state.success(0, {logoUrl});
    await pending;
    assert.equal(state.image.src, undefined, `untrusted preview URL must not be assigned: ${logoUrl}`);
    assert.equal(state.image.hidden, true);
    assert.equal(state.status.textContent, 'No logo found. You can still save this account.');
    assert.equal(state.submit.disabled, false);
  }
});

test('busy account saves do not begin a new optional artwork lookup', async () => {
  const state = harness();
  state.preview.ready();
  state.form.dataset.busy = 'true';
  await state.button.emit('click');
  await tick();
  assert.equal(state.requests.length, 0);
});
