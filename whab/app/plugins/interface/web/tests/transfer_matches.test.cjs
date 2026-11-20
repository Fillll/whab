const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transfer-matches.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness({present = true, rows = [], currency = 'USD'} = {}) {
  const nodes = new Map(), calls = [], state = {before: true, fail: null, saved: 0, revision: 'r', rows, focus: null};
  function node(tag) {
    return {
      tag, children: [], hidden: false, open: false, disabled: false, textContent: '', dataset: {}, events: {}, attributes: {},
      append(...items) {this.children.push(...items);}, replaceChildren() {this.children = [];},
      get firstElementChild() {return this.children[0];}, get lastElementChild() {return this.children.at(-1);},
      querySelector(selector) {return this.children.find(child => child.tag === selector) || this.children.map(child => child.querySelector(selector)).find(Boolean);},
      focus() {state.focus = this;}, showModal() {this.open = true;}, close() {this.open = false;},
      setAttribute(name, value) {this.attributes[name] = value;},
      addEventListener(name, callback) {this.events[name] = callback;}, removeEventListener(name) {delete this.events[name];},
    };
  }
  const root = node(), get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, node(selector));
    return nodes.get(selector);
  };
  root.querySelector = selector => present ? get(selector) : null;
  const panel = get('[data-transfer-panel]');
  panel.querySelector = get;
  let nextID = 0;
  const runtime = {document: {createElement: node, getElementById: id => {
    assert.equal(id, 'account-currency-choices');
    return {textContent: '{"USD":2,"IQD":3}'};
  }}, Intl, crypto: {randomUUID: () => `intent-${++nextID}`}, Error};
  vm.runInNewContext(source.replace('export function ', 'function '), runtime);
  const leg = id => ({id, accountName: id === 'out' ? '旅行 🍏' : 'Checking B', date: '2026-11-01',
    merchant: '<script> refund', amount: rows.find(row => row.id === id)?.amount === 0 ? 0 : id === 'out' ? 100049 : -100049,
    currency, reviewed: false, note: '[YNAB]\n  exact'});
  const candidate = (ids = ['out', 'in']) => ({transactionIds: ids, transactions: ids.map(leg), daysApart: 0,
    bankHint: true, ambiguous: true});
  const fingerprint = 'a'.repeat(64);
  const control = runtime.initTransferMatches({root, selectedRows: () => state.rows,
    context: () => ({workspace: 'personal', currency, selectedAccountIds: ['visible'], startDate: '2026-11-01', endDate: '2026-11-30'}),
    before: async () => state.before,
    saved: async () => {
      assert.equal(panel.open, false);
      assert.equal(await control.flush(), true);
      state.saved++;
    },
    api: async (url, body) => {
      calls.push({url, body});
      if (state.read && url.endsWith('/query')) return state.read(body);
      if (url.endsWith('/apply')) {
        if (state.fail) {const failure = new Error('Unknown outcome');failure.code = state.fail;throw failure;}
        const changes = body.transactionIds.map((id, index) => ({id, after: {id, reviewed: false,
          isTransfer: body.action === 'match', transferPeerId: body.action === 'match' ? body.transactionIds[1 - index] : null}}));
        if (state.wrongReceipt) changes[0].after.reviewed = true;
        return {revision: 'saved', data: {count: 2, changes, action: body.action, fingerprint}};
      }
      if (body.operation === 'transfer_candidates') return {revision: state.revision,
        data: {total: 1, next: null, candidates: [candidate()]}};
      return {revision: 'original-revision', data: {fingerprint,
        changes: body.parameters.transactionIds.map(id => ({before: leg(id)}))}};
    },
  });
  const list = get('[data-transfer-list]');
  return {get, calls, state, root, panel, list, candidate, control, find: () => get('[data-transfer-find]').events.click(),
    message: () => get('[data-transfer-message]').textContent,
    pairButton: () => list.children[0].children.at(-1).children.at(-1),
    confirm: () => list.children.at(-1).children.at(-1)};
}

test('no initial read and drafts block opening Transfers', async () => {
  const empty = harness({present: false});
  assert.equal(await empty.control.flush(), true);
  empty.control.dispose();
  const h = harness();
  assert.equal(h.calls.length, 0);
  h.state.before = false;
  await h.find();
  assert.equal(h.calls.length, 0);
  assert.equal(h.control.open, false);
  assert.notEqual(h.panel.open, true);
});

test('suggestions show evidence and exact preview leaves both records alone until confirmed', async () => {
  const h = harness();
  await h.find();
  assert.equal(h.panel.open, true);
  assert.equal(h.state.focus, h.get('h2'));
  assert.match(h.list.children[0].children[1].children[0].textContent, /Bank transfer hint · Other possible counterparts/);
  const from = h.list.children[0].children[0].children[0];
  assert.equal(from.children[0].children[1].textContent, '旅行 🍏');
  assert.equal(from.children[1].children[1].textContent, '<script> refund');
  assert.equal(from.children[2].children[0].textContent, '−$1,000');
  assert.deepEqual(from.children[2].children[0].dataset, {privateAmount: '', privateCurrency: 'USD'});
  await h.pairButton().onclick();
  assert.equal(h.control.dirty, false);
  assert.equal(h.calls[1].body.operation, 'preview_transfer_match');
  const preview = h.list.children[1].children[0];
  assert.equal(preview.children[2].children[0].textContent, '−$1,000.49');
  assert.equal(preview.children.at(-1).textContent, '[YNAB]\n  exact');
  assert.match(h.message(), /separate review/);
  assert.equal(h.calls.filter(call => call.url.endsWith('/apply')).length, 0);
  await h.confirm().onclick();
  assert.equal(h.state.saved, 1);
  assert.equal(h.calls.at(-1).body.requestId, 'intent-1');
  assert.equal(h.state.focus, h.get('[data-transfer-find]'));
  assert.equal(h.control.dirty, false);
});

test('one selected row searches all accounts and keeps only anchored counterparts across pages', async () => {
  const h = harness({rows: [{id: 'out', accountId: 'selected', date: '2026-10-31', amount: 100049, currency: 'USD'}]});
  h.state.read = body => {
    if (body.parameters.offset === 0) return {revision: 'r', data: {next: 100, candidates: [h.candidate(['other-out', 'other-in'])]}};
    return {revision: 'r', data: {next: null, candidates: [h.candidate()]}};
  };
  await h.get('[data-transfer-selected]').events.click();
  assert.equal(h.get('[data-transfer-manual]').attributes['aria-pressed'], 'true');
  assert.equal(h.calls.length, 2);
  for (const {body} of h.calls) {
    assert.deepEqual([...body.parameters.accountIds], ['selected']);
    assert.equal(body.parameters.since, '2026-10-31');
    assert.equal(body.parameters.until, '2026-10-31');
  }
  assert.match(h.message(), /1 possible counterpart/);
  assert.equal(h.list.children.length, 1);
  h.state.read = null;
  await h.pairButton().onclick();
  assert.deepEqual([...h.calls.at(-1).body.parameters.transactionIds], ['out', 'in']);
});

test('confirmation uses the ledger currency precision instead of browser currency defaults', async () => {
  const h = harness({currency: 'IQD'});
  await h.find();
  await h.pairButton().onclick();
  const money = h.list.children[1].children[0].children[2];
  assert.equal(money.children[0].textContent, '−100.049');
  assert.equal(money.children[0].dataset.privateCurrency, 'IQD');
  assert.equal(money.children[1].textContent, 'IQD');
});

test('two selected rows preview their exact IDs directly and allow explicit zeros', async () => {
  const h = harness({rows: [{id: 'out', amount: 0}, {id: 'in', amount: 0}]});
  await h.find();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].body.operation, 'preview_transfer_match');
  assert.deepEqual([...h.calls[0].body.parameters.transactionIds], ['out', 'in']);
  assert.equal(h.list.children[1].children[0].children[0].children[0].textContent, 'First entry');
  assert.equal(h.list.children[1].children[1].children[0].children[0].textContent, 'Second entry');
  h.list.children.at(-1).children[0].onclick();
  assert.equal(h.panel.open, false);
  assert.equal(h.state.saved, 0);
});

test('missing selection and ineligible one-row matching explain the next action', async () => {
  const h = harness();
  await h.find();
  await h.get('[data-transfer-manual]').events.click();
  assert.match(h.message(), /Select one transaction/);
  assert.match(h.list.children[0].textContent, /Add transaction/);
  assert.equal(h.calls.length, 1);
  for (const [row, reason] of [[{pending: true}, /pending/], [{matched: true}, /already matched/],
    [{amount: 0}, /select both/], [{amount: 1, categoryId: 'expense', categoryKind: 'expense'}, /Clear that category first/]]) {
    const blocked = harness({rows: [row]});
    await blocked.find();
    assert.match(blocked.message(), reason);
    assert.equal(blocked.calls.length, 0);
  }
});

test('uncertain and unverifiable saves block dismissal and keep exact intent until explicit retry', async () => {
  const h = harness();
  await h.find();
  await h.pairButton().onclick();
  const button = h.confirm();
  h.state.wrongReceipt = true;
  await button.onclick();
  const original = h.calls.at(-1).body;
  assert.match(h.message(), /may have saved/);
  assert.equal(h.control.dirty, true);
  assert.equal(await h.control.flush(), false);
  h.get('[data-transfer-close]').events.click();
  h.panel.events.cancel({preventDefault() {}});
  h.panel.events.click({target: h.panel});
  assert.equal(h.panel.open, true);
  assert.equal(h.calls.length, 3);
  h.state.wrongReceipt = false;
  h.state.fail = 'transfer_revision_conflict';
  await button.onclick();
  assert.equal(h.control.dirty, true);
  h.state.fail = null;
  await button.onclick();
  assert.deepEqual(h.calls.at(-1).body, original);
  assert.equal(h.state.saved, 1);
});

test('a definite stale write requires a new preview before confirming', async () => {
  const h = harness();
  await h.find();
  await h.pairButton().onclick();
  h.state.fail = 'transfer_revision_conflict';
  await h.confirm().onclick();
  assert.equal(h.control.dirty, false);
  assert.match(h.message(), /saved no changes/);
  h.state.fail = null;
  await h.confirm().onclick();
  assert.equal(h.calls.at(-1).body.operation, 'preview_transfer_match');
  await h.confirm().onclick();
  assert.equal(h.calls.at(-1).body.requestId, 'intent-2');
});

test('suggestions never mix revisions when loading more', async () => {
  const h = harness();
  h.state.read = body => ({revision: body.parameters.offset ? 'r2' : 'r1', data: {total: 2, next: 8, candidates: [h.candidate()]}});
  await h.find();
  await h.get('[data-transfer-more]').events.click();
  assert.match(h.message(), /budget changed/);
  assert.equal(h.list.children[0].textContent, 'Refresh suggestions');
});

test('row unmatch opens exact preview and Escape returns focus without mutation', async () => {
  const h = harness();
  const opener = {dataset: {transferPeer: 'in'}, closest: () => ({dataset: {transactionId: 'out'}}), focus() {h.state.focus = this;}};
  h.root.events.click({target: {closest: () => opener}, stopPropagation() {}});
  await tick();
  assert.equal(h.calls[0].body.parameters.action, 'unmatch');
  assert.equal(h.confirm().textContent, 'Confirm unmatch');
  let prevented = false;
  h.panel.events.cancel({preventDefault() {prevented = true;}});
  assert.equal(prevented, true);
  assert.equal(h.panel.open, false);
  assert.equal(h.state.focus, opener);
  assert.equal(h.calls.length, 1);
});

test('closing during a read invalidates its result and allows reopening', async () => {
  const h = harness();
  let finish;
  h.state.read = () => new Promise(resolve => {finish = resolve;});
  const reading = h.find();
  await tick();
  assert.equal(h.panel.open, true);
  h.get('[data-transfer-close]').events.click();
  h.state.read = null;
  await h.find();
  const shown = h.message();
  finish({revision: 'r', data: {total: 0, next: null, candidates: []}});
  await reading;
  assert.equal(h.message(), shown);
  assert.equal(h.list.children.length, 1);
  h.control.dispose();
});
