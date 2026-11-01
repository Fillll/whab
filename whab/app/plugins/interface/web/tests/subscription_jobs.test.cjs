const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const source = readFileSync(resolve(__dirname, '../src/whab_plugin_web/static/subscription-jobs.js'), 'utf8');
const load = () => import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const state = (changes = {}) => ({version:1,id:'job',status:'running',total:100,processed:20,classified:10,reused:10,skipped:0,error:'',...changes});
const deferred = () => {let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const settle = async () => {for(let i=0;i<5;i++) await new Promise(resolve=>setImmediate(resolve));};
async function fixture(call) {
  const module = await load(), changes = [], completed = [], errors = [], timers = new Map();
  let timerId = 0, owner = 'first-budget';
  const controller = module.createSubscriptionJobs({call,scope:()=>owner,onChange:job=>changes.push(job),onComplete:job=>completed.push(job),onError:error=>errors.push(error),newId:()=> 'intent',setTimer:callback=>{timers.set(++timerId,callback);return timerId;},clearTimer:id=>timers.delete(id)});
  async function tick() {const [id,callback]=timers.entries().next().value; timers.delete(id); callback(); await settle();}
  return {controller,changes,completed,errors,timers,tick,scope:value=>{owner=value;}};
}

test('closing/discarding the observer never sends Stop and a reopened observer sees durable progress', async()=>{
  const requests=[];
  let server=state();
  const call=async(action,values)=>{requests.push({action,...values});return server;};
  const ui=await fixture(call);
  await ui.controller.start({model:'fictional'});
  assert.equal(requests[0].action,'start'); assert.equal(requests[0].operationId,'intent');
  assert.equal(ui.changes[0].status,'preparing');
  // Navigation/browser teardown disposes observation only, while server work advances.
  ui.controller.dispose();
  assert.equal(ui.timers.size,0); assert.equal(requests.some(row=>row.action==='stop'),false);
  server=state({processed:80,classified:70});
  const reopened=await fixture(call); await reopened.controller.refresh();
  assert.equal(reopened.changes.at(-1).processed,80);
  server=state({status:'completed',processed:100,classified:90});
  await reopened.tick();
  assert.equal(reopened.completed.length,1); assert.equal(reopened.timers.size,0);
});

test('Stop targets the observed job and keeps polling until the current batch finishes', async()=>{
  const requests=[];
  const ui=await fixture(async(action,values)=>{requests.push({action,...values});return state({status:action==='stop'?'stopping':'stopped'});});
  ui.controller.accept(state());
  await ui.controller.stop();
  assert.deepEqual(requests[0],{action:'stop',jobId:'job'});
  await ui.tick();
  assert.equal(ui.completed[0].status,'stopped');
  const before=requests.length; await ui.controller.stop(); assert.equal(requests.length,before);
});

test('lost Start replies only read status, retain intent and never start another paid request automatically', async()=>{
  const requests=[];
  const ui=await fixture(async(action,values)=>{requests.push({action,...values});if(action==='start')throw new Error('lost reply');return state({id:'intent'});});
  await assert.rejects(ui.controller.start(),/lost reply/); await settle();
  assert.deepEqual(requests.map(row=>row.action),['start','job']);
  assert.equal(ui.changes.at(-1).id,'intent');
  assert.equal(ui.timers.size,1);
});

test('transient polling errors preserve running work and retry reads without another Start', async()=>{
  let fail=true; const actions=[];
  const ui=await fixture(async(action)=>{actions.push(action);if(fail)throw new Error('temporarily unavailable');return state({status:'completed',processed:100,classified:90});});
  ui.controller.accept(state()); await ui.tick();
  assert.equal(ui.errors.length,1); assert.equal(ui.timers.size,1);
  fail=false;await ui.tick();assert.deepEqual(actions,['job','job']);assert.equal(ui.completed.length,1);
});

test('late polls cannot replace a newer status or another budget', async()=>{
  const pending=deferred();const ui=await fixture(()=>pending.promise);
  const read=ui.controller.refresh();
  ui.controller.accept(state({processed:90,classified:80}));
  pending.resolve(state({processed:20}));await read;
  assert.equal(ui.changes.at(-1).processed,90);
  const second=deferred(), changed=await fixture(()=>second.promise);
  const otherRead=changed.controller.refresh();changed.scope('second-budget');second.resolve(state());await otherRead;
  assert.equal(changed.changes.length,0);
});

test('late Start and Stop responses are ignored after navigation/disposal', async()=>{
  const start=deferred(), ui=await fixture(()=>start.promise);
  const request=ui.controller.start();ui.controller.dispose();start.resolve(state());await request;
  assert.equal(ui.changes.length,1);assert.equal(ui.timers.size,0);
  const stop=deferred(), stopping=await fixture(()=>stop.promise);stopping.controller.accept(state());
  const stopRequest=stopping.controller.stop();stopping.controller.dispose();stop.resolve(state({status:'stopping'}));await stopRequest;
  assert.equal(stopping.changes.length,1);
});

test('failed Stop remains observable and keeps reading the same running job', async()=>{
  const ui=await fixture(async()=>{throw new Error('stop reply lost');});ui.controller.accept(state());
  await assert.rejects(ui.controller.stop(),/stop reply lost/);assert.equal(ui.timers.size,1);
});

test('progress is based on saved transaction counts, handles preparation, skips and zero-work completion',async()=>{
  const {renderSubscriptionProgress,classificationRunning}=await load();
  const nodes=new Map();
  const panel={hidden:false,querySelector:selector=>{if(!nodes.has(selector))nodes.set(selector,{textContent:'',attributes:{},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];}});return nodes.get(selector);}};
  renderSubscriptionProgress(panel,null);assert.equal(panel.hidden,true);
  renderSubscriptionProgress(panel,state({status:'preparing'}));assert.match(nodes.get('[data-subscriptions-job-count]').textContent,/Preparing/);
  renderSubscriptionProgress(panel,state({processed:50,classified:35,reused:10,skipped:5}));
  assert.equal(nodes.get('progress').max,100);assert.equal(nodes.get('progress').value,50);
  assert.match(nodes.get('[data-subscriptions-job-count]').textContent,/50 of 100.*10 reused.*5 changed/);
  assert.match(nodes.get('[data-subscriptions-job-help]').textContent,/leave this page or close the browser tab/);
  renderSubscriptionProgress(panel,state({status:'completed',total:0,processed:0}));assert.equal(nodes.get('progress').value,1);
  renderSubscriptionProgress(panel,state({status:'interrupted'}));assert.match(nodes.get('[data-subscriptions-job-help]').textContent,/Completed batches are saved/);
  renderSubscriptionProgress(panel,state({status:'failed',error:'Fictional provider limit'}));assert.equal(nodes.get('[data-subscriptions-job-help]').textContent,'Fictional provider limit');
  assert.equal(classificationRunning(state()),true);assert.equal(classificationRunning(state({status:'completed'})),false);
});
