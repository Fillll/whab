const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/whab_plugin_web/static/transaction-category-batch.js'),'utf8');
const rows=()=>['a','b','c'].map(id=>({dataset:{transactionId:id}}));
function harness(){
 const calls=[],errors=[],saved=[],restored=[],states=[],state={fault:null,before:true,gate:null,previewBad:false};
 const context={crypto:{randomUUID:()=> 'original-request'},Set,Error};
 vm.runInNewContext(source.replace('export function ','function '),context);
 const controller=context.createCategoryBatch({workspace:'personal',before:async()=>state.before,restore:r=>restored.push(r),state:s=>states.push(s),error:e=>errors.push(e),saved:(...a)=>saved.push(a),api:async(url,body)=>{
  calls.push({url,body:JSON.parse(JSON.stringify(body))});
  if(state.gate)await state.gate;
  if(url.endsWith('/preview')&&state.previewBad)return{revision:'r',data:{count:3,changes:[]}};
  if(!url.endsWith('/preview')&&state.fault){const e=new Error('Save unknown');e.code=state.fault;throw e;}
  return {revision:'r',data:{count:body.transactionIds.length,changes:body.transactionIds.map(id=>({id,after:{id,categoryId:body.categoryId}}))}};
 }});
 return{controller,calls,errors,saved,restored,states,state};
}
test('one explicit selection previews then commits once, without individual requests',async()=>{
 const h=harness();assert.equal(await h.controller.apply(rows(),'food',null),true);assert.equal(h.calls.length,2);assert.equal(h.calls[0].url,'/transactions/categories/preview');assert.deepEqual(h.calls[1].body,{workspace:'personal',transactionIds:['a','b','c'],categoryId:'food',revision:'r',requestId:'original-request'});assert.equal(h.saved.length,1);assert.equal(h.controller.dirty,false);assert.equal(await h.controller.flush(),true);
});
test('unknown responses keep the exact request and block another selection until explicit retry',async()=>{
 const h=harness();h.state.fault='lost';assert.equal(await h.controller.apply(rows(),null,null),false);const intent=h.calls.at(-1).body;assert.equal(h.controller.dirty,true);await h.controller.apply(rows(),'other',null);assert.equal(h.calls.length,2);h.state.fault='category_revision_conflict';assert.equal(await h.controller.flush(),false);assert.equal(h.controller.dirty,true);h.state.fault=null;assert.equal(await h.controller.flush(),true);assert.deepEqual(h.calls.at(-1).body,intent);assert.equal(h.calls.filter(c=>c.url.endsWith('/preview')).length,1);
});
test('first stale refusal releases the selection without replaying or applying records',async()=>{
 const h=harness();h.state.fault='category_revision_conflict';await h.controller.apply(rows(),'food',null);assert.equal(h.controller.dirty,false);assert.equal(h.saved.length,0);assert.match(h.errors[0],/Refresh/);assert.equal(h.restored.length,1);
});
test('invalid, duplicate and oversized selections never split or submit',async()=>{
 for(const r of [[],[...rows(),...rows()],Array.from({length:101},(_,i)=>({dataset:{transactionId:String(i)}}))]){const h=harness();assert.equal(await h.controller.apply(r,'food',null),false);assert.equal(h.calls.length,0);assert.match(h.errors[0],/1–100/);}
});
test('pending row failures and malformed previews refuse a category mutation',async()=>{
 const h=harness();h.state.before=false;await h.controller.apply(rows(),'food',null);assert.equal(h.calls.length,0);h.state.before=true;h.state.previewBad=true;await h.controller.apply(rows(),'food',null);assert.equal(h.calls.length,1);assert.equal(h.saved.length,0);assert.equal(h.controller.dirty,false);
});
test('concurrent actions and disposal cannot launch another write',async()=>{
 const h=harness();let release;h.state.gate=new Promise(resolve=>{release=resolve;});const first=h.controller.apply(rows(),'food',null);await Promise.resolve();await h.controller.apply(rows(),'other',null);assert.equal(h.controller.dirty,true);h.controller.dispose();release();assert.equal(await first,false);assert.equal(h.calls.length,1);assert.equal(h.saved.length,0);assert.equal(await h.controller.apply(rows(),'food',null),false);
});
