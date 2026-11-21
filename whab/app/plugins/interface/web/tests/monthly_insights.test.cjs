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
  return {controls, segments, querySelectorAll(selector) {
    return selector === '[data-chart-emphasis]' ? controls : segments;
  }};
}

test('applying date, grouping, account and category selections navigates without posting', async () => {
  const {initMonthlyInsights} = harness();
  const form = {
    action: 'https://localhost:5173/budgets/fictional/insights/current-month/eur',
    fields: [
      ['insight_from', '2026-08'], ['insight_to', '2026-11'], ['insight_group', 'category'],
      ['insight_account', 'café-wallet'], ['insight_category_group', ''], ['insight_category', 'category:market'],
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

test('emphasizing a series is local to its chart and reversible', () => {
  const {initMonthlyInsights} = harness();
  const main = legend();
  const second = legend();
  const report = {querySelector: () => ({addEventListener() {}}), querySelectorAll: () => [main, second]};
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
