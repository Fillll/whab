/* Run with node --test plugins/interface/web/tests/budget_source_links.test.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/budget-source-links.js'), 'utf8');

function harness(reducedMotion = false) {
  const listeners = [];
  const cells = new Map();
  const planner = {
    contains: cell => [...cells.values()].includes(cell),
    querySelectorAll: () => [...cells.values()].filter(cell => cell.classes.has('budget-source-highlight')),
  };
  const document = {addEventListener: (_, listener) => listeners.push(listener),
    getElementById: id => cells.get(id)};
  const context = {document, window: {matchMedia: () => ({matches: reducedMotion})}};
  vm.runInNewContext(source.replace('export function ', 'function '), context);
  context.initBudgetSourceLinks();

  function pair(month, currency = 'usd') {
    const overview = cell(`assigned-summary-${month}-${currency}`);
    const total = cell(`assigned-total-${month}-${currency}`);
    const link = {
      dataset: {budgetSourceLink: total.id, budgetSourceOverview: overview.id},
      closest: () => planner,
    };
    return {overview, total, target: {closest: () => link}};
  }
  function cell(id) {
    const classes = new Set();
    const result = {id, classes, classList: {add: value => classes.add(value), remove: value => classes.delete(value)},
      focus: options => {result.focused = options;}, scrollIntoView: options => {result.scrolled = options;}};
    cells.set(id, result);
    return result;
  }
  function click(target, changes = {}) {
    const event = {target, button: 0, defaultPrevented: false,
      preventDefault() {this.defaultPrevented = true;}, ...changes};
    for (const listener of listeners) listener(event);
    return event;
  }
  return {pair, click, cells};
}

test('a matching-total link focuses and scrolls its total while highlighting both exact cells', () => {
  const state = harness();
  const october = state.pair('2026-10');
  const november = state.pair('2026-11');
  const euro = state.pair('2026-10', 'eur');
  assert.equal(state.click(october.target).defaultPrevented, true);
  assert.equal(october.overview.classes.has('budget-source-highlight'), true);
  assert.equal(october.total.classes.has('budget-source-highlight'), true);
  assert.equal(october.total.focused.preventScroll, true);
  assert.equal(october.total.scrolled.block, 'center');
  assert.equal(october.total.scrolled.behavior, 'smooth');
  assert.equal(november.total.scrolled, undefined);
  assert.equal(euro.total.scrolled, undefined);
  state.click(euro.target);
  assert.equal(october.overview.classes.has('budget-source-highlight'), false);
  assert.equal(october.total.classes.has('budget-source-highlight'), false);
  assert.equal(euro.overview.classes.has('budget-source-highlight'), true);
  assert.equal(euro.total.classes.has('budget-source-highlight'), true);
});

test('delegation finds refreshed fragment cells and respects reduced motion', () => {
  const state = harness(true);
  const original = state.pair('2026-10');
  const replacement = state.pair('2026-10');
  state.click(replacement.target);
  assert.equal(original.total.scrolled, undefined);
  assert.equal(original.overview.classes.size, 0);
  assert.equal(replacement.total.scrolled.behavior, 'auto');
  assert.equal(replacement.overview.classes.has('budget-source-highlight'), true);
});

test('unrelated, modified, prevented or missing-target links keep native behavior', () => {
  const state = harness();
  const pair = state.pair('2026-10');
  assert.equal(state.click({closest: () => null}).defaultPrevented, false);
  for (const modifier of ['ctrlKey', 'metaKey', 'shiftKey', 'altKey']) {
    assert.equal(state.click(pair.target, {[modifier]: true}).defaultPrevented, false);
  }
  assert.equal(state.click(pair.target, {button: 1}).defaultPrevented, false);
  state.click(pair.target, {defaultPrevented: true});
  state.cells.delete(pair.total.id);
  assert.equal(state.click(pair.target).defaultPrevented, false);
  assert.equal(pair.total.scrolled, undefined);
  assert.equal(pair.overview.classes.size, 0);
});
