const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/monthly-insights.js'), 'utf8')
  .replace('export function ', 'function ');

function harness() {
  const context = {URL, URLSearchParams, FormData: class {constructor(form) {return form.fields;}}};
  vm.runInNewContext(source, context);
  return context;
}

function legend() {
  const controls = [0, 1].map(index => ({
    dataset: {chartEmphasis: String(index)}, disabled: true, selected: false,
    setAttribute(name, value) {assert.equal(name, 'aria-pressed'); this.selected = value === 'true';},
    addEventListener(name, select) {assert.equal(name, 'click'); this.click = select;},
  }));
  const segments = [0, 1, 1].map(index => ({
    dataset: {chartSeries: String(index)}, dimmed: false,
    toggleAttribute(name, value) {assert.equal(name, 'data-chart-dimmed'); this.dimmed = value;},
  }));
  return {controls, segments, querySelector() { return null; }, querySelectorAll(selector) {
    return selector === '[data-chart-emphasis]' ? controls : segments;
  }};
}

test('applying date, grouping, account and category selections navigates without posting', async () => {
  const {initMonthlyInsights} = harness();
  const form = {
    action: 'https://localhost:5173/budgets/fictional/insights/current-month/eur',
    fields: [
      ['insight_report', 'plugin:monthly-spending'], ['insight_from', '2026-08'],
      ['insight_to', '2026-11'], ['insight_group', 'category'],
      ['insight_account', 'café-wallet'], ['insight_account', 'cash'],
      ['insight_category_group', 'living'], ['insight_category_group', 'transit'],
      ['insight_category', 'category:market'], ['insight_category', 'category:metro'],
    ],
    addEventListener(event, submit) {assert.equal(event, 'submit'); this.submit = submit;},
  };
  const report = {querySelector: () => form, querySelectorAll: () => []};
  const calls = [];
  initMonthlyInsights({root:{querySelector: () => report}, navigate: async url => {calls.push(url); return true;}});
  let prevented = false;
  await form.submit({preventDefault() {prevented = true;}});
  assert.ok(prevented);
  assert.equal(calls[0].pathname, '/budgets/fictional/insights/current-month/eur');
  assert.deepEqual([...calls[0].searchParams], form.fields);
});

test('too many fields keep the draft and explain how to apply it', async () => {
  const {initMonthlyInsights} = harness();
  const error = {hidden: true};
  const form = {
    action: 'https://localhost:5173/budgets/fictional/insights/current-month/usd',
    dataset: {monthlyFilterMaxFields: '128'},
    fields: [
      ['insight_report', 'plugin:monthly-spending'], ['insight_from', '2026-08'],
      ['insight_to', '2026-11'], ['insight_group', 'category'],
      ...Array.from({length: 125}, (_, index) => ['insight_category', `category:${index}`]),
    ],
    addEventListener(event, submit) { this.submit = submit; },
    querySelector() { return error; },
  };
  const report = {querySelector: () => form, querySelectorAll: () => []};
  const requests = [];
  initMonthlyInsights({root: {querySelector: () => report}, navigate: async url => { requests.push(url); return true; }});
  const event = {preventDefault() {}};
  await form.submit(event);
  assert.equal(error.hidden, false);
  assert.equal(requests.length, 0);
  assert.equal(form.fields.length, 129);
  form.fields.pop();
  await form.submit(event);
  assert.equal(error.hidden, true);
  assert.equal(requests[0].searchParams.size, 128);
});

test('search preserves selections and the picker clears to All without submitting', () => {
  const {initMonthlyFilter} = harness();
  const control = props => ({
    ...props,
    events: {},
    addEventListener(type, listener) { this.events[type] = listener; },
  });
  const options = ['Café', '日本旅行'].map(textContent => ({
    textContent, checkbox: {checked: true}, querySelector() { return this.checkbox; },
  }));
  const summary = {focused: false, focus() { this.focused = true; }};
  const label = {textContent: '2 selected'};
  const search = control({value: 'initial'});
  const clear = control({});
  const tools = {hidden: true};
  const empty = {};
  const children = {
    summary, '[data-monthly-filter-label]': label,
    '[data-monthly-filter-panel]': {getBoundingClientRect: () => ({right: 410})},
    '[data-monthly-filter-search]': search, '[data-monthly-filter-clear]': clear,
    '[data-monthly-filter-tools]': tools, '[data-monthly-filter-empty]': empty,
  };
  const attrs = new Set();
  const picker = control({
    open: true, ownerDocument: {documentElement: {clientWidth: 400}}, dataset: {allLabel: 'All categories'},
    querySelector: selector => children[selector],
    querySelectorAll: () => options,
    contains: target => Object.values(children).includes(target),
    setAttribute: name => attrs.add(name),
    removeAttribute: name => attrs.delete(name),
    toggleAttribute: (name, enabled) => enabled ? attrs.add(name) : attrs.delete(name),
  });
  initMonthlyFilter(picker);
  picker.events.toggle();
  assert.equal(tools.hidden, false);
  assert.equal(search.value, '');
  assert.equal(attrs.has('data-monthly-filter-align-end'), true);
  search.value = 'CAFE';
  search.events.input();
  assert.deepEqual(options.map(option => option.hidden), [false, true]);
  assert.deepEqual(options.map(option => option.checkbox.checked), [true, true]);
  options[0].checkbox.checked = false;
  picker.events.change({target: options[0].checkbox});
  assert.equal(label.textContent, '1 selected');

  function keystroke(key) {
    const event = {key, target: search, prevented: false, preventDefault() { this.prevented = true; }};
    picker.events.keydown(event);
    return event.prevented;
  }
  assert.equal(keystroke('Enter'), true);
  assert.equal(picker.open, true);
  assert.equal(keystroke('Escape'), true);
  assert.equal(picker.open, false);
  assert.equal(summary.focused, true);
  assert.equal(options[1].checkbox.checked, true);

  picker.open = true;
  clear.events.click();
  assert.deepEqual(options.map(option => option.checkbox.checked), [false, false]);
  assert.equal(label.textContent, 'All categories');
  assert.equal(picker.open, false);
});

test('bulk picker actions include hidden rows and distinguish all, subsets and explicit none when applying', async () => {
  const {initMonthlyInsights, initMonthlyFilter} = harness();
  const listener = (props = {}) => ({
    ...props, events: {}, addEventListener(type, select) { this.events[type] = select; },
  });
  function facet(name, allLabel, size) {
    const options = Array.from({length: size}, (_, index) => ({
      textContent: index ? `Choice ${index}` : 'Café',
      input: {name, value: `choice-${index}`, checked: false}, querySelector() { return this.input; },
    }));
    const label = {};
    const search = listener({value: ''});
    const select = listener();
    const deselect = listener();
    const children = {
      '[data-monthly-filter-label]': label,
      '[data-monthly-filter-search]': search,
      '[data-monthly-filter-select-all]': select,
      '[data-monthly-filter-deselect-all]': deselect,
      '[data-monthly-filter-tools]': {},
      '[data-monthly-filter-empty]': {},
    };
    return listener({
      open: true, label, search, select, deselect, options,
      dataset: {monthlyFilter: name, monthlyFilterNone: '0', monthlyFilterAll: '', allLabel},
      querySelector: selector => children[selector], querySelectorAll: () => options,
      setAttribute() {},
    });
  }
  const [account, group, category] = [
    facet('insight_account', 'All accounts', 3),
    facet('insight_category_group', 'All groups', 4),
    facet('insight_category', 'All categories', 135),
  ];
  const pickers = [account, group, category];
  const error = {hidden: true};
  const form = {
    action: 'https://localhost:5173/budgets/fictional/insights/current-month/usd',
    dataset: {monthlyFilterMaxFields: '128'},
    get fields() { return [
      ['insight_report', 'plugin:monthly-spending'],
      ...pickers.flatMap(picker => picker.options.filter(row => row.input.checked).map(row => [row.input.name, row.input.value])),
    ]; },
    addEventListener(_, submit) { this.submit = submit; },
    querySelector: () => error,
  };
  const report = {querySelector: () => form, querySelectorAll: selector => selector === '[data-monthly-filter]' ? pickers : []};
  const requests = [];
  initMonthlyInsights({root: {querySelector: () => report}, navigate: async url => { requests.push(url); return true; }});
  const event = {preventDefault() {}};
  assert.equal(form.fields.length, 143);
  await form.submit(event);
  assert.equal(error.hidden, true);
  assert.deepEqual([...requests[0].searchParams], [['insight_report', 'plugin:monthly-spending']]);

  category.search.value = 'cafe';
  category.search.events.input();
  assert.equal(category.options.filter(row => !row.hidden).length, 1);
  category.deselect.events.click();
  assert.ok(category.options.every(row => !row.input.checked));
  assert.equal(category.label.textContent, 'None selected');
  assert.equal(category.open, true);
  category.select.events.click();
  assert.ok(category.options.every(row => row.input.checked));
  assert.equal(category.label.textContent, 'All categories');
  assert.equal(category.search.value, 'cafe');
  assert.equal(category.open, true);
  account.options[0].input.checked = false;
  account.events.change({target: account.options[0].input});
  assert.equal(account.label.textContent, '2 selected');
  group.deselect.events.click();
  await form.submit(event);
  assert.deepEqual([...requests[1].searchParams], [
    ['insight_report', 'plugin:monthly-spending'], ['insight_account', 'choice-1'], ['insight_account', 'choice-2'],
    ['insight_category_group_none', '1'],
  ]);

  for (const picker of pickers) picker.deselect.events.click();
  await form.submit(event);
  assert.deepEqual([...requests[2].searchParams], [
    ['insight_report', 'plugin:monthly-spending'], ['insight_account_none', '1'],
    ['insight_category_group_none', '1'], ['insight_category_none', '1'],
  ]);

  const savedGroup = facet('insight_category_group', 'All groups', 2);
  delete savedGroup.dataset.monthlyFilterAll;
  savedGroup.options.forEach(row => { row.input.checked = true; });
  const savedGroupValues = new URLSearchParams('insight_category_group=choice-0&insight_category_group=choice-1');
  const writeSavedGroup = initMonthlyFilter(savedGroup);
  assert.equal(savedGroup.label.textContent, '2 selected');
  savedGroup.events.change({target: savedGroup.search});
  writeSavedGroup(savedGroupValues);
  assert.deepEqual(savedGroupValues.getAll('insight_category_group'), ['choice-0', 'choice-1']);
  savedGroup.select.events.click();
  writeSavedGroup(savedGroupValues);
  assert.equal(savedGroupValues.size, 0);
  assert.equal(savedGroup.label.textContent, 'All groups');

  const empty = facet('insight_account', 'All accounts', 0);
  const writeEmpty = initMonthlyFilter(empty);
  const emptyValues = new URLSearchParams();
  writeEmpty(emptyValues);
  assert.equal(emptyValues.size, 0);
  delete empty.dataset.monthlyFilterAll;
  empty.dataset.monthlyFilterNone = '1';
  const writeExplicitEmpty = initMonthlyFilter(empty);
  writeExplicitEmpty(emptyValues);
  assert.equal(emptyValues.get('insight_account_none'), '1');
});

test('emphasizing a series is local to its chart and reversible', () => {
  const {initMonthlyInsights} = harness();
  const main = legend();
  const second = legend();
  const report = {
    querySelector: () => ({addEventListener() {}}),
    querySelectorAll: selector => selector === '.monthly-insight' ? [main, second] : [],
  };
  initMonthlyInsights({root:{querySelector: () => report}});
  assert.equal(main.controls[0].disabled, false);
  main.controls[0].click();
  assert.deepEqual(main.controls.map(control => control.selected), [true, false]);
  assert.deepEqual(main.segments.map(segment => segment.dimmed), [false, true, true]);
  assert.deepEqual(second.segments.map(segment => segment.dimmed), [false, false, false]);
  main.controls[1].click();
  assert.deepEqual(main.controls.map(control => control.selected), [false, true]);
  assert.deepEqual(main.segments.map(segment => segment.dimmed), [true, false, false]);
  main.controls[1].click();
  assert.deepEqual(main.controls.map(control => control.selected), [false, false]);
  assert.deepEqual(main.segments.map(segment => segment.dimmed), [false, false, false]);
});

test('contributor previews show safe text, stay in the viewport and support hover, touch and keyboard', () => {
  const state = harness();
  let privacyChange;
  state.AbortController = AbortController;
  state.MutationObserver = class {
    constructor(change) { privacyChange = change; }
    observe() {}
    disconnect() { privacyChange = null; }
  };
  const eventTarget = props => ({
    ...props, events: {},
    addEventListener(name, handler) { this.events[name] = handler; },
  });
  const caption = {};
  const body = {
    rows: [], replaceChildren() { this.rows = []; },
    insertRow() {
      const row = {cells: [], setAttribute() {}, insertCell() {
        const cell = {
          setAttribute(name) { this.attribute = name; },
          append(child) { this.child = child; },
        };
        this.cells.push(cell);
        return cell;
      }};
      this.rows.push(row);
      return row;
    },
  };
  const panel = {
    id: 'preview', hidden: true, style: {},
    contains: target => target === panel,
    matches: () => panel.open,
    querySelector: selector => selector === 'tbody' ? body : caption,
    getBoundingClientRect: () => ({width: 320, height: 180}),
    showPopover() { this.open = true; },
    hidePopover() { this.open = false; },
  };
  const page = eventTarget({
    documentElement: {dataset: {amounts: 'shown'}, clientWidth: 360, clientHeight: 320},
    defaultView: eventTarget({setTimeout, clearTimeout}),
    createElement: tag => ({tag, setAttribute(name) { this.attribute = name; }}),
  });
  const triggers = ['Checking', 'Archive'].map(label => ({
    attributes: {'aria-label': label},
    dataset: {monthlyContributorTrigger: JSON.stringify([[label, '<shop & café>', '-$29', label === 'Checking' ? 'market' : null]])},
    contains(target) { return target === this; },
    closest() { return this; },
    getAttribute(name) { return this.attributes[name]; },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    querySelector() { return null; },
    getBoundingClientRect: () => ({left: 290, right: 320, top: 260, bottom: 280}),
    focus() { page.activeElement = this; plot.events.focusin({target: this}); },
  }));
  const scroll = {getBoundingClientRect: () => ({left: 0, right: 360})};
  const plot = eventTarget({
    ownerDocument: page,
    querySelector: selector => selector === '.monthly-chart-scroll' ? scroll : panel,
    querySelectorAll: () => triggers,
  });
  const event = (target, fields = {}) => ({target, preventDefault() {}, ...fields});
  const [one, two] = triggers;
  state.initContributorPreview(plot, new Map([['category:market', 'Home / <Market>']]));
  assert.equal(one.attributes.tabindex, '0');
  plot.events.pointerover(event(one, {pointerType: 'mouse'}));
  assert.equal(panel.open, true);
  assert.deepEqual(panel.style, {left: '32px', top: '72px'});
  assert.deepEqual(body.rows[0].cells.map(cell => cell.textContent), ['Checking', '<shop & café>', '-$29']);
  assert.equal(body.rows[0].cells[1].child.textContent, 'Home / <Market>');
  assert.equal(body.rows[0].cells[1].child.attribute, 'data-monthly-contributor-category');
  assert.equal(body.rows[0].cells[2].attribute, 'data-monthly-contributor-amount');

  one.focus();
  plot.events.keydown(event(one, {key: 'ArrowRight'}));
  assert.equal(two.attributes.tabindex, '0');
  assert.equal(two.attributes['aria-describedby'], 'preview');
  assert.equal(caption.textContent, 'Archive');
  assert.equal(body.rows[0].cells[1].child.textContent, 'Uncategorized');
  plot.events.keydown(event(two, {key: 'Enter'}));
  plot.events.pointerover(event(one, {pointerType: 'mouse'}));
  assert.equal(caption.textContent, 'Archive');
  page.events.keydown(event(two, {key: 'Escape'}));
  assert.equal(panel.hidden, true);
  assert.equal(body.rows.length, 0);

  plot.events.pointerover(event(one, {pointerType: 'touch'}));
  assert.equal(panel.hidden, true);
  plot.events.click(event(one));
  assert.equal(panel.hidden, false);
  plot.events.click(event(one));
  assert.equal(panel.hidden, true);
  plot.events.click(event(one));
  page.documentElement.dataset.amounts = 'hidden';
  privacyChange();
  assert.equal(panel.hidden, true);
  assert.equal(one.attributes['aria-expanded'], 'false');
  plot.events.click(event(one));
  assert.equal(panel.hidden, true);
});
