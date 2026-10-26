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
  getAttribute() {return this.id;}
  setAttribute(name, value) {this[name] = value;}
  showModal() {this.open = true; this.opens += 1;}
  close() {this.open = false; this.dispatchEvent(new Event('close'));}
  focus() {this.focused = true;}
}
async function fixture() {
  const {initSettingsNavigation, isSettingsPageOpen} = await moduleReady;
  const page = new EventTarget(), window = new Element('settings-dialog');
  const pages = ['settings-home', 'plugins-dialog', 'plugin-options-web', 'budgets-dialog', 'about-dialog'].map(id => new Element(id, window));
  const [home, plugins, web, budgets, about] = pages;
  for (const item of pages) item.dataset.settingsPage = '';
  for (const item of [plugins, budgets, about]) item.dataset.settingsParent = home.id;
  web.dataset.settingsParent = plugins.id;
  window.querySelectorAll = () => pages;
  page.getElementById = id => [window, ...pages].find(item => item.id === id);
  let cancellations = 0;
  const navigation = initSettingsNavigation({document: page, beforeOpen: () => {cancellations += 1;}});
  return {page, window, pages, home, plugins, web, budgets, about, navigation, isSettingsPageOpen, get cancellations() {return cancellations;}};
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

test('Settings to Plugins to plugin details uses one modal and one visible section', async () => {
  const h = await fixture();
  for (const id of ['settings-dialog', h.plugins.id, h.web.id]) {
    assert.equal(click(h.page, opener(id)).defaultPrevented, true);
    assert.equal(h.window.opens, 1);
    assert.equal(h.pages.filter(page => h.isSettingsPageOpen(page)).length, 1);
  }
  assert.equal(h.isSettingsPageOpen(h.web), true);
  assert.equal(h.window['aria-label'], h.web.id);
  assert.equal(h.cancellations, 3);
});

test('Back preserves the mounted draft, restores scroll and focuses the opening control', async () => {
  const h = await fixture(); h.navigation.open('settings-dialog');
  h.navigation.open(h.plugins.id); h.plugins.draft = {selection: ['web']};
  h.window.scrollTop = 180;
  const control = opener(h.web.id); h.navigation.open(h.web.id, control);
  assert.equal(h.window.scrollTop, 0);
  h.navigation.back();
  assert.equal(h.isSettingsPageOpen(h.plugins), true);
  assert.deepEqual(h.plugins.draft, {selection: ['web']});
  assert.equal(h.window.scrollTop, 180); assert.equal(control.focused, true);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.home), true);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.home), true);
});

test('deep entry constructs its parent path; closing any section dismisses Settings and reopening starts at home', async () => {
  const h = await fixture(); h.navigation.open(h.web.id);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.plugins), true);
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.home), true);
  h.navigation.open(h.budgets.id); h.window.close();
  assert.equal(h.pages.some(page => h.isSettingsPageOpen(page)), false);
  h.navigation.open('settings-dialog'); assert.equal(h.isSettingsPageOpen(h.home), true);
});

test('leaving a section closes its plugin lifecycle exactly once and Back starts the parent again', async () => {
  const h = await fixture(); const events = [];
  for (const item of h.pages) for (const name of ['close', 'whab:settings-enter']) item.addEventListener(name, () => events.push(`${item.id}:${name}`));
  h.navigation.open('settings-dialog'); h.navigation.open(h.plugins.id); h.navigation.open(h.web.id); h.navigation.back(); h.window.close();
  assert.deepEqual(events, ['settings-home:whab:settings-enter', 'settings-home:close', 'plugins-dialog:whab:settings-enter',
    'plugins-dialog:close', 'plugin-options-web:whab:settings-enter', 'plugin-options-web:close', 'plugins-dialog:whab:settings-enter', 'plugins-dialog:close']);
});

test('busy budget work blocks Back, section changes and Escape', async () => {
  const h = await fixture(); h.navigation.open(h.budgets.id); h.window.busy = true;
  h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.budgets), true);
  assert.equal(h.navigation.open(h.plugins.id), false);
  const cancel = new Event('cancel', {cancelable: true}); h.window.dispatchEvent(cancel);
  assert.equal(cancel.defaultPrevented, true);
  h.window.busy = false; h.navigation.back(); assert.equal(h.isSettingsPageOpen(h.home), true);
});

test('account settings is suspended and restored with its draft without stacking native dialogs', async () => {
  const h = await fixture(), account = new Element('account-settings'); account.open = true; account.draft = 'Unsaved name';
  const control = opener(h.web.id, account);
  h.navigation.open(h.web.id, control);
  assert.equal(account.open, false); assert.equal(h.window.open, true);
  h.window.close(); assert.equal(account.open, true); assert.equal(h.window.open, false);
  assert.equal(account.draft, 'Unsaved name'); assert.equal(control.focused, true);
  account.busy = true; assert.equal(h.navigation.open(h.web.id, control), false); assert.equal(account.open, true);
});

test('disabled openers are ignored; Back buttons navigate inside the same window', async () => {
  const h = await fixture(), control = opener('settings-dialog'); control.disabled = true;
  click(h.page, control); assert.equal(h.window.open, false);
  h.navigation.open(h.plugins.id);
  const back = {parent: h.window, closest: selector => selector === '[data-settings-back]' ? back : null};
  assert.equal(click(h.page, back).defaultPrevented, true); assert.equal(h.isSettingsPageOpen(h.home), true);
});
