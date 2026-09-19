import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {money,escape as esc,appStatus,createDisplayClock} from './model.mjs';
import {bindStrategyView,portfolioView,portfolioStatus,strategySettingsMatch,rangeFamilyView,rangeFamilyMarkup,cryptoQuantityLabel,cryptoHistoryMarkup,positionUnit,CRYPTO_MARKETS} from './strategy-families.mjs';

const source=(await readFile(new URL('./app.js',import.meta.url),'utf8')).replace(/^import .*?;\n/gm,'');
const initial=()=>({live_enabled:true,execution_available:true,execution_policy:{version:'socrates-v1',summary:['Socrates rules']},
  settings:{target_dollars:'25.00'},portfolio:{global_live_enabled:true,socrates:{enabled:true,target_dollars:'25.00'},
    range_reversal:{enabled:false,target_dollars:'5.00',symbols:['BTC/USD'],execution_available:true,
      policy_version:'crypto-v1',review_required:false,policy_summary:['Crypto stop-limit may not fill in a fast move.','Crypto fees apply.'],capabilities:{long:true,short:false}}},
  crypto_execution:{message:'Waiting for a current long setup',trades:[],incidents:[]}});
function harness({state=initial(),startup=false,clock=createDisplayClock}={}) {
  const nodes=new Map(),calls=[];
  const element=id=>{
    if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',checked:false,disabled:false,open:false,hidden:false,dataset:{},listeners:{},
      addEventListener(type,handler){this.listeners[type]=handler;},showModal(){this.open=true;},close(){this.open=false;},querySelectorAll(){return [];}});
    return nodes.get(id);
  };
  const context=vm.createContext({URL,Date,money,esc,appStatus,createDisplayClock:clock,portfolioView,portfolioStatus,strategySettingsMatch,rangeFamilyView,rangeFamilyMarkup,cryptoQuantityLabel,cryptoHistoryMarkup,positionUnit,CRYPTO_MARKETS,
    bindStrategyView:(document,options)=>bindStrategyView(document,{...options,storage:()=>null}),
    location:{hostname:'example.test',port:''},AbortSignal:{timeout:()=>({})},setInterval(){},
    document:{baseURI:'https://example.test/pivot/',getElementById:element,querySelector:()=>({dataset:{}})},
    fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url:String(url),options,resolve,reject}))});
  vm.runInContext((startup?source:source.replace('refresh();setInterval(refresh,10000);',''))+`\n
    render=()=>{};renderStrategyFamilies=()=>{};acceptSnapshot(${JSON.stringify(state)});
    globalThis.app={changeStrategies,changeLive,refresh,reviewStrategies,renderStrategyControls,recheckCryptoIncidents,
      setSnapshot:acceptSnapshot,displayNow:()=>displayClock.now(),state:()=>({snapshot,strategyError,pendingStrategy,strategyReview,strategySaving,cryptoRechecking,cryptoRecheckMessage})};`,context);
  return {app:context.app,element,calls,fire:(id,type='click')=>element(id).listeners[type]?.({preventDefault(){}})};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const answer=(call,state,ok=true)=>call.resolve({ok,json:async()=>state});
const writes=h=>h.calls.filter(c=>['PUT','POST'].includes(c.options.method));
const payload=call=>JSON.parse(call.options.body);
const updated=(state,settings)=>{const next=structuredClone(state);for(const [id,values] of Object.entries(settings))Object.assign(next.portfolio[id],values);return next;};

test('loading and switching views never writes a permission or family setting',()=>{
  const h=harness({startup:true});
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.method,undefined);
  const before=JSON.stringify(h.app.state().snapshot);
  for(const value of ['range_reversal','all','socrates']){h.element('strategy-view').value=value;h.fire('strategy-view','change');}
  assert.equal(writes(h).length,0);assert.equal(JSON.stringify(h.app.state().snapshot),before);
});

test('Run both requires explicit review and sends only the selected family settings',async()=>{
  const state=initial(),h=harness({state});h.fire('run-both');
  assert.equal(h.element('strategy-dialog').open,true);assert.equal(writes(h).length,0);
  assert.match(h.element('strategy-dialog-policy').innerHTML,/Socrates rules/);
  assert.match(h.element('strategy-dialog-policy').innerHTML,/stop-limit/);
  assert.match(h.element('strategy-dialog-policy').innerHTML,/fees/);
  h.fire('strategy-review-form','submit');assert.equal(writes(h).length,0);
  h.element('accept-strategy-policy').checked=true;h.fire('strategy-review-form','submit');
  assert.equal(writes(h).length,1);
  assert.equal(h.calls[0].url,'https://example.test/pivot/api/strategies');
  assert.deepEqual(payload(h.calls[0]),{socrates:{enabled:true},range_reversal:{enabled:true,policy_version:'crypto-v1'}});
  assert.equal(h.calls[0].options.headers['X-Pivot-Intent'],'settings');
  const next=updated(state,payload(h.calls[0]));answer(h.calls[0],next);await flush();
  assert.equal(h.element('strategy-dialog').open,false);
  assert.equal(h.app.state().snapshot.portfolio.global_live_enabled,true);
  answer(h.calls[1],next);await flush();assert.equal(writes(h).length,1);
});

for(const [button,id] of [['socrates-toggle','socrates'],['range-toggle','range_reversal']])test(`${id} Off leaves global permission and the other family alone`,async()=>{
  const state=initial();state.portfolio.range_reversal.enabled=true;
  const h=harness({state});h.fire(button);assert.deepEqual(payload(h.calls[0]),{[id]:{enabled:false}});
  assert.equal(h.element('strategy-dialog').open,false);
  const next=updated(state,{[id]:{enabled:false}});answer(h.calls[0],next);await flush();
  assert.equal(h.app.state().snapshot.portfolio.global_live_enabled,true);
  assert.equal(h.app.state().snapshot.portfolio[id==='socrates'?'range_reversal':'socrates'].enabled,true);
  answer(h.calls[1],next);await flush();
});

test('global Off uses the master endpoint and preserves both family selections',async()=>{
  const state=initial();state.portfolio.range_reversal.enabled=true;
  const h=harness({state});h.fire('live-status');
  assert.equal(h.calls[0].url,'https://example.test/pivot/api/live');
  assert.deepEqual(payload(h.calls[0]),{enabled:false,policy_version:'socrates-v1'});
  const next=structuredClone(state);next.live_enabled=false;next.portfolio.global_live_enabled=false;
  answer(h.calls[0],next);await flush();
  assert.ok(h.app.state().snapshot.portfolio.socrates.enabled&&h.app.state().snapshot.portfolio.range_reversal.enabled);
  answer(h.calls[1],next);await flush();assert.equal(writes(h).length,1);
});

test('enabling Range while global Off does not enable global Live or alter Socrates',async()=>{
  const state=initial();state.live_enabled=false;state.portfolio.global_live_enabled=false;state.portfolio.socrates.enabled=false;
  const h=harness({state});h.fire('range-toggle');
  assert.match(h.element('strategy-dialog-summary').textContent,/turn global Live On separately/);
  h.element('accept-strategy-policy').checked=true;h.fire('strategy-review-form','submit');
  assert.deepEqual(payload(h.calls[0]),{range_reversal:{enabled:true,policy_version:'crypto-v1'}});
  const next=updated(state,payload(h.calls[0]));answer(h.calls[0],next);await flush();
  assert.equal(h.app.state().snapshot.portfolio.global_live_enabled,false);
  assert.equal(h.app.state().snapshot.portfolio.socrates.enabled,false);
  answer(h.calls[1],next);await flush();
});

test('crypto amount and ETH choice are reviewed and saved separately from Socrates',async()=>{
  const state=initial(),h=harness({state});h.element('range-amount').value='7.25';h.element('range-btc').checked=true;h.element('range-eth').checked=true;
  h.fire('range-settings-form','submit');assert.equal(writes(h).length,0);
  assert.match(h.element('strategy-dialog-policy').innerHTML,/unvalidated adaptation/);
  assert.match(h.element('strategy-dialog-policy').innerHTML,/\$7.25/);
  h.element('accept-strategy-policy').checked=true;h.fire('strategy-review-form','submit');
  assert.deepEqual(payload(h.calls[0]),{range_reversal:{target_dollars:'7.25',symbols:['BTC/USD','ETH/USD'],policy_version:'crypto-v1'}});
  const next=updated(state,payload(h.calls[0]));answer(h.calls[0],next);await flush();
  assert.equal(h.app.state().snapshot.settings.target_dollars,'25.00');
  assert.equal(h.app.state().snapshot.portfolio.range_reversal.enabled,false);
  answer(h.calls[1],next);await flush();
});

test('invalid crypto amounts or no markets cannot create a write',()=>{
  const h=harness();h.element('range-btc').checked=true;
  for(const value of ['0','Infinity','2.123','1e3']){h.element('range-amount').value=value;h.fire('range-settings-form','submit');}
  h.element('range-amount').value='5';h.element('range-btc').checked=false;h.fire('range-settings-form','submit');
  assert.equal(writes(h).length,0);assert.match(h.app.state().strategyError,/Choose at least one/);
});

test('uncertain family update clears only after a matching fresh snapshot with no write retry',async()=>{
  const state=initial(),h=harness({state}),settings={range_reversal:{enabled:true,policy_version:'crypto-v1'}};
  const changing=h.app.changeStrategies(settings);h.calls[0].reject(Object.assign(new Error('Timeout'),{name:'TimeoutError'}));await changing;
  assert.match(h.app.state().strategyError,/uncertain/);
  answer(h.calls[1],state);await flush();assert.match(h.app.state().strategyError,/uncertain/);
  const refreshing=h.app.refresh();answer(h.calls[2],updated(state,settings));await refreshing;
  assert.equal(h.app.state().strategyError,'');assert.equal(h.app.state().pendingStrategy,null);assert.equal(writes(h).length,1);
});

test('rejected family settings keep the server explanation while unmatched',async()=>{
  const h=harness(),changing=h.app.changeStrategies({range_reversal:{enabled:true}});
  answer(h.calls[0],{detail:'Review current crypto policy'},false);await changing;
  answer(h.calls[1],initial());await flush();assert.equal(h.app.state().strategyError,'Review current crypto policy');assert.equal(writes(h).length,1);
});

test('rejected crypto policy review is not confirmed by an already enabled family',async()=>{
  const state=initial();Object.assign(state.portfolio.range_reversal,{enabled:true,review_required:true});
  const h=harness({state}),settings={range_reversal:{enabled:true,policy_version:'crypto-v1'}};
  h.app.reviewStrategies(settings);
  const changing=h.app.changeStrategies(settings);
  answer(h.calls[0],{detail:'Review the current crypto execution rules'},false);await changing;
  answer(h.calls[1],state);await flush();
  assert.equal(h.element('strategy-dialog').open,true);
  assert.equal(h.app.state().strategyError,'Review the current crypto execution rules');
  for(const fields of [
    {policy_version:'crypto-v2',review_required:true},
    {policy_version:'crypto-v2',review_required:false},
    {policy_version:'crypto-v1',review_required:undefined},
  ]) {
    const poll=h.app.refresh();answer(h.calls.at(-1),updated(state,{range_reversal:fields}));await poll;
    assert.equal(h.element('strategy-dialog').open,true);
    assert.equal(h.app.state().strategyError,'Review the current crypto execution rules');
    assert.ok(h.app.state().pendingStrategy);
  }
  const confirmed=h.app.refresh();answer(h.calls.at(-1),updated(state,{range_reversal:{review_required:false}}));await confirmed;
  assert.equal(h.element('strategy-dialog').open,false);
  assert.equal(h.app.state().strategyError,'');assert.equal(h.app.state().pendingStrategy,null);
  assert.equal(writes(h).length,1);
});

test('pre-change poll cannot confirm an uncertain family update',async()=>{
  const state=initial(),h=harness({state}),settings={range_reversal:{enabled:true}},poll=h.app.refresh(),changing=h.app.changeStrategies(settings);
  h.calls[1].reject(Object.assign(new Error('Timeout'),{name:'TimeoutError'}));await changing;
  answer(h.calls[0],updated(state,settings));await poll;assert.match(h.app.state().strategyError,/uncertain/);
  answer(h.calls[2],state);await flush();assert.equal(h.app.state().snapshot.portfolio.range_reversal.enabled,false);assert.equal(writes(h).length,1);
});

test('review cannot silently enable settings changed by a later snapshot',()=>{
  const state=initial(),h=harness({state});h.fire('run-both');h.element('accept-strategy-policy').checked=true;
  const next=updated(state,{range_reversal:{target_dollars:'500.00'}});h.app.setSnapshot(next);h.fire('strategy-review-form','submit');
  assert.equal(writes(h).length,0);assert.match(h.element('strategy-dialog-error').textContent,/settings changed/);
});

test('global review includes only enabled families and refuses changed selections',()=>{
  const state=initial();state.live_enabled=false;state.portfolio.global_live_enabled=false;state.portfolio.socrates.enabled=false;state.portfolio.range_reversal.enabled=true;
  const h=harness({state});h.fire('live-status');assert.match(h.element('live-summary').textContent,/4H Range Reversal/);
  assert.doesNotMatch(h.element('live-policy').innerHTML,/Socrates rules/);assert.match(h.element('live-policy').innerHTML,/Crypto fees/);
  h.element('accept-policy').checked=true;h.app.setSnapshot(updated(state,{socrates:{enabled:true}}));h.fire('live-form','submit');
  assert.equal(writes(h).length,0);assert.match(h.element('live-error').textContent,/settings changed/);
});

test('actual enabled families drive the global status independently of selected view',()=>{
  const state=initial();state.portfolio.socrates.enabled=false;state.portfolio.range_reversal.enabled=true;
  assert.equal(portfolioView(state).scope,'4H Range Reversal');assert.match(portfolioStatus(state,{}).title,/4H Range Reversal/);
  assert.doesNotMatch(portfolioStatus(state,{}).title,/Socrates/);
  state.portfolio.global_live_enabled=false;state.crypto_execution.trades=[{symbol:'BTC/USD',stage:'open'}];
  assert.match(portfolioStatus(state,{}).title,/Managing 1 position/);assert.match(portfolioStatus(state,{}).text,/Existing positions continue/);
});

test('Range signals expire between snapshots and shorts remain visibly unsupported',()=>{
  const state=initial(),now=Date.parse('2026-09-19T12:10:30Z');
  state.strategy_families={range_reversal:{source:'alpaca_crypto_us',symbol:'BTC/USD',analyzed_at:new Date(now).toISOString(),observed_at:new Date(now).toISOString(),
    state:'SETUP_OBSERVED',signal_ready:true,signal_valid_until:new Date(now+1000).toISOString(),
    current_event:{status:'CONFIRMED',current:true,direction:'long',entry:100,stop:90,target:120,confirmation_at:new Date(now-30000).toISOString()}}};
  assert.equal(rangeFamilyView(state,now).state,'Ready signal');assert.equal(rangeFamilyView(state,now+1000).state,'Entry window expired');
  Object.assign(state.strategy_families.range_reversal.current_event,{direction:'short',stop:110,target:80});
  const view=rangeFamilyView(state,now);assert.match(view.state,/unsupported/);assert.match(rangeFamilyMarkup(view),/No new short order/);
});

test('ETH uses its own analysis and carries the adaptation notice',()=>{
  const state=initial();state.strategy_families={range_reversal:{analyses:{'ETH/USD':{symbol:'ETH/USD',source:'alpaca_crypto_us',analyzed_at:new Date().toISOString(),observed_at:new Date().toISOString(),state:'WATCHING'}}}};
  const view=rangeFamilyView(state,Date.now(),'ETH/USD');assert.equal(view.symbol,'ETH/USD');assert.match(rangeFamilyMarkup(view),/unvalidated adaptation/);
});

test('additional market selection requires review and saves only the explicit chosen markets',async()=>{
  const state=initial(),h=harness({state});
  h.app.renderStrategyControls();
  for(const symbol of ['sol','link','xrp'])assert.equal(h.element('range-'+symbol).checked,false);
  h.element('range-sol').checked=true;h.element('range-link').checked=true;h.element('range-xrp').checked=true;
  h.fire('range-settings-form','input');h.fire('range-settings-form','submit');
  assert.equal(writes(h).length,0);
  assert.match(h.element('strategy-dialog-policy').innerHTML,/SOL\/USD, LINK\/USD, XRP\/USD/);
  h.element('accept-strategy-policy').checked=true;h.fire('strategy-review-form','submit');
  assert.deepEqual(payload(h.calls[0]).range_reversal.symbols,['BTC/USD','SOL/USD','LINK/USD','XRP/USD']);
  assert.equal(payload(h.calls[0]).range_reversal.enabled,undefined);
  const next=updated(state,payload(h.calls[0]));answer(h.calls[0],next);await flush();answer(h.calls[1],next);await flush();
  assert.equal(h.app.state().snapshot.portfolio.range_reversal.enabled,false);
});

test('crypto target above provider order maximum never opens review or sends a write',()=>{
  const h=harness();h.app.renderStrategyControls();h.element('range-amount').value='200000.01';
  h.fire('range-settings-form','submit');assert.equal(writes(h).length,0);
  assert.match(h.app.state().strategyError,/200,000/);
});

test('crypto worker failure stays prominent even when global Live is Off and a position is open',()=>{
  const state=initial();state.portfolio.global_live_enabled=false;state.crypto_execution.trades=[{symbol:'BTC/USD',stage:'open'}];
  state.crypto_worker_error='Crypto execution needs attention; check its orders and positions.';
  assert.equal(portfolioStatus(state,{}).title,'Crypto execution needs attention');
  assert.equal(portfolioStatus(state,{}).text,state.crypto_worker_error);
  delete state.crypto_worker_error;state.worker_health={workers:[{name:'crypto_execution',status:'stalled'}]};
  assert.match(portfolioStatus(state,{}).text,/cannot confirm that exits/);
});

test('global deployment and policy holds are not hidden by the combined view',()=>{
  const state=initial(),fallback={title:'Entries paused',text:'Update gate'};
  state.deployment_gate={configured:true,locked:true};assert.deepEqual(portfolioStatus(state,fallback),fallback);
  delete state.deployment_gate;state.review_required=true;assert.deepEqual(portfolioStatus(state,fallback),fallback);
});

test('a saved crypto policy needing review cannot be labeled entries enabled',()=>{
  const state=initial();Object.assign(state.portfolio.range_reversal,{enabled:true,review_required:true});
  assert.equal(portfolioView(state).families[1].status,'Review updated rules');
  const h=harness({state});h.app.renderStrategyControls();
  assert.equal(h.element('range-review').hidden,false);h.fire('range-review');
  assert.deepEqual(JSON.parse(JSON.stringify(h.app.state().strategyReview)),{range_reversal:{enabled:true,policy_version:'crypto-v1'}});
  assert.equal(writes(h).length,0);
});

test('crypto cards distinguish net acquisition from remaining holdings and gross fills',()=>{
  const state=initial(),h=harness({state});
  for(const [quantities,label] of [
    [{net_entry_qty:'0.049875',filled_qty:'0.05'},'Net acquired: 0.049875 units'],
    [{remaining_qty:'0.01',net_entry_qty:'0.049875',filled_qty:'0.05'},'Remaining: 0.01 units'],
    [{remaining_qty:0,net_entry_qty:'0.049875'},'Remaining: 0 units'],
    [{filled_qty:'0.05'},'Gross filled: 0.05 units'],
    [{net_qty:'0.05',qty:'0.05'},'Quantity pending'],
    [{remaining_qty:null,net_entry_qty:'',filled_qty:false},'Quantity pending'],
    [{remaining_qty:'NaN',net_entry_qty:-1,filled_qty:Infinity},'Quantity pending'],
  ]) {
    state.crypto_execution.trades=[{symbol:'BTC/USD',stage:'open',amount:'5.00',...quantities}];
    h.app.setSnapshot(state);h.app.renderStrategyControls();
    assert.ok(h.element('crypto-trades').innerHTML.includes(label));
    if(!label.startsWith('Remaining'))assert.doesNotMatch(h.element('crypto-trades').innerHTML,/Remaining/);
  }
  assert.equal(writes(h).length,0);
});

test('incident recheck is offered only for recorded incidents and submits one bounded proof request',async()=>{
  const state=initial(),h=harness({state});h.app.renderStrategyControls();
  assert.equal(h.element('crypto-recheck').hidden,true);
  await h.fire('crypto-recheck');assert.equal(writes(h).length,0);
  state.crypto_execution.incidents=[{reason:'Entry submission needs reconciliation'}];h.app.setSnapshot(state);h.app.renderStrategyControls();
  assert.equal(h.element('crypto-recheck').hidden,false);
  const checking=h.fire('crypto-recheck');assert.equal(h.element('crypto-recheck').disabled,true);
  assert.match(h.element('crypto-recheck').textContent,/Checking broker/);
  await h.fire('crypto-recheck');assert.equal(writes(h).length,1);
  assert.equal(h.calls[0].url,'https://example.test/pivot/api/crypto/reconcile');
  assert.equal(h.calls[0].options.method,'POST');assert.equal(h.calls[0].options.headers['X-Pivot-Intent'],'settings');assert.deepEqual(payload(h.calls[0]),{});
  const checked={...state,crypto_reconciliation:{message:'Recovery requires a flat account with no open broker orders; existing exposure is unchanged.',remaining:state.crypto_execution.incidents,recovered:[]}};
  answer(h.calls[0],checked);await checking;h.app.renderStrategyControls();
  assert.match(h.element('crypto-recheck-message').textContent,/1 incident still needs attention/);
  assert.match(h.element('crypto-recheck-message').textContent,/Recovery requires a flat account with no open broker orders/);
  assert.match(h.element('crypto-incidents').innerHTML,/Entry submission needs reconciliation/);
  assert.equal(h.app.state().snapshot.live_enabled,true);
  assert.deepEqual(JSON.parse(JSON.stringify(h.app.state().snapshot.portfolio)),state.portfolio);
  answer(h.calls[1],state);await flush();h.app.renderStrategyControls();
  assert.match(h.element('crypto-recheck-message').textContent,/Recovery requires a flat account with no open broker orders/);
  assert.equal(writes(h).length,1);
});

test('verified incident recovery displays the returned snapshot and hides the recheck action',async()=>{
  const state=initial();state.crypto_execution.incidents=[{reason:'Uncertain entry'}];
  const h=harness({state}),checking=h.fire('crypto-recheck'),next=structuredClone(state);next.crypto_execution.incidents=[];
  answer(h.calls[0],next);await checking;h.app.renderStrategyControls();
  assert.equal(h.element('crypto-recheck').hidden,true);
  assert.match(h.element('crypto-recheck-message').textContent,/No crypto incidents in the latest update/);
  assert.equal(h.element('crypto-incidents').innerHTML,'');
  answer(h.calls[1],next);await flush();assert.equal(writes(h).length,1);
  h.app.setSnapshot(state);h.app.renderStrategyControls();
  assert.match(h.element('crypto-recheck-message').textContent,/1 incident still needs attention/);
  assert.doesNotMatch(h.element('crypto-recheck-message').textContent,/No crypto incidents/);
});

for(const failure of ['rejection','timeout','incomplete'])test(`incident ${failure} keeps records visible and never retries automatically`,async()=>{
  const state=initial();state.crypto_execution.incidents=[{reason:'Unverified entry'}];
  const h=harness({state}),checking=h.fire('crypto-recheck');
  if(failure==='timeout')h.calls[0].reject(Object.assign(new Error('Timeout'),{name:'TimeoutError'}));
  else answer(h.calls[0],failure==='incomplete'?{}:{detail:'Broker unavailable'},failure==='incomplete');
  await checking;h.app.renderStrategyControls();
  assert.match(h.element('crypto-recheck-message').textContent,failure==='timeout'?/uncertain/:failure==='incomplete'?/incomplete/:/Broker unavailable/);
  assert.match(h.element('crypto-incidents').innerHTML,/Unverified entry/);
  assert.equal(h.element('crypto-recheck').disabled,false);
  answer(h.calls[1],state);await flush();assert.equal(writes(h).length,1);
});

test('a poll started during incident recheck cannot restore stale incidents after recovery',async()=>{
  const state=initial();state.crypto_execution.incidents=[{reason:'Old incident'}];
  const h=harness({state}),checking=h.fire('crypto-recheck'),poll=h.app.refresh(),next=structuredClone(state);next.crypto_execution.incidents=[];
  answer(h.calls[0],next);await checking;
  answer(h.calls[1],state);await poll;
  assert.equal(h.app.state().snapshot.crypto_execution.incidents.length,0);
  answer(h.calls[2],next);await flush();assert.equal(writes(h).length,1);
});

test('global Off remains available and a slower recheck cannot overwrite its newer saved state',async()=>{
  const state=initial();state.crypto_execution.incidents=[{reason:'Old incident'}];
  const h=harness({state}),checking=h.fire('crypto-recheck'),turningOff=h.app.changeLive(false),off=structuredClone(state);
  off.live_enabled=false;off.portfolio.global_live_enabled=false;
  answer(h.calls[1],off);await turningOff;answer(h.calls[2],off);await flush();
  const old=structuredClone(state);old.crypto_execution.incidents=[];answer(h.calls[0],old);await checking;
  assert.equal(h.app.state().snapshot.live_enabled,false);
  assert.match(h.app.state().cryptoRecheckMessage,/Refreshing the current incident list/);
  answer(h.calls[3],off);await flush();
  assert.deepEqual(writes(h).map(c=>new URL(c.url).pathname),['/pivot/api/crypto/reconcile','/pivot/api/live']);
});

test('recent crypto outcomes distinguish known zero fills, closed fills, and unverified attempts',()=>{
  const state=initial();state.crypto_execution.history=[
    {symbol:'BTC/USD',completed_at:'2026-09-19T12:00:00Z',reason:'Entry rejected',filled_qty:'0'},
    {symbol:'ETH/USD',completed_at:'2026-09-19T12:10:00Z',reason:'Position fully closed',filled_qty:'0.05',net_entry_qty:'0.049875'},
    {symbol:'BTC/USD',completed_at:'invalid',reason:'<script>Unverified</script>'},
  ];
  const h=harness({state});h.app.renderStrategyControls();const html=h.element('crypto-history').innerHTML;
  assert.match(html,/BTC\/USD · No fill/);assert.match(html,/Gross filled: 0 units/);
  assert.match(html,/ETH\/USD · Closed position/);assert.match(html,/Gross filled: 0.05 units · Net acquired: 0.049875 units/);
  assert.match(html,/Completed attempt/);assert.match(html,/&lt;script&gt;Unverified/);assert.doesNotMatch(html,/<script>|profit|P&L|Remaining/);
  assert.equal((cryptoHistoryMarkup(Array(12).fill(state.crypto_execution.history[0])).match(/<article>/g)||[]).length,10);
  assert.equal(writes(h).length,0);
});

test('broker holdings label crypto units separately from stock shares',()=>{
  for(const symbol of ['BTCUSD','BTC/USD','ETHUSD','ETH/USD'])assert.equal(positionUnit({symbol}),'units');
  assert.equal(positionUnit({symbol:'SOL/USD',asset_class:'crypto'}),'units');
  assert.equal(positionUnit({symbol:'QQQ',asset_class:'us_equity'}),'shares');
  assert.equal(positionUnit({symbol:'QQQ'}),'shares');
});

test('Socrates card prioritizes a verified closed session over an old VIX wait',()=>{
  const state=initial(),server=Date.parse('2026-09-19T13:00:00Z');
  Object.assign(state,{server_at:new Date(server).toISOString(),account_at:new Date(server-1000).toISOString(),clock:{is_open:false},execution:{message:'Waiting for actual VIX data'}});
  const h=harness({state,clock:()=>createDisplayClock({wallNow:()=>server-60000,elapsedNow:()=>0})});
  h.app.renderStrategyControls();assert.match(h.element('socrates-run-detail').textContent,/regular market session is closed/);
  assert.doesNotMatch(h.element('socrates-run-detail').textContent,/Waiting for actual VIX/);
  state.execution.trade={stage:'exit_pending'};state.execution.message='Waiting for confirmed position exit';
  h.app.setSnapshot(state);h.app.renderStrategyControls();assert.match(h.element('socrates-run-detail').textContent,/confirmed position exit/);
  delete state.execution.trade;state.account_at=new Date(server-61000).toISOString();
  h.app.setSnapshot(state);h.app.renderStrategyControls();assert.match(h.element('socrates-run-detail').textContent,/Waiting for confirmed position exit/);
  assert.doesNotMatch(h.element('socrates-run-detail').textContent,/regular market session is closed/);
});

test('only accepted responses reset display time and render calls do not refresh its age',async()=>{
  let elapsed=0;const server=Date.parse('2026-09-19T13:00:00Z'),state=initial();state.server_at=new Date(server).toISOString();
  const h=harness({state,clock:()=>createDisplayClock({wallNow:()=>server-5000,elapsedNow:()=>elapsed})});
  elapsed=2500;h.app.renderStrategyControls();assert.equal(h.app.displayNow(),server+2500);
  const oldPoll=h.app.refresh(),changing=h.app.changeLive(false);
  const next=structuredClone(state);next.server_at=new Date(server+10000).toISOString();next.live_enabled=false;next.portfolio.global_live_enabled=false;
  answer(h.calls[1],next);await changing;assert.equal(h.app.displayNow(),server+10000);
  answer(h.calls[0],state);await oldPoll;assert.equal(h.app.displayNow(),server+10000);
  elapsed+=1000;assert.equal(h.app.displayNow(),server+11000);
  answer(h.calls[2],next);await flush();assert.equal(h.app.displayNow(),server+10000);
});
