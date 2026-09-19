import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {bindStrategyView,rangeFamilyView,rangeFamilyMarkup,portfolioView,globalCryptoAlert,STRATEGY_VIEW_KEY,STRATEGY_VIEW_SECTIONS} from './strategy-families.mjs';
import {createDisplayClock} from './model.mjs';

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
    if(!elements.has(id))elements.set(id,{id,hidden:false,value:'',textContent:'',innerHTML:'',dataset:{},listeners:{},
      addEventListener(type,fn){this.listeners[type]=fn;},querySelectorAll(){return [];}});
    return elements.get(id);
  };
  return {element,document:{baseURI:'https://example.test/pivot/',getElementById:element}};
}
// Build the visibility tree from the shipped HTML, rather than inventing any
// requested id. This catches missing wrappers and children outside their scope.
function pageDom(html){
  const elements=new Map(),stack=[],voidTags=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
  for(const match of html.matchAll(/<\/?([a-z][a-z0-9-]*)\b[^>]*>/gi)){
    const tag=match[1].toLowerCase(),raw=match[0];
    if(raw.startsWith('</')){assert.equal(stack.pop()?.tag,tag,`Unexpected closing ${tag}`);continue;}
    const id=raw.match(/\bid="([^"]+)"/)?.[1];
    const node={id,tag,parent:stack.at(-1),hidden:/\shidden(?:\s|>)/.test(raw),dataset:{},value:'',textContent:'',listeners:{},
      addEventListener(type,fn){this.listeners[type]=fn;}};
    if(id){assert.equal(elements.has(id),false,`Duplicate id: ${id}`);elements.set(id,node);}
    if(!voidTags.has(tag)&&!raw.endsWith('/>'))stack.push(node);
  }
  const element=id=>{assert.ok(elements.has(id),`Missing actual HTML id: ${id}`);return elements.get(id);};
  const ancestors=id=>{const result=[];for(let node=element(id);node;node=node.parent)if(node.id)result.push(node.id);return result;};
  return {element,ancestors,visible:id=>{for(let node=element(id);node;node=node.parent)if(node.hidden)return false;return true;},
    select(value){element('strategy-view').value=value;element('strategy-view').listeners.change();},
    document:{getElementById:id=>elements.get(id)||null}};
}
test('view selection persists locally while every execution value stays unchanged',async()=>{
  const source=(await readFile(new URL('./app.js',import.meta.url),'utf8'))
    .replace(/^import .*?;\n/gm,'').replace('refresh();setInterval(refresh,10000);','');
  const h=dom(),writes=[],requests=[],saved=new Map(),initial=snapshot();
  initial.portfolio={global_live_enabled:true,socrates:{enabled:true,target_dollars:'5.00'},
    range_reversal:{enabled:true,target_dollars:'5.00',symbols:['BTC/USD'],execution_available:true}};
  const storage={getItem:key=>saved.get(key),setItem:(key,value)=>{saved.set(key,value);writes.push({key,value});}};
  const context=vm.createContext({URL,Date,createDisplayClock,bindStrategyView:(document,options)=>bindStrategyView(document,{...options,storage:()=>storage}),
    rangeFamilyView,rangeFamilyMarkup,portfolioView,document:h.document,location:{hostname:'example.test',port:''},
    fetch:(...args)=>{requests.push(args);throw Error('No network expected');}});
  vm.runInContext(source+`\nsnapshot=${JSON.stringify(initial)};globalThis.state=()=>({snapshot,dirty,saving,toggling,pendingLive});globalThis.refreshRange=renderStrategyFamilies;`,context);
  const before=JSON.stringify(context.state());
  for(const [selected,socratesHidden,rangeHidden] of [['range_reversal',true,false],['all',false,false],['socrates',false,true]]){
    h.element('strategy-view').value=selected;
    h.element('strategy-view').listeners.change();
    assert.equal(h.element('socrates-family').hidden,socratesHidden);
    assert.equal(h.element('range-family').hidden,rangeHidden);
    context.refreshRange();
    for(const id of STRATEGY_VIEW_SECTIONS.socrates)assert.equal(h.element(id).hidden,socratesHidden,`${id} after rendering`);
    for(const id of STRATEGY_VIEW_SECTIONS.range_reversal)assert.equal(h.element(id).hidden,rangeHidden,`${id} after rendering`);
    assert.equal(JSON.stringify(context.state()),before);
    assert.match(h.element('strategy-view-note').textContent,/Changing this view does not change trading/);
    if(selected==='all')assert.match(h.element('strategy-view-note').textContent,/does not change trading/);
  }
  assert.equal(requests.length,0);
  assert.deepEqual(writes.map(row=>row.key),Array(3).fill(STRATEGY_VIEW_KEY));
  assert.equal(saved.get(STRATEGY_VIEW_KEY),'socrates');
});
test('shipped page switches the whole strategy workspace while shared controls and urgent alerts remain visible',async()=>{
  const h=pageDom(await readFile(new URL('./index.html',import.meta.url),'utf8'));
  h.element('global-crypto-alert').hidden=false;
  const incident=globalCryptoAlert({crypto_execution:{incidents:[{message:'Protection order requires reconciliation.'}]}});
  h.element('global-crypto-alert').textContent=incident;
  bindStrategyView(h.document,{storage:()=>null});
  const stockContent=['socrates-toggle','amount','data-connections','session-review','market-overview','strategy-cards','audit','trade-results'];
  const cryptoContent=['range-toggle','range-settings-form','range-content','crypto-execution-status','crypto-history'];
  const shared=['balance','live-status','status-title','strategy-run-summary','run-both','holdings','journal','global-crypto-alert'];
  for(const view of ['range_reversal','socrates','all','range_reversal','all','socrates']){
    h.select(view);
    for(const id of stockContent)assert.equal(h.visible(id),view!=='range_reversal',`${view}: ${id}`);
    for(const id of cryptoContent)assert.equal(h.visible(id),view!=='socrates',`${view}: ${id}`);
    for(const id of shared)assert.equal(h.visible(id),true,`${view}: shared ${id}`);
    assert.equal(h.element('global-crypto-alert').textContent,incident,`${view}: incident message preserved`);
    assert.equal(h.element('strategy-workspace').dataset.view,view);
    assert.equal(h.element('family-control-grid').dataset.view,view);
    assert.match(h.element('strategy-controls-title').textContent,view==='all'?/All strateg(?:y|ies)/i:view==='socrates'?/Socrates/:/4H Range Reversal/);
  }
});
test('shared crypto warning includes outstanding incidents once and clears only when no incidents remain',()=>{
  const message='Protection order requires reconciliation.',second='Exit cancellation is unconfirmed.';
  assert.equal(globalCryptoAlert({crypto_execution:{incidents:[{message},{message},{message:second}]}}),
    `4H Range Reversal needs attention: ${message} ${second}`);
  assert.equal(globalCryptoAlert({crypto_execution:{incidents:[]}}),'');
  assert.equal(globalCryptoAlert({}),'');
});
test('saved selection restores the complete workspace on reload',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8'),saved=new Map();
  const storage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)};
  const first=pageDom(html);bindStrategyView(first.document,{storage:()=>storage});first.select('range_reversal');
  const reload=pageDom(html);bindStrategyView(reload.document,{storage:()=>storage});
  assert.equal(reload.element('strategy-view').value,'range_reversal');
  assert.equal(reload.visible('range-settings-form'),true);assert.equal(reload.visible('range-content'),true);
  assert.equal(reload.visible('amount'),false);assert.equal(reload.visible('session-review'),false);assert.equal(reload.visible('audit'),false);
});
test('server display time fixes client skew while Range observations still expire',()=>{
  let elapsed=0;const state=snapshot(),clock=createDisplayClock({wallNow:()=>now-700,elapsedNow:()=>elapsed});
  assert.equal(rangeFamilyView(state,clock.now()).current,false);
  clock.accept({server_at:at});assert.equal(rangeFamilyView(state,clock.now()).current,true);
  elapsed=90001;assert.equal(rangeFamilyView(state,clock.now()).state,'Analysis out of date');
});
test('Range evidence genuinely in the future relative to server time remains rejected',()=>{
  const state=snapshot(),clock=createDisplayClock({wallNow:()=>now+3600000,elapsedNow:()=>0});
  clock.accept({server_at:at});state.strategy_families.range_reversal.observed_at=new Date(now+1000).toISOString();
  assert.equal(rangeFamilyView(state,clock.now()).current,false);
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
test('all scoped wrappers exist in the shipped HTML and own their actual family content',async()=>{
  const h=pageDom(await readFile(new URL('./index.html',import.meta.url),'utf8'));
  for(const id of Object.values(STRATEGY_VIEW_SECTIONS).flat())h.element(id);
  const ownership={
    'socrates-toggle':'socrates-control',amount:'socrates-purchase','data-connections':'socrates-data',
    'market-overview':'socrates-family','strategy-cards':'socrates-family',audit:'socrates-rules','trade-results':'socrates-results',
    'range-toggle':'range-control','range-settings-form':'range-control','range-content':'range-family',
    'crypto-history':'crypto-management','crypto-execution-status':'crypto-management'};
  for(const [child,owner] of Object.entries(ownership))assert.ok(h.ancestors(child).includes(owner),`${child} must belong to ${owner}`);
  const scoped=new Set(Object.values(STRATEGY_VIEW_SECTIONS).flat());
  for(const id of ['balance','live-status','strategy-run-summary','run-both','holdings','journal','global-crypto-alert'])
    assert.equal(h.ancestors(id).some(parent=>scoped.has(parent)),false,`${id} must remain shared`);
});
