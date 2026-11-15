const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/settings-navigation.js'), 'utf8');
const moduleReady = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
class Element extends EventTarget {
  constructor(id, parent = null) {
    super(); this.id = id; this.parent = parent; this.dataset = {}; this.hidden = true;
    this.open = false; this.scrollTop = 0; this.opens = 0; this.busy = false; this.focused = false;
    this.heading = {focus: () => {this.focused = true;}};
  }
  querySelector(selector) {return selector === 'h2' ? this.heading : this.busy ? {} : null;}
  closest(selector) {return selector === 'dialog' ? this.parent : null;}
  contains(node) {return node.parent === this;}
  getAttribute(name) {return name === 'aria-label' ? this.id : this[name] ?? null;}
  setAttribute(name, value) {this[name] = value;}
  removeAttribute(name) {delete this[name];}
  showModal() {this.open = true; this.opens += 1;}
  close() {this.open = false; this.dispatchEvent(new Event('close'));}
  focus() {this.focused = true;}
}
async function fixture({content = true} = {}) {
  const {initSettingsNavigation, isSettingsPageOpen} = await moduleReady;
  const page = new EventTarget(), window = new Element('settings-dialog');
  const pages = ['plugins-dialog', 'plugin-options-web', 'budgets-dialog', 'appearance-settings-page', 'budget-history-page', 'about-dialog'].map(id => new Element(id, window));
  const [plugins, web, budgets, appearance, history, about] = pages;
  for (const item of pages) item.dataset.settingsPage = '';
  web.dataset.settingsParent = plugins.id;
  const sections = [budgets, appearance, plugins, history, about].map(item => {
    const button = opener(item.id, window); button.dataset.settingsSection = ''; return button;
  });
  const scroller = content ? new Element('content', window) : window;
  window.querySelectorAll = selector => selector === '[data-settings-page]' ? pages : sections;
  const query = window.querySelector.bind(window);
  window.querySelector = selector => selector === '[data-settings-content]' ? content ? scroller : null : query(selector);
  page.getElementById = id => [window, ...pages].find(item => item.id === id);
  let cancellations = 0;
  const navigation = initSettingsNavigation({document: page, beforeOpen: () => {cancellations += 1;}});
  return {page, window, scroller, sections, pages, plugins, web, budgets, appearance, history, about, navigation, isSettingsPageOpen, get cancellations() {return cancellations;}};
}
function click(page, target) {
  const event = new Event('click', {cancelable: true});
  Object.defineProperty(event, 'target', {value: target}); page.dispatchEvent(event); return event;
}
function opener(id, parent = null) {
  const button = new Element('opener', parent); button.dataset.dialog = id;
  button.closest = selector => selector === '[data-dialog]' ? button : selector === 'dialog' ? parent : null;
  return button;
}
function activeSections(h) {return h.sections.filter(button => button.getAttribute('aria-current') === 'page').map(button => button.dataset.dialog);}

test('Settings opens Plugins directly and keeps one modal for plugin details', async () => {
  const h = await fixture();
  for (const id of ['settings-dialog', h.web.id]) {
    assert.equal(click(h.page, opener(id)).defaultPrevented, true);
    assert.equal(h.window.opens, 1);
    assert.equal(h.pages.filter(page => h.isSettingsPageOpen(page)).length, 1);
    assert.deepEqual(activeSections(h), [h.plugins.id]);
  }
  assert.equal(h.isSettingsPageOpen(h.web), true);
  assert.equal(h.window['aria-label'], h.web.id);
  assert.equal(h.cancellations, 2);
});

test('Back preserves mounted drafts, content scroll and the opening control focus', async () => {
  const h = await fixture(); h.navigation.open('settings-dialog');
  h.plugins.draft = {selection: ['web']}; h.scroller.scrollTop = 180;
  const control = opener(h.web.id); h.navigation.open(h.web.id, control);
  assert.equal(h.scroller.scrollTop, 0);
  h.navigation.back();
  assert.equal(h.isSettingsPageOpen(h.plugins), true);
  assert.deepEqual(h.plugins.draft, {selection: ['web']});
  assert.equal(h.scroller.scrollTop, 180); assert.equal(control.focused, true);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.plugins), true);
});

test('direct detail entry constructs its parent path; close and reopen starts at Plugins', async () => {
  const h = await fixture(); h.navigation.open(h.web.id);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.plugins), true);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.plugins), true);
  h.navigation.open(h.budgets.id); h.window.close();
  assert.equal(h.pages.some(page => h.isSettingsPageOpen(page)), false);
  h.navigation.open('settings-dialog'); assert.equal(h.isSettingsPageOpen(h.plugins), true);
});

test('persistent section links expose the active scope and reset nested Back paths', async () => {
  const h = await fixture(); h.navigation.open(h.web.id);
  h.plugins.draft = {selection: ['web', 'report']};
  const appearance = h.sections.find(button => button.dataset.dialog === h.appearance.id);
  click(h.page, appearance);
  assert.equal(h.isSettingsPageOpen(h.appearance), true);
  assert.deepEqual(activeSections(h), [h.appearance.id]);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.appearance), true);
  click(h.page, h.sections.find(button => button.dataset.dialog === h.plugins.id));
  assert.equal(h.isSettingsPageOpen(h.plugins), true);
  assert.deepEqual(h.plugins.draft, {selection: ['web', 'report']});
  assert.deepEqual(activeSections(h), [h.plugins.id]);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.plugins), true);
  assert.equal(h.window.opens, 1);
});

test('leaving a section stops its lifecycle exactly once and Back restarts its parent', async () => {
  const h = await fixture(); const events = [];
  for (const item of h.pages) for (const name of ['close', 'whab:settings-enter']) item.addEventListener(name, () => events.push(`${item.id}:${name}`));
  h.navigation.open('settings-dialog'); h.navigation.open(h.web.id); h.navigation.back(); h.window.close();
  assert.deepEqual(events, ['plugins-dialog:whab:settings-enter', 'plugins-dialog:close',
    'plugin-options-web:whab:settings-enter', 'plugin-options-web:close', 'plugins-dialog:whab:settings-enter', 'plugins-dialog:close']);
});

test('busy budget work blocks Back, direct section changes, Close and Escape', async () => {
  const h = await fixture(); h.navigation.open(h.budgets.id); h.window.busy = true;
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.budgets), true);
  assert.equal(h.navigation.open(h.plugins.id), false);
  click(h.page, h.sections.find(button => button.dataset.dialog === h.appearance.id));
  assert.equal(h.isSettingsPageOpen(h.budgets), true);
  assert.deepEqual(activeSections(h), [h.budgets.id]);
  assert.equal(h.navigation.close(), false);
  const cancel = new Event('cancel', {cancelable: true}); h.window.dispatchEvent(cancel);
  assert.equal(cancel.defaultPrevented, true);
  h.window.busy = false;
  const allowed = new Event('cancel', {cancelable: true}); h.window.dispatchEvent(allowed);
  assert.equal(allowed.defaultPrevented, false);
  assert.equal(h.navigation.close(), true); assert.equal(h.window.open, false);
});

test('account settings is suspended and restored with its draft without stacking dialogs', async () => {
  const h = await fixture(), account = new Element('account-settings'); account.open = true; account.draft = 'Unsaved name';
  const control = opener(h.web.id, account);
  h.navigation.open(h.web.id, control);
  assert.equal(account.open, false); assert.equal(h.window.open, true);
  h.window.close(); assert.equal(account.open, true); assert.equal(h.window.open, false);
  assert.equal(account.draft, 'Unsaved name'); assert.equal(control.focused, true);
  account.busy = true; assert.equal(h.navigation.open(h.web.id, control), false); assert.equal(account.open, true);
});

test('disabled openers are ignored; detail Back stays inside the same window', async () => {
  const h = await fixture(), control = opener('settings-dialog'); control.disabled = true;
  click(h.page, control); assert.equal(h.window.open, false);
  h.navigation.open(h.web.id);
  const back = {parent: h.window, closest: selector => selector === '[data-settings-back]' ? back : null};
  assert.equal(click(h.page, back).defaultPrevented, true); assert.equal(h.isSettingsPageOpen(h.plugins), true);
});

test('visual handover closes Settings without restoring a readiness-blocking account dialog', async () => {
  const h = await fixture(), account = new Element('account-settings'); account.open = true;
  h.navigation.open(h.web.id, opener(h.web.id, account));
  assert.equal(h.navigation.close({restoreOrigin: false}), true);
  assert.equal(account.open, false); assert.equal(h.window.open, false);
  assert.equal(h.pages.some(page => h.isSettingsPageOpen(page)), false);
});

test('legacy standalone fixture can still restore modal scroll without a content wrapper', async () => {
  const h = await fixture({content: false}); h.navigation.open('settings-dialog'); h.window.scrollTop = 95;
  h.navigation.open(h.web.id); h.navigation.back();
  assert.equal(h.window.scrollTop, 95);
});
