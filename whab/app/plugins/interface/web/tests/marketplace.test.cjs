const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/marketplace.js'), 'utf8');
class Node {
  constructor() {this.children = []; this.dataset = {}; this.events = {}; this.hidden = false;}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children = nodes;}
  setAttribute() {}
  removeAttribute() {}
  focus() {this.focused = true;}
  addEventListener(name, callback) {(this.events[name] ||= []).push(callback);}
  async emit(name) {await Promise.all((this.events[name] || []).map(callback => callback()));}
  all() {return this.children.flatMap(child => [child, ...child.all()]);}
}
function harness({workspace = 'personal', reply} = {}) {
  const dialog = new Node(), nodes = {}, requests = [];
  dialog.dataset.workspace = workspace;
  for (const name of ['list', 'error', 'status', 'review', 'packages', 'install', 'cancel']) nodes[name] = new Node();
  dialog.querySelector = selector => nodes[selector.match(/data-marketplace-(\w+)/)[1]];
  dialog.querySelectorAll = selector => [...nodes.list.all().filter(node => node.dataset.marketplaceAction), ...(selector.includes('install') ? [nodes.install] : [])];
  const context = {document: {querySelector: () => dialog, querySelectorAll: () => [], createElement: () => new Node()}};
  vm.runInNewContext(source.replaceAll('export ', '') + '\nthis.init = initMarketplace;', context);
  let completed = 0;
  const row = {id: 'fictional', display_name: '<fictional>', category: 'extension', version: '1.0.0', dependencies: [{id: 'provider', version: '>=1'}], compatible: true, installed_version: null};
  context.init({api: async (path, body) => {
    requests.push({path, body});
    if (reply) return reply(path, body);
    if (!body) return {plugins: [row]};
    if (path.endsWith('/review')) return {token: 'review', plugins: [{id: 'fictional', display_name: '<fictional>', version: '1.0.0', installed: false}], packages: []};
    return {installed: ['fictional']};
  }, onInstalled: () => {completed++;}});
  return {dialog, nodes, requests, get completed() {return completed;}, action: () => nodes.list.all().find(node => node.dataset.marketplaceAction)};
}
test('six sections and dependencies are reviewed before an explicit install', async () => {
  const h = harness();
  await h.dialog.emit('whab:dialog-open');
  assert.equal(h.nodes.list.children.length, 6);
  await h.action().emit('click');
  assert.equal(h.nodes.review.hidden, false);
  assert.equal(h.completed, 0);
  assert.equal(h.nodes.packages.children[0].textContent, '<fictional> 1.0.0');
  await h.nodes.install.emit('click');
  assert.equal(h.completed, 1);
  assert.equal(h.requests.at(-1).body.token, 'review');
  assert.equal(h.requests.some(request => request.path.includes('/management')), false);
});
test('demo browsing disables package installation', async () => {
  const h = harness({workspace: 'demo'});
  await h.dialog.emit('whab:dialog-open');
  assert.equal(h.action().disabled, true);
});
test('closing invalidates a late review and prevents installation', async () => {
  let resolve;
  const h = harness({reply: (path, body) => !body ? {plugins: [{id: 'fictional', category: 'interface', dependencies: [], compatible: true}]} : new Promise(done => {resolve = done;})});
  await h.dialog.emit('whab:dialog-open');
  const pending = h.action().emit('click');
  await new Promise(done => setImmediate(done));
  await h.dialog.emit('close');
  resolve({token: 'late', plugins: [], packages: []});
  await pending;
  assert.equal(h.nodes.review.hidden, true);
  assert.equal(h.nodes.install.disabled, true);
  await h.nodes.install.emit('click');
  assert.equal(h.completed, 0);
});
