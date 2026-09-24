import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {readinessView,readinessAction,readinessMarkup,readinessItemsMarkup,readinessKey,readinessTimeText,socratesTradeMarkup,SOCRATES_KEY_RULES,READINESS_ORDER,READINESS_MAX_AGE_MS} from './readiness.mjs';
import * as model from './model.mjs';
import * as families from './strategy-families.mjs';
import * as readiness from './readiness.mjs';

const now=Date.parse('2026-09-22T15:00:00Z'),at=new Date(now-5000).toISOString();
const item=(id,status='ok',detail='Fine.')=>({id,label:id.replaceAll('_',' '),status,detail});
const allOk=()=>READINESS_ORDER.map(id=>item(id,id==='setup'?'info':'ok'));
const readiness_=(status,items=allOk(),extra={})=>({socrates_readiness:{checked_at:at,status,headline:`Headline ${status}`,next_action:null,items,...extra}});

test('each backend status renders its own owner-facing label',()=>{
  const ready=readinessView(readiness_('ready',READINESS_ORDER.map(id=>item(id))),now);
  assert.equal(ready.status,'ready');assert.equal(ready.label,'Ready');
  const waiting=readinessView(readiness_('waiting'),now);
  assert.equal(waiting.status,'waiting');assert.equal(waiting.label,'Waiting');
  const items=allOk();items[0]=item('live_permission','fail','Off: review the updated rules.');
  const blocked=readinessView(readiness_('blocked',items),now);
  assert.equal(blocked.status,'blocked');assert.equal(blocked.label,'Needs your action');
  for(const view of [ready,waiting,blocked])assert.match(readinessMarkup(view),new RegExp(`readiness-badge is-${view.status}`));
});

test('a failing or warning item can never be shown as ready',()=>{
  const failing=allOk();failing[3]=item('buying_power','fail','Not enough buying power.');
  assert.equal(readinessView(readiness_('ready',failing),now).status,'blocked');
  const warning=READINESS_ORDER.map(id=>item(id));warning[8]=item('data_leaders','warn','One leader is late.');
  assert.equal(readinessView(readiness_('ready',warning),now).status,'waiting');
  assert.equal(readinessView(readiness_('ready',[]),now).status,'waiting','ready needs a checklist');
  // A backend 'blocked' without a failing item is shown as waiting, never as ready.
  assert.equal(readinessView(readiness_('blocked'),now).status,'waiting');
});

test('next action appears as a prominent callout only when present',()=>{
  const items=allOk();items[0]=item('live_permission','fail','Off.');
  const withAction=readinessView(readiness_('blocked',items,{next_action:'Accept the updated Socrates rules: click Live money'}),now);
  const snapshot={review_required:true,live_enabled:false,execution_available:true,portfolio:{global_live_enabled:false,socrates:{enabled:true,review_required:true}}};
  const action=readinessAction(withAction,snapshot);
  assert.deepEqual(action,{kind:'live',label:'Accept updated rules'});
  const html=readinessMarkup(withAction,action);
  assert.match(html,/class="next-action"/);assert.match(html,/Accept the updated Socrates rules: click Live money/);
  assert.match(html,/id="readiness-action" class="primary" type="button" data-action="live">Accept updated rules</);
  const without=readinessView(readiness_('waiting'),now);
  assert.equal(without.nextAction,null);assert.equal(readinessAction(without,{portfolio:{global_live_enabled:true}}),null);
  assert.doesNotMatch(readinessMarkup(without,null),/next-action|readiness-action/);
});

test('the action button never offers Live money when it is already On, so it cannot turn Live Off',()=>{
  const items=allOk();items[0]=item('live_permission','fail','Accepted under an old policy.');
  const view=readinessView(readiness_('blocked',items),now);
  assert.equal(readinessAction(view,{live_enabled:true,portfolio:{global_live_enabled:true}}),null);
  assert.deepEqual(readinessAction(view,{live_enabled:false,execution_available:true,portfolio:{global_live_enabled:false,socrates:{enabled:true}}}),{kind:'live',label:'Turn Live money On'});
  const paused=allOk();paused[1]=item('strategy_enabled','fail','Paused by the app.');
  assert.deepEqual(readinessAction(readinessView(readiness_('blocked',paused),now),{portfolio:{global_live_enabled:true,socrates:{enabled:false}}}),{kind:'socrates',label:'Turn Socrates back on'});
  assert.equal(readinessAction(readinessView(readiness_('blocked',paused),now),{portfolio:{global_live_enabled:true,socrates:{enabled:true}}}),null);
});

test('the Live button needs a strategy the Live review can list, and follows the backend next step',()=>{
  const items=allOk();items[0]=item('live_permission','fail','Rules need review.');items[1]=item('strategy_enabled','fail','Socrates is turned Off.');
  const off={review_required:true,live_enabled:false,execution_available:true,portfolio:{global_live_enabled:false,socrates:{enabled:false,review_required:true}}};
  // Socrates Off: the header switch is disabled, so no Live button; turning Socrates on comes first.
  assert.deepEqual(readinessAction(readinessView(readiness_('blocked',items,{action:'socrates'}),now),off),{kind:'socrates',label:'Turn Socrates back on'});
  assert.equal(readinessAction(readinessView(readiness_('blocked',items,{action:'live'}),now),off),null);
  assert.equal(readinessAction(readinessView(readiness_('blocked',items),now),off,{liveReviewable:false})?.kind,'socrates','an older backend falls through to Socrates');
  // Order sending not configured: the text asks for configuration and no button contradicts it.
  const unconfigured=allOk();unconfigured[0]=item('live_permission','fail','Order sending is not configured on this server.');
  const on={live_enabled:false,execution_available:false,portfolio:{global_live_enabled:false,socrates:{enabled:true}}};
  assert.equal(readinessAction(readinessView(readiness_('blocked',unconfigured,{action:null,next_action:'Ask for the Alpaca live trading connection to be configured on AllSpark.'}),now),on),null);
  // An app pause: the backend names no control, so Alpaca is reviewed before any button is offered.
  const paused=allOk();paused[1]=item('strategy_enabled','fail','Socrates is Off. Pivot paused it: order rejected.');
  assert.equal(readinessAction(readinessView(readiness_('blocked',paused,{action:null}),now),{portfolio:{global_live_enabled:true,socrates:{enabled:false}}}),null);
});

test('a failing item that clears by itself reads as Waiting, never as needing the owner or ready',()=>{
  const items=READINESS_ORDER.map(id=>item(id));items[7]={...item('data_qqq','fail','Waiting for current QQQ 1-hour candles.'),needs_owner:false};
  const view=readinessView(readiness_('waiting',items),now);
  assert.equal(view.status,'waiting');assert.equal(view.label,'Waiting');assert.equal(view.counts.owner,0);assert.equal(view.counts.waiting,1);
  assert.equal(readinessView(readiness_('ready',items),now).status,'waiting');
  const html=readinessMarkup(view);
  assert.match(html,/14 of 15 checks OK · 1 blocking until it clears · show all checks/);assert.doesNotMatch(html,/need action|needs action/);
  assert.match(html,/Blocking until it clears/);
  // Unknown or missing needs_owner keeps the safe reading.
  items[3]=item('buying_power','fail','Not enough.');
  const mixed=readinessView(readiness_('waiting',items),now);
  assert.equal(mixed.status,'blocked');assert.equal(mixed.label,'Needs your action');
  assert.match(readinessMarkup(mixed),/1 needs action · 1 blocking until it clears/);
});

test('the render key ignores the check time, so an unchanged card is not rebuilt',()=>{
  const first=readinessView(readiness_('waiting'),now),later=readinessView({socrates_readiness:{...readiness_('waiting').socrates_readiness,checked_at:new Date(now-1000).toISOString()}},now);
  assert.notEqual(first.checkedAt,later.checkedAt);
  assert.equal(readinessKey(first),readinessKey(later));
  assert.notEqual(readinessKey(first),readinessKey(readinessView(readiness_('waiting',allOk(),{headline:'Other'}),now)));
  assert.notEqual(readinessKey(first,{kind:'live',label:'Turn Live money On'}),readinessKey(first));
  assert.match(readinessTimeText(first.checkedAt),/^Checked \d{1,2}:\d{2}:\d{2} [AP]M ET$/);
});

test('unknown fields, statuses and order are tolerated without inventing a pass',()=>{
  const view=readinessView({socrates_readiness:{checked_at:at,status:'mystery',future_field:{x:1},items:[
    {id:'setup',label:'Current setup',status:'info',detail:'Waiting.',extra:true},
    {id:'brand_new_check',label:'New check',status:'excellent',detail:'<b>odd</b>'},
    null,'text',[1],{status:'ok'},
    {id:'live_permission',label:'Live money',status:'ok',detail:'On.'}]}},now);
  assert.equal(view.available,true);
  assert.deepEqual(view.items.map(row=>row.id),['live_permission','setup','brand_new_check','item-2']);
  assert.equal(view.items.find(row=>row.id==='brand_new_check').status,'warn','an unknown item status reads as needing a look');
  assert.equal(view.status,'waiting');assert.equal(view.headline,'Socrates is waiting for a setup.');
  const html=readinessMarkup(view);
  assert.doesNotMatch(html,/<b>odd<\/b>/);assert.match(html,/&lt;b&gt;odd/);
  assert.match(html,/2 of 4 checks OK · 1 to check/);
});

test('a missing readiness block degrades to the existing status line',()=>{
  const view=readinessView({},now,{title:'Live money on · Socrates',text:'Waiting for its own data.'});
  assert.equal(view.available,false);assert.equal(view.status,'checking');assert.equal(view.label,'Checking');
  assert.equal(view.headline,'Live money on · Socrates');assert.match(view.note,/not available from this app version/);
  assert.equal(readinessAction(view,{}),null);
  const html=readinessMarkup(view);
  assert.match(html,/Waiting for its own data/);assert.doesNotMatch(html,/<details|next-action/);
  for(const bad of [null,[],'ready',{items:'nope'}])assert.doesNotThrow(()=>readinessMarkup(readinessView({socrates_readiness:bad},now)));
});

test('an old or future-dated checklist is shown as unverified, never as ready',()=>{
  const old=readiness_('ready',READINESS_ORDER.map(id=>item(id)));old.socrates_readiness.checked_at=new Date(now-READINESS_MAX_AGE_MS-1).toISOString();
  const view=readinessView(old,now);
  assert.equal(view.status,'checking');assert.equal(view.stale,true);assert.match(view.note,/out of date/);
  assert.equal(readinessAction(view,{}),null);
  old.socrates_readiness.checked_at='not a time';assert.equal(readinessView(old,now).status,'checking');
});

test('checks that are not simply OK are listed first with their icons; the rest stay one click away',()=>{
  const items=allOk();items[2]=item('account','fail','Account restricted.');items[9]=item('data_vix','warn','Only 20 requests left this month.');
  const html=readinessMarkup(readinessView(readiness_('blocked',items),now));
  const upfront=html.split('<details')[0];
  assert.match(upfront,/is-fail[^]*✕[^]*Account restricted/);assert.match(upfront,/is-warn[^]*![^]*Only 20 requests/);
  assert.match(upfront,/is-info/);assert.doesNotMatch(upfront,/is-ok/);
  assert.match(html,/12 of 15 checks OK · 1 needs action · 1 to check · show all checks/);
  assert.equal((html.split('<details')[1].match(/<li /g)||[]).length,15);
  assert.equal(readinessItemsMarkup([]),'');
});

test('the open trade card explains the PSQ short proxy with its own stop and target',()=>{
  const html=socratesTradeMarkup({stage:'protected',symbol:'PSQ',proxy:'inverse_etf',signal_direction:'short',amount:'15.00',stop:'36.71',target:'38.02',
    signal_geometry:{entry:'597.10',stop:'599.60',target:'593.40'}},'Managing PSQ');
  assert.match(html,/Open trade · PSQ/);assert.match(html,/Protected by a stop/);
  assert.match(html,/Stop \(PSQ\)<\/span><b>\$36\.71/);assert.match(html,/Target \(PSQ\)<\/span><b>\$38\.02/);
  assert.match(html,/inverse Nasdaq-100 ETF/);assert.match(html,/QQQ entry \$597\.10, stop \$599\.60, target \$593\.40/);assert.match(html,/never held overnight/);
  const long=socratesTradeMarkup({stage:'open',symbol:'QQQ',signal_direction:'long',stop:null,target:'',amount:'15'});
  assert.match(long,/Open trade · QQQ/);assert.doesNotMatch(long,/inverse/);assert.match(long,/Stop \(QQQ\)<\/span><b>—/);
  assert.equal(socratesTradeMarkup(null),'');
});

test('the Live dialog summary carries the 4.6 rules, not the retired ones',()=>{
  const text=SOCRATES_KEY_RULES.join(' ');
  for(const rule of [/4 of the 7/,/60 minutes/,/end of the next session/,/within 0\.4%/,/1R minimum/,/longs only \(QQQ\)/,/10:00 AM–12:00 PM ET/,/today’s open/,/0\.20% away/,/1\.5% away/])assert.match(text,rule);
  assert.doesNotMatch(text,/5 of 7|five of seven|180|15-minute reaction/i);
});

// app.js wiring: the next-step button opens the same review dialog as the header switch and
// sends nothing until the owner ticks the box and confirms.
const source=(await readFile(new URL('./app.js',import.meta.url),'utf8')).replace(/^import .*?;\n/gm,'').replace('refresh();setInterval(refresh,10000);','');
function harness(state) {
  const nodes=new Map(),calls=[];
  const element=id=>{
    if(!nodes.has(id))nodes.set(id,{id,value:'',textContent:'',innerHTML:'',checked:false,disabled:false,open:false,hidden:false,title:'',dataset:{},listeners:{},attributes:{},
      classList:{names:new Set(),toggle(name,on){on?this.names.add(name):this.names.delete(name);},contains(name){return this.names.has(name);}},
      setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this.attributes[name];},
      addEventListener(type,handler){this.listeners[type]=handler;},showModal(){this.open=true;},close(){this.open=false;},querySelectorAll(){return [];}});
    return nodes.get(id);
  };
  const context=vm.createContext({URL,Date,...model,esc:model.escape,...families,...readiness,
    bindStrategyView:(document,options)=>families.bindStrategyView(document,{...options,storage:()=>null}),
    location:{hostname:'example.test',port:''},AbortSignal:{timeout:()=>({})},setInterval(){},
    document:{baseURI:'https://example.test/pivot/',getElementById:element,querySelector:()=>({dataset:{}})},
    fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url:String(url),options,resolve,reject}))});
  vm.runInContext(source+`\nrenderStrategyFamilies=()=>{};acceptSnapshot(${JSON.stringify(state)});
    globalThis.app={renderReadiness,renderStrategyControls,state:()=>({snapshot})};`,context);
  return {app:context.app,element,calls,click:(action)=>element('readiness-content').listeners.click({target:{dataset:{action}}})};
}
const reviewState=()=>({live_enabled:false,review_required:true,execution_available:true,server_at:at,
  execution_policy:{version:'nasdaq-qqq-execution-v7-video-aligned',summary:['Socrates: rule one','Socrates: rule two']},settings:{target_dollars:'15.00'},
  entry_allowance:{status:'available',session_day:'2026-09-22',timezone:'America/New_York',limit:2,used:0,remaining:4,families:{socrates:{used:0,remaining:2},range_reversal:{used:0,remaining:2}}},
  portfolio:{global_live_enabled:false,socrates:{enabled:true,review_required:true,target_dollars:'15.00'},
    range_reversal:{enabled:false,paused:true,execution_available:true,target_dollars:'15.00',symbols:['BTC/USD'],policy_version:'crypto-v2',policy_summary:['Crypto rule']}},
  strategy_pause:{range_reversal:{paused:true,reason:'Socrates-only focus.',source:'code_default'}},
  crypto_execution:{paused:true,message:'Crypto is paused: Socrates-only focus. Existing crypto positions, if any, keep their exits.',trades:[],incidents:[]},
  socrates_readiness:{checked_at:at,status:'blocked',headline:'Socrates cannot place orders until you act.',next_action:'Accept the updated Socrates rules: click Live money',
    items:[item('live_permission','fail','Off: review needed.'),...allOk().slice(1)]}});

test('the next-step button opens the Live review dialog without sending a request',async()=>{
  const h=harness(reviewState());h.app.renderReadiness();
  assert.match(h.element('readiness-content').innerHTML,/data-action="live">Accept updated rules/);
  assert.equal(h.element('socrates-readiness').dataset.status,'blocked');
  h.click('live');
  assert.equal(h.element('live-dialog').open,true);
  assert.equal(h.element('live-title').textContent,'Accept the updated Socrates rules');
  assert.equal(h.element('confirm-live').textContent,'Accept updated rules');
  assert.equal(h.element('confirm-live').disabled,true);assert.equal(h.element('accept-policy').checked,false);
  assert.match(h.element('live-key-points').innerHTML,/4 of the 7/);
  assert.match(h.element('live-policy').innerHTML,/Socrates: rule one/);assert.doesNotMatch(h.element('live-policy').innerHTML,/Crypto rule/);
  assert.equal(h.element('live-rules-details').open,true,'the full rule list is shown, not folded away');
  assert.match(h.element('live-summary').textContent,/Socrates rules version: nasdaq-qqq-execution-v7-video-aligned\./);
  assert.equal(h.calls.length,0);
  h.element('accept-policy').checked=true;h.element('live-form').listeners.submit({preventDefault(){}});
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].url,'https://example.test/pivot/api/live');assert.equal(h.calls[0].options.method,'PUT');
  assert.equal(h.calls[0].options.headers['X-Pivot-Intent'],'live-control');
  assert.deepEqual(JSON.parse(h.calls[0].options.body),{enabled:true,policy_version:'nasdaq-qqq-execution-v7-video-aligned'});
});

test('the Live review never opens without an enabled strategy whose rules it can list',()=>{
  const state=reviewState();state.portfolio.socrates.enabled=false;
  state.socrates_readiness.items[1]=item('strategy_enabled','fail','Socrates is turned Off.');state.socrates_readiness.action='socrates';
  const h=harness(state);h.app.renderReadiness();
  assert.match(h.element('readiness-content').innerHTML,/data-action="socrates">Turn Socrates back on/);
  h.click('live');
  assert.equal(h.element('live-dialog').open,false);assert.equal(h.calls.length,0);
  h.click('socrates');
  assert.equal(h.element('strategy-dialog').open,true);assert.equal(h.calls.length,0);
});

test('a refresh that changes nothing keeps the card in place and only updates the time',()=>{
  const state=reviewState(),h=harness(state);h.app.renderReadiness();
  const content=h.element('readiness-content'),stamp={textContent:''};
  assert.match(h.element('readiness-announce').textContent,/^Needs your action: /);
  content.innerHTML='kept';content.querySelector=selector=>selector==='.readiness-time'?stamp:null;
  h.app.renderReadiness();
  assert.equal(content.innerHTML,'kept','an unchanged card is not rebuilt');assert.match(stamp.textContent,/^Checked /);
  h.app.state().snapshot.socrates_readiness.headline='Something changed.';h.app.renderReadiness();
  assert.match(content.innerHTML,/Something changed\./);
  assert.equal(h.element('readiness-announce').textContent,'Needs your action: Something changed.');
});

test('with Live money On the next-step handler never turns it Off',()=>{
  const state=reviewState();Object.assign(state,{live_enabled:true,review_required:false});state.portfolio.global_live_enabled=true;
  const h=harness(state);h.click('live');h.click('socrates');
  assert.equal(h.element('live-dialog').open,false);assert.equal(h.element('strategy-dialog').open,false);assert.equal(h.calls.length,0);
});

test('paused crypto controls render grayed, disabled and explained without changing any setting',()=>{
  const state=reviewState(),h=harness(state),before=JSON.stringify(state.portfolio);
  h.app.renderStrategyControls();
  for(const id of ['range-toggle','run-both','save-range','range-amount','range-btc','range-eth']){
    assert.equal(h.element(id).disabled,true,`${id} disabled`);
    assert.equal(h.element(id).attributes['aria-disabled'],'true',`${id} aria-disabled`);
    assert.equal(h.element(id).title,'Crypto is paused: Socrates-only focus',`${id} tooltip`);
  }
  assert.equal(h.element('range-control').classList.contains('is-paused'),true);
  assert.equal(h.element('crypto-area').classList.contains('is-paused'),true);
  for(const id of ['range-pause-badge','crypto-area-badge','crypto-pause-banner','crypto-paused-note'])assert.equal(h.element(id).hidden,false,id);
  assert.equal(h.element('range-run-state').textContent,'Paused · Socrates-only focus');
  assert.match(h.element('range-run-detail').textContent,/Crypto is paused: Socrates-only focus/);
  assert.match(h.element('strategy-run-summary').textContent,/Crypto paused/);
  assert.match(h.element('entry-allowance').textContent,/^Socrates entries today: 0 of 2 attempts used/);
  assert.doesNotMatch(h.element('entry-allowance').textContent,/4H Range Reversal/);
  h.element('range-toggle').listeners.click();h.element('run-both').listeners.click();
  assert.equal(h.element('strategy-dialog').open,false);assert.equal(h.calls.length,0);
  assert.equal(JSON.stringify(h.app.state().snapshot.portfolio),before);
  // Unpaused, the same controls are live again.
  const active=reviewState();delete active.strategy_pause;delete active.portfolio.range_reversal.paused;delete active.crypto_execution.paused;
  const u=harness(active);u.app.renderStrategyControls();
  assert.equal(u.element('range-toggle').disabled,false);assert.equal(u.element('range-toggle').attributes['aria-disabled'],undefined);
  assert.equal(u.element('crypto-pause-banner').hidden,true);assert.equal(u.element('range-control').classList.contains('is-paused'),false);
  // Lifting the pause in the same page hands the amount and market inputs back.
  h.app.state().snapshot.strategy_pause={};delete h.app.state().snapshot.portfolio.range_reversal.paused;delete h.app.state().snapshot.crypto_execution.paused;
  h.app.renderStrategyControls();
  for(const id of ['range-amount','range-btc','range-eth'])assert.equal(h.element(id).disabled,false,`${id} re-enabled`);
  assert.equal(h.element('crypto-management').classList.contains('has-exposure'),false);
  const open=reviewState();open.crypto_execution.trades=[{id:'t1',symbol:'BTC/USD',stage:'protected'}];
  const o=harness(open);o.app.renderStrategyControls();
  assert.equal(o.element('crypto-management').classList.contains('has-exposure'),true,'an open crypto position stays in full colour');
});
