const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/budget-history.js'), 'utf8');
function harness({present = true, saved = null} = {}) {
  const nodes = new Map(), events = new Map(), calls = [], storage = new Map();
  function node() {return {children:[], dataset:{}, hidden:false, disabled:false, textContent:'', append(...items) {this.children.push(...items);}, replaceChildren() {this.children=[];}, setAttribute() {}, focus() {}, addEventListener(name, fn) {this[name]=fn;}};}
  const root = node(); root.hidden = true;
  root.querySelector = key => {if (!nodes.has(key)) nodes.set(key,node()); return nodes.get(key);};
  root.querySelectorAll = () => [...nodes.values()]; root.addEventListener = (key, fn) => events.set(key,fn);
  const document = {querySelector:()=>present ? root : null, createElement:node};
  const context = {document, sessionStorage:{getItem:()=>saved, setItem:(k,v)=>storage.set(k,v), removeItem:k=>storage.delete(k)}, crypto:{randomUUID:()=> 'original-request'}, Date, JSON};
  vm.runInNewContext(source.replaceAll('export function ', 'function '), context);
  const state = {fail:false, gate:null};
  const control = context.initBudgetHistory({document, getContext:()=>({budgetUrl:'/fictional',workspace:'personal'}), beforeChange:async()=>{}, api:async(url,body)=> {
    calls.push({url,body});
    if (body && url.endsWith('/apply')) {if (state.gate) await state.gate; if (state.fail) {const failure = new Error('Unknown outcome'); failure.code = state.code; throw failure;} return {};}
    if (body) return {revision:'original-revision',data:{fingerprint:'a'.repeat(64),changes:[{path:['transactions','fictional'],before:{categoryId:null,merchant:'<script>'},after:{categoryId:'food',merchant:'<script>'}}]}};
    return {data:{entries:[],next:null}};
  }});
  return {control,calls,state,nodes,storage,root,context};
}
test('absent history panel does not start background reads',()=> {assert.equal(harness({present:false}).control,null);});
test('preview performs no apply and renders payees as plain text',async()=> {
  const h = harness(); await h.control.prepare('undo','batch');
  assert.equal(h.calls.length,1); assert.ok(h.calls[0].url.endsWith('/preview'));
  assert.match(h.nodes.get('[data-history-changes]').children[0].children[0].textContent, /<script>/);
});
test('one explicit apply, double-click guard, and exact retained retry',async()=> {
  const h = harness(); await h.control.prepare('undo','batch');
  h.state.fail = true;
  await Promise.all([h.control.commit(),h.control.commit()]);
  const first = h.calls.filter(c=>c.url.endsWith('/apply'));
  assert.equal(first.length,1); assert.equal(h.storage.size,1);
  assert.match(h.nodes.get('[data-history-error]').textContent,/Unknown outcome/);
  h.state.fail = false; await h.control.commit(true);
  const all = h.calls.filter(c=>c.url.endsWith('/apply'));
  assert.equal(all.length,2); assert.deepEqual(all[0].body,all[1].body); assert.equal(h.storage.size,0);
});
test('reloaded uncertain intent never submits automatically',async()=> {
  const h = harness({saved:JSON.stringify({workspace:'personal',action:'undo',target:'batch',fingerprint:'a'.repeat(64),revision:'old',requestId:'retained'})});
  await h.control.load(); assert.equal(h.calls.filter(c=>c.body).length,0);
  await h.control.commit(true); assert.equal(h.calls.find(c=>c.body).body.requestId,'retained');
});

test('known first revision rejection releases preview but an uncertain retry retains its original intent',async()=> {
  const h = harness(); await h.control.prepare('undo','batch');
  h.state.fail = true; h.state.code = 'history_revision_conflict';
  await h.control.commit(); assert.equal(h.storage.size,0);
  await h.control.prepare('undo','batch'); h.state.code = null;
  await h.control.commit(); assert.equal(h.storage.size,1);
  h.state.code = 'history_revision_conflict'; await h.control.commit(true); assert.equal(h.storage.size,1);
});
