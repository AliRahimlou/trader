import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {bindStrategyView,rangeFamilyView,rangeFamilyMarkup,portfolioView,STRATEGY_VIEW_KEY} from './strategy-families.mjs';

const at='2026-09-19T12:10:10+00:00',now=Date.parse(at);
function snapshot(){return {live_enabled:true,settings:{target_dollars:'5.00'},strategy_families:{range_reversal:{
  analyzed_at:at,observed_at:at,last_refresh_at:at,state:'SETUP_OBSERVED',source:'alpaca_crypto_us',symbol:'BTC/USD',
  detail:'Completed reversal observed.',latest_bar_at:'2026-09-19T12:10:00+00:00',
  range:{low:90,high:110,start_at:'2026-09-19T04:00:00+00:00',end_at:'2026-09-19T08:00:00+00:00'},
  current_event:{status:'CONFIRMED',current:true,direction:'long',entry:100,stop:90,target:120,confirmation_at:at},
  candidates:[{status:'CONFIRMED',direction:'long',entry:100,stop:90,target:120,confirmation_at:at}],
  interpretation_warnings:['Provisional candle anchor.'],archive_status:'recording'}}};}
function dom(){
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{id,hidden:false,value:'',textContent:'',innerHTML:'',listeners:{},
      addEventListener(type,fn){this.listeners[type]=fn;},querySelectorAll(){return [];}});
    return elements.get(id);
  };
  return {element,document:{baseURI:'https://example.test/pivot/',getElementById:element}};
}
test('view selection persists locally while every execution value stays unchanged',async()=>{
  const source=(await readFile(new URL('./app.js',import.meta.url),'utf8'))
    .replace(/^import .*?;\n/gm,'').replace('refresh();setInterval(refresh,10000);','');
  const h=dom(),writes=[],requests=[],saved=new Map(),initial=snapshot();
  const storage={getItem:key=>saved.get(key),setItem:(key,value)=>{saved.set(key,value);writes.push({key,value});}};
  const context=vm.createContext({URL,Date,bindStrategyView:(document,options)=>bindStrategyView(document,{...options,storage:()=>storage}),
    rangeFamilyView,rangeFamilyMarkup,portfolioView,document:h.document,location:{hostname:'example.test',port:''},
    fetch:(...args)=>{requests.push(args);throw Error('No network expected');}});
  vm.runInContext(source+`\nsnapshot=${JSON.stringify(initial)};globalThis.state=()=>({snapshot,dirty,saving,toggling,pendingLive});`,context);
  const before=JSON.stringify(context.state());
  for(const [selected,socratesHidden,rangeHidden] of [['range_reversal',true,false],['all',false,false],['socrates',false,true]]){
    h.element('strategy-view').value=selected;
    h.element('strategy-view').listeners.change();
    assert.equal(h.element('socrates-family').hidden,socratesHidden);
    assert.equal(h.element('range-family').hidden,rangeHidden);
    assert.equal(JSON.stringify(context.state()),before);
    assert.match(h.element('strategy-view-note').textContent,/Changing this view does not change trading/);
    if(selected==='all')assert.match(h.element('strategy-view-note').textContent,/does not change trading/);
  }
  assert.equal(requests.length,0);
  assert.deepEqual(writes.map(row=>row.key),Array(3).fill(STRATEGY_VIEW_KEY));
  assert.equal(saved.get(STRATEGY_VIEW_KEY),'socrates');
});
test('unavailable storage still permits local switching without affecting live controls',()=>{
  const h=dom();
  bindStrategyView(h.document,{storage:()=>{throw Error('Blocked storage');}});
  assert.equal(h.element('strategy-view').value,'socrates');
  h.element('strategy-view').value='range_reversal';h.element('strategy-view').listeners.change();
  assert.equal(h.element('socrates-family').hidden,true);
  assert.equal(h.element('range-family').hidden,false);
});
test('unrecognized saved or selected strategy falls back to Socrates',()=>{
  const h=dom();
  bindStrategyView(h.document,{storage:()=>({getItem:()=>'<script>',setItem(){}})});
  assert.equal(h.element('strategy-view').value,'socrates');
  h.element('strategy-view').value='trade-everything';h.element('strategy-view').listeners.change();
  assert.equal(h.element('strategy-view').value,'socrates');
});
test('valid remembered All view shows both analyses without changing trading permission',()=>{
  const h=dom();
  bindStrategyView(h.document,{storage:()=>({getItem:()=> 'all'})});
  assert.equal(h.element('socrates-family').hidden,false);
  assert.equal(h.element('range-family').hidden,false);
  assert.match(h.element('strategy-view-note').textContent,/does not change trading/);
});
test('current confirmed reversal shows references without claiming unavailable execution',()=>{
  const view=rangeFamilyView(snapshot(),now),markup=rangeFamilyMarkup(view);
  assert.equal(view.state,'Reversal observed');assert.ok(view.currentSignal);
  assert.equal(view.watchingOnly,true);
  assert.match(markup,/Execution unavailable/);assert.match(markup,/Broker execution is not available/);
  assert.match(markup,/Entry reference/);assert.match(markup,/Alpaca spot market/);
  assert.match(markup,/not submitted orders or guaranteed fills/);
});
for(const field of ['analyzed_at','observed_at','last_refresh_at'])test(`${field} older than 90 seconds cannot display a current setup`,()=>{
  const value=snapshot();value.strategy_families.range_reversal[field]=new Date(now-90001).toISOString();
  const view=rangeFamilyView(value,now),markup=rangeFamilyMarkup(view);
  assert.equal(view.state,'Analysis out of date');assert.equal(view.currentSignal,null);
  assert.doesNotMatch(markup,/class="range-signal"/);
  assert.match(markup,/Saved candles and observations/);assert.equal(view.history.length,1);
});
test('future receipt cannot appear current',()=>{
  const value=snapshot();value.strategy_families.range_reversal.observed_at=new Date(now+1).toISOString();
  assert.equal(rangeFamilyView(value,now).currentSignal,null);
});
test('a historical event or waiting state never appears as a present signal',()=>{
  for(const change of [{state:'WATCHING'},{current_event:{...snapshot().strategy_families.range_reversal.current_event,current:false}}]){
    const value=snapshot();Object.assign(value.strategy_families.range_reversal,change);
    assert.equal(rangeFamilyView(value,now).currentSignal,null);
  }
});
test('malformed geometry and unverified source cannot create an executable-looking reference',()=>{
  const value=snapshot();value.strategy_families.range_reversal.current_event.stop=105;
  value.strategy_families.range_reversal.source='unverified';
  const view=rangeFamilyView(value,now);
  assert.equal(view.currentSignal,null);assert.equal(view.source,'Waiting for verified Alpaca spot data');
});
test('unverified source alone cannot display a current setup',()=>{
  const value=snapshot();value.strategy_families.range_reversal.source='unverified';
  assert.equal(rangeFamilyView(value,now).currentSignal,null);
});
test('a current failed analysis keeps its useful data-waiting reason visible',()=>{
  const value=snapshot();Object.assign(value.strategy_families.range_reversal,{
    state:'DATA_WAITING',observed_at:null,detail:'Native candle coverage has a gap.'});
  const view=rangeFamilyView(value,now);
  assert.equal(view.state,'Waiting for data');assert.equal(view.detail,'Native candle coverage has a gap.');
  assert.equal(view.currentSignal,null);
});
test('missing family remains an explicit waiting state',()=>{
  const view=rangeFamilyView({},now);
  assert.equal(view.state,'Waiting for analysis');assert.equal(view.currentSignal,null);
  assert.match(rangeFamilyMarkup(view),/Waiting for the first native five-minute Bitcoin observation/);
});
test('provider text is escaped and history is bounded to the five newest observations',()=>{
  const value=snapshot(),family=value.strategy_families.range_reversal;
  family.detail='<img src=x onerror=alert(1)>';family.interpretation_warnings=['<script>alert(1)</script>'];
  family.candidates=Array.from({length:8},(_,i)=>({...family.candidates[0],entry:100+i}));
  const view=rangeFamilyView(value,now),markup=rangeFamilyMarkup(view);
  assert.equal(view.history.length,5);assert.equal(view.history[0].entry,107);
  assert.doesNotMatch(markup,/<img|<script>/);assert.match(markup,/&lt;img/);
});
test('static page keeps account, Socrates execution, positions and controls outside strategy view sections',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  assert.match(html,/id="strategy-view"/);assert.match(html,/Global Live · all enabled strategies/);
  assert.match(html,/<h2>Socrates · why no trade today\?<\/h2>/);
  const socratesStart=html.indexOf('id="socrates-family"'),rangeStart=html.indexOf('id="range-family"');
  assert.ok(html.indexOf('id="live-status"')<socratesStart);
  assert.ok(html.indexOf('id="balance"')<socratesStart);
  assert.ok(html.indexOf('id="session-review"')>rangeStart);
  assert.ok(html.indexOf('id="holdings"')>rangeStart);
});
