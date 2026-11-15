const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const encoded = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const grid = encoded(readFileSync(resolve(__dirname,'../src/whab_plugin_web/static/transaction-grid.js'),'utf8'));
const source = readFileSync(resolve(__dirname,'../src/whab_plugin_web/static/subscription-plans.js'),'utf8').replace('./transaction-grid.js',grid);
const load = () => import(encoded(source));
const rows = [
  {id:'annual',merchant:'Fictional Mobile',category:'Connectivity',account:'Card',amount:32300,cadence:'annual',monthly:2692,annual:32300,occurrences:3,occurrencesLast12Months:1,date:'2026-10-15'},
  {id:'monthly',merchant:'Fictional Music',category:'Entertainment',account:'Checking',amount:1700,cadence:'monthly',monthly:1700,annual:20400,occurrences:24,occurrencesLast12Months:12,date:'2026-10-28'},
  {id:'unknown',merchant:'Fictional Hosting',category:'Software',account:'Card',amount:500,cadence:'unknown',monthly:0,annual:0,occurrences:1,occurrencesLast12Months:0,date:'2025-04-05'}
];

test('all plan columns sort numerically or chronologically without mutating reports',async()=>{
  const {selectPlans,createPlanTableState,planColumns}=await load(),state=createPlanTableState();
  for (const column of planColumns) {
    state.sortKey=column.key;state.descending=false;
    const ascending=selectPlans(rows,state);
    assert.equal(ascending.length,3);
    state.descending=true;const descending=selectPlans(rows,state);
    if (['monthly','annual'].includes(column.key)) assert.equal(descending.at(-1).id,'unknown');
    else assert.deepEqual(ascending.map(r=>r[column.key]),descending.map(r=>r[column.key]).reverse());
  }
  state.sortKey='occurrences';state.descending=true;assert.deepEqual(selectPlans(rows,state).map(r=>r.occurrences),[24,3,1]);
  state.sortKey='date';assert.equal(selectPlans(rows,state)[0].date,'2026-10-28');
  assert.deepEqual(rows.map(r=>r.id),['annual','monthly','unknown']);
});

test('text, account, billing frequency and inclusive amount filters combine across columns',async()=>{
  const {selectPlans,createPlanTableState}=await load(),state=createPlanTableState();
  state.filters={merchant:{query:'FICTIONAL'},account:{query:'card'},amount:{min:'323',max:'323.00',cadence:'annual'}};
  assert.deepEqual(selectPlans(rows,state).map(r=>r.id),['annual']);
  state.filters.category={query:'entertainment'};assert.deepEqual(selectPlans(rows,state),[]);
  state.filters={amount:{cadence:'unknown'}};assert.equal(selectPlans(rows,state)[0].id,'unknown');
});

test('monthly, annual, charge-count and date ranges include endpoints and keep unknowns out',async()=>{
  const {selectPlans,createPlanTableState}=await load(),state=createPlanTableState();
  state.filters={monthly:{min:'17',max:'17.00'},annual:{min:'204',max:'204'},occurrences:{min:'24',max:'24'},occurrencesLast12Months:{min:'12',max:'12'},date:{min:'2026-10-28',max:'2026-10-28'}};
  assert.deepEqual(selectPlans(rows,state).map(r=>r.id),['monthly']);
  state.filters={monthly:{min:'0'}};assert.equal(selectPlans(rows,state).length,2);
  state.filters={occurrencesLast12Months:{min:'0',max:'0'}};assert.equal(selectPlans(rows,state)[0].id,'unknown');
});

test('filters use currency minor-unit precision and report refresh retains chosen ordering/filters',async()=>{
  const {selectPlans,createPlanTableState}=await load(),state=createPlanTableState();
  state.filters={amount:{min:'32300',max:'32300'}};assert.equal(selectPlans(rows,state,0)[0].id,'annual');
  state.filters={amount:{min:'1.700',max:'1.700'}};assert.equal(selectPlans(rows,state,3)[0].id,'monthly');
  state.filters={amount:{min:'.50',max:'5.00'}};assert.equal(selectPlans(rows,state)[0].id,'unknown');
  state.filters={account:{query:'card'}};state.sortKey='occurrences';state.descending=false;
  assert.deepEqual(selectPlans([...rows,{...rows[0],id:'new',occurrences:2}],state).map(r=>r.id),['unknown','new','annual']);
  state.filters={};assert.equal(selectPlans(rows,state).length,3);
});

test('older report fields are unavailable rather than fabricated and sort after known values',async()=>{
  const {selectPlans,createPlanTableState}=await load(),state=createPlanTableState();
  const legacy={...rows[0],id:'legacy'};delete legacy.annual;delete legacy.occurrencesLast12Months;
  state.sortKey='annual';assert.equal(selectPlans([legacy,rows[1]],state).at(-1).id,'legacy');
  state.filters={occurrencesLast12Months:{min:'0'}};assert.deepEqual(selectPlans([legacy],state),[]);
});

test('plan rendering marks monetary cells and range editors without masking dates or charge counts', async () => {
  const {renderSubscriptionPlans, createPlanTableState} = await load();
  const previousDocument = global.document, previousFrame = global.requestAnimationFrame;
  class Element {
    constructor(tag) {this.tagName=tag; this.children=[]; this.dataset={}; this.style={}; this.events={}; this.attributes={}; this.classList={add(){},toggle(){}};}
    append(...nodes) {this.children.push(...nodes);}
    replaceChildren(...nodes) {this.children=nodes;}
    setAttribute(name,value) {this.attributes[name]=value;}
    addEventListener(name,handler) {this.events[name]=handler;}
    querySelector(selector) {const value = /option\[value="(.*)"\]/.exec(selector)?.[1]; return this.children.find(node=>node.value===value);}
    set textContent(value) {this.text=String(value); this.children=[];}
    get textContent() {return (this.text || '') + this.children.map(node=>node.textContent).join('');}
  }
  global.document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text})};
  global.requestAnimationFrame=callback=>callback();
  try {
    const parent = new Element('section'), state=createPlanTableState();
    renderSubscriptionPlans(parent,{subscriptions:rows},state,{format:value=>`$${(value/100).toFixed(2)}`,digits:2});
    const body = parent.children[2].children[0].children[2];
    const annual = body.children[0];
    assert.equal(annual.children[3].children[0].dataset.privateAmount,'');
    assert.equal(annual.children[3].textContent,'$323.00 / year');
    assert.equal(annual.children[3].dataset.privateAmountAttributes,'title');
    assert.equal(annual.children[4].children[0].dataset.privateAmount,'');
    assert.equal(annual.children[5].children[0].dataset.privateAmount,'');
    assert.equal(annual.children[6].textContent,'3');
    assert.deepEqual(annual.children[6].dataset,{});
    assert.equal(annual.children[8].textContent,'2026-10-15');
    assert.deepEqual(annual.children[8].dataset,{});
    const choice=parent.children[0].children[0].children[0];
    choice.value='amount'; choice.events.change();
    const controls=parent.children[0].children[1];
    assert.equal(controls.children[0].children[0].dataset.privateAmount,'');
    assert.equal(controls.children[1].children[0].dataset.privateAmount,'');
    assert.equal(controls.children[0].children[0].value,'');
  } finally {global.document=previousDocument; global.requestAnimationFrame=previousFrame;}
});
