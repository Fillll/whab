const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/plugin-filter.js'), 'utf8');
const ready = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
async function fixture({searchable = true} = {}) {
  const {initPluginFilter} = await ready;
  const category = new EventTarget(); category.value = 'all';
  const search = new EventTarget(); search.value = '';
  const empty = {hidden: true};
  const groups = [
    ['ingestion', [['Bank connection plaid', true], ['日本語 Import ynab', false]]],
    ['insight', [['Spending Report spending-report', true]]],
    ['automation', []],
  ].map(([name, values]) => {
    const rows = values.map(([name, checked]) => ({dataset: {pluginName: name}, checked, disabled: false, hidden: false}));
    return {dataset: {pluginCategory: name}, rows, hidden: false, querySelectorAll: () => rows};
  });
  const form = {querySelectorAll: () => groups, querySelector: () => empty};
  const controls = {'[data-plugin-selection]': form, '[data-plugin-category-filter]': category,
    '[data-plugin-search]': searchable ? search : null};
  const filter = initPluginFilter({page: {querySelector: selector => controls[selector]}});
  return {groups, category, search, empty, filter,
    visible: () => groups.filter(group => !group.hidden).flatMap(group => group.rows.filter(row => !row.hidden).map(row => row.dataset.pluginName)),
    query(value) {search.value = value; search.dispatchEvent(new Event('input'));},
    select(value) {category.value = value; category.dispatchEvent(new Event('change'));},
  };
}

test('search and category combine without losing hidden plugin selections', async () => {
  const h = await fixture();
  const rows = h.groups.flatMap(group => group.rows), original = rows.map(row => [row.checked, row.disabled]);
  h.query('  BANK  '); assert.deepEqual(h.visible(), ['Bank connection plaid']);
  h.select('insight'); assert.deepEqual(h.visible(), []); assert.equal(h.empty.hidden, false);
  h.query('report'); assert.deepEqual(h.visible(), ['Spending Report spending-report']);
  h.select('all'); h.query('');
  assert.equal(h.visible().length, 3); assert.equal(h.empty.hidden, true);
  assert.deepEqual(rows.map(row => [row.checked, row.disabled]), original);
});

test('search accepts Unicode display names and stable plugin IDs', async () => {
  const h = await fixture();
  h.query('日本語'); assert.deepEqual(h.visible(), ['日本語 Import ynab']);
  h.query('YNAB'); assert.deepEqual(h.visible(), ['日本語 Import ynab']);
  h.query('<script>'); assert.deepEqual(h.visible(), []); assert.equal(h.empty.hidden, false);
  h.query('   '); assert.equal(h.visible().length, 3); assert.equal(h.empty.hidden, true);
});

test('an empty selected category reports no matches and clearing it restores all rows', async () => {
  const h = await fixture(); h.select('automation');
  assert.deepEqual(h.visible(), []); assert.equal(h.empty.hidden, false);
  h.select('all'); assert.equal(h.empty.hidden, true); assert.equal(h.visible().length, 3);
});

test('category filtering also works when no search input is rendered', async () => {
  const h = await fixture({searchable: false}); h.select('ingestion');
  assert.deepEqual(h.visible(), ['Bank connection plaid', '日本語 Import ynab']);
});

test('unavailable selection forms do not register filter behavior', async () => {
  const {initPluginFilter} = await ready;
  assert.equal(initPluginFilter({page: {querySelector: () => null}}), null);
});
