const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/whab_plugin_web/static/sidebar.js'), 'utf8');
function harness({saved=null, compact=false, blocked=false, present=true}={}) {
  const events={}, windowEvents={}, mediaEvents={}, writes=[], dataset={}, attrs={};
  const button={hidden:true,title:'',setAttribute:(k,v)=>{attrs[k]=v;}};
  const media={matches:compact,addEventListener:(k,fn)=>{mediaEvents[k]=fn;}};
  const document={documentElement:{dataset},querySelectorAll:()=>present?[button]:[],addEventListener:(k,fn)=>{events[k]=fn;}};
  const window={matchMedia:()=>media,localStorage:{getItem(){if(blocked)throw Error('unavailable');return saved;},setItem(k,v){if(blocked)throw Error('unavailable');writes.push([k,v]);}},addEventListener:(k,fn)=>{windowEvents[k]=fn;}};
  vm.runInNewContext(source,{document,window});
  return {button,attrs,dataset,writes,events,media,mediaEvents,windowEvents,click:()=>events.click({target:{closest:()=>button}})};
}
test('desktop expands and compact collapses before styles without writing defaults',()=>{
  for(const compact of [false,true]) {const h=harness({compact});assert.equal(h.dataset.sidebar,compact?'collapsed':'expanded');assert.equal(h.attrs['aria-expanded'],String(!compact));assert.equal(h.button.hidden,false);assert.equal(h.writes.length,0);h.events.DOMContentLoaded();}
});
test('explicit choice persists and restores across refresh and viewport changes',()=>{
  const h=harness();h.click();assert.equal(h.dataset.sidebar,'collapsed');assert.equal(h.attrs['aria-label'],'Expand sidebar');assert.deepEqual(h.writes,[['whab.sidebar','collapsed']]);h.media.matches=true;h.mediaEvents.change();h.click();assert.equal(h.attrs['aria-label'],'Collapse sidebar');h.media.matches=false;h.mediaEvents.change();assert.equal(h.dataset.sidebar,'expanded');assert.equal(harness({saved:'collapsed'}).dataset.sidebar,'collapsed');assert.equal(harness({saved:'expanded',compact:true}).dataset.sidebar,'expanded');
});
test('default follows viewport until a choice is saved; invalid values and unrelated clicks are ignored',()=>{
 const h=harness({saved:'broken'});h.media.matches=true;h.mediaEvents.change();assert.equal(h.dataset.sidebar,'collapsed');h.events.click({target:{closest:()=>null}});assert.equal(h.writes.length,0);
});
test('storage failure keeps toggle usable; absent controls remain safe',()=>{
 const h=harness({blocked:true});h.click();assert.equal(h.dataset.sidebar,'collapsed');assert.equal(h.writes.length,0);const absent=harness({present:false});absent.events.DOMContentLoaded();assert.equal(absent.dataset.sidebar,'expanded');
});
test('storage events synchronize layout and labels; removing choice restores responsive default',()=>{
 const h=harness();h.windowEvents.storage({key:'other',newValue:'collapsed'});h.windowEvents.storage({key:'whab.sidebar',newValue:'invalid'});assert.equal(h.dataset.sidebar,'expanded');h.windowEvents.storage({key:'whab.sidebar',newValue:'collapsed'});assert.equal(h.attrs['aria-expanded'],'false');h.windowEvents.storage({key:'whab.sidebar',newValue:null});assert.equal(h.dataset.sidebar,'expanded');
});
