const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/transfer-matches.js'), 'utf8');
function harness({present = true} = {}) {
  const nodes = new Map(), calls = [], state = {before:true, fail:null, saved:0, revision:'r', next:null};
  function node() {return {children:[], hidden:false, disabled:false, textContent:'', dataset:{}, events:{}, append(...items) {this.children.push(...items);}, replaceChildren() {this.children=[];}, focus() {}, addEventListener(name, callback) {this.events[name]=callback;}, removeEventListener(name) {delete this.events[name];}};}
  const root = node();
  root.querySelector = selector => !present ? null : (nodes.has(selector) ? nodes.get(selector) : (nodes.set(selector,node()),nodes.get(selector)));
  const panel = root.querySelector('[data-transfer-panel]');
  if (panel) panel.querySelector = selector => root.querySelector(selector);
  const context = {document:{createElement:node}, Intl, crypto:{randomUUID:()=> 'original'}, Error};
  vm.runInNewContext(source.replace('export function ', 'function '),context);
  const leg = id => ({id, accountName:'旅行 🍏', date:'2026-11-01', merchant:'<script> refund', amount:id==='out'?100:-100, currency:'USD', note:'[YNAB]\n  exact'});
  const control = context.initTransferMatches({root, context:()=>({workspace:'personal', currency:'USD', selectedAccountIds:['a'], startDate:'2026-11-01', endDate:'2026-11-30'}), before:async()=>state.before, saved:async()=>{assert.equal(await control.flush(),true);state.saved++;}, api:async(url,body)=>{
    calls.push({url,body});
    if (state.fail) {const failure=new Error('Unknown outcome');failure.code=state.fail;throw failure;}
    if (url.endsWith('/apply')) return {revision:'saved',data:{count:2}};
    if (body.operation==='transfer_candidates') return {revision:state.revision,data:{total:1,next:state.next,candidates:[{transactionIds:['out','in'],transactions:[leg('out'),leg('in')],daysApart:0,bankHint:true,reason:'Equal opposite amounts',ambiguous:true}]}};
    return {revision:'original-revision',data:{fingerprint:'a'.repeat(64),changes:['out','in'].map(id=>({before:leg(id)}))}};
  }});
  return {nodes,calls,state,root,control,find:()=>nodes.get('[data-transfer-find]').events.click(),list:()=>nodes.get('[data-transfer-list]'),message:()=>nodes.get('[data-transfer-message]').textContent};
}
test('no initial read, absent controls stay inert, blocked edits do not propose',async()=>{
  const empty=harness({present:false});assert.equal(await empty.control.flush(),true);empty.control.dispose();assert.equal(empty.control.dirty,false);
  const h=harness();assert.equal(h.calls.length,0);h.state.before=false;await h.find();assert.equal(h.calls.length,0);
});
test('suggestions and exact preview never write; literal merchant text and memo stay visible',async()=>{
  const h=harness();await h.find();assert.equal(h.calls.length,1);assert.match(h.list().children[0].children[0].textContent,/multiple alternatives/);
  assert.match(h.list().children[0].children[1].children[1].textContent,/<script>/);
  await h.list().children[0].children.at(-1).onclick();assert.equal(h.calls.length,2);assert.match(h.list().children[0].children.at(-1).textContent,/\[YNAB\]/);
  assert.equal(h.control.dirty,false);assert.equal(h.calls[1].body.operation,'preview_transfer_match');
  await h.list().children.at(-1).onclick();assert.equal(h.state.saved,1);assert.equal(h.calls.at(-1).body.requestId,'original');assert.equal(h.control.dirty,false);
});
test('uncertain save blocks navigation, never retries automatically, exact explicit retry is retained',async()=>{
  const h=harness();await h.find();await h.list().children[0].children.at(-1).onclick();const button=h.list().children.at(-1);h.state.fail='lost';await button.onclick();assert.equal(h.control.dirty,true);assert.equal(await h.control.flush(),false);assert.match(h.message(),/may have committed/);
  const original=h.calls.at(-1).body;await h.find();assert.equal(h.calls.length,3);h.state.fail=null;await button.onclick();assert.deepEqual(h.calls.at(-1).body,original);assert.equal(h.state.saved,1);
});
test('stale rejection requires fresh preview; pagination refuses mixed revisions',async()=>{
  const h=harness();h.state.next=1;await h.find();h.state.revision='changed';await h.nodes.get('[data-transfer-more]').events.click();assert.match(h.message(),/between pages/);
  await h.list().children[0].children.at(-1).onclick();h.state.fail='transfer_revision_conflict';await h.list().children.at(-1).onclick();assert.equal(h.control.dirty,false);assert.match(h.message(),/Nothing was applied/);h.state.fail=null;await h.list().children.at(-1).onclick();assert.equal(h.calls.at(-1).body.operation,'preview_transfer_match');
  h.control.dispose();
});
test('row unmatch action previews only and close dismisses without mutations',async()=>{
  const h=harness();const button={dataset:{transferPeer:'in'},closest:()=>({dataset:{transactionId:'out'}})};h.root.events.click({target:{closest:()=>button},stopPropagation(){}});await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls[0].body.parameters.action,'unmatch');assert.equal(h.calls.length,1);
  h.nodes.get('[data-transfer-close]').events.click();assert.equal(h.nodes.get('[data-transfer-panel]').hidden,true);
});
