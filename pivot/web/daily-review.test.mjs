import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import * as model from './model.mjs';
import * as families from './strategy-families.mjs';
import * as daily from './daily-review.mjs';

const family=()=>({checks_recorded:4,submission_attempts:0,filled_entries:0,closed_trades:0,open_at_end:0,
  wins:0,losses:0,breakeven:0,unverified_outcomes:0,verified_net_pnl:null,net_pnl_status:'no_closed_trades',blockers:[],trades:[]});
const report=()=>({schema:'daily-trading-review-v1',day:'2026-09-19',status:'partial',period_status:'in_progress',
  generated_at:'2026-09-19T16:00:00Z',revision:3,families:{socrates:family(),range_reversal:family()},
  accounting:{fees:{observed_usd_cost:null,status:'pending',final:false}},missing_evidence:[],investigations:[]});

test('an empty day is neutral and missing costs are not zero or a profit',()=>{
  const view=daily.dailyReviewView(report(),'all'),html=daily.dailyReviewMarkup(report(),'all');
  assert.equal(view.noTrades,true);assert.equal(view.entries,0);assert.equal(view.closed,0);
  assert.equal(view.families[0].net,null);assert.equal(view.feesLabel,'Not available');
  assert.match(html,/No closed trades/);assert.match(html,/no closed-trade return to assess/i);
  assert.doesNotMatch(html,/\$0\.00|profitable day|positive return/i);
});

test('single-family views exclude other family trades and scoped investigations',()=>{
  const r=report();r.investigations=[{family:'socrates',title:'Stock review',reason:'stock-only'},
    {family:'range_reversal',title:'Crypto review',reason:'crypto-only'},{title:'Account records',reason:'shared'}];
  r.families.socrates.blockers=[{reason:'stock blocker',count:2}];
  r.families.range_reversal.blockers=[{reason:'crypto blocker',count:3}];
  const stock=daily.dailyReviewMarkup(r,'socrates'),crypto=daily.dailyReviewMarkup(r,'range_reversal'),all=daily.dailyReviewMarkup(r,'all');
  assert.match(stock,/stock blocker/);assert.doesNotMatch(stock,/crypto blocker|crypto-only/);
  assert.match(crypto,/crypto blocker/);assert.doesNotMatch(crypto,/stock blocker|stock-only/);
  assert.match(all,/stock blocker/);assert.match(all,/crypto blocker/);
  assert.ok([stock,crypto,all].every(html=>html.includes('shared')));
  assert.equal(daily.dailyReviewView(r,'__proto__').families[0].id,'socrates');
});

test('missing, malformed and boolean counts never appear as confirmed zeros',()=>{
  for(const value of [undefined,null,false,true,'0','',NaN,-1,1.1]){
    const r=report();r.families.socrates.filled_entries=value;
    const view=daily.dailyReviewView(r);
    assert.equal(view.entries,null);assert.equal(view.noTrades,false);
    assert.match(view.summary,/unavailable/);
    assert.match(daily.dailyReviewMarkup(r),/<strong>—<\/strong><span>Filled entries/);
  }
});

test('partial or unavailable net stays unverified even when a numeric subtotal exists',()=>{
  for(const status of ['partial','unavailable','pending',null,undefined]){
    const r=report();Object.assign(r.families.socrates,{closed_trades:2,verified_net_pnl:'18.00',net_pnl_status:status});
    assert.equal(daily.dailyReviewView(r).families[0].net,null);
    assert.doesNotMatch(daily.dailyReviewMarkup(r),/\$18\.00/);
  }
});

test('verified zero is a known result while invalid money remains unknown',()=>{
  const r=report();Object.assign(r.families.socrates,{closed_trades:1,verified_net_pnl:'0.00',net_pnl_status:'complete'});
  assert.equal(daily.dailyReviewView(r).families[0].netLabel,'$0.00');
  for(const value of [null,undefined,false,true,'',' ','NaN','Infinity','1e3',{},[]]){
    r.families.socrates.verified_net_pnl=value;
    assert.equal(daily.dailyReviewView(r).families[0].net,null);
  }
});

test('provisional account fees are labeled separately without pretending they are final',()=>{
  const r=report();r.accounting.fees.observed_usd_cost='0.03';
  const view=daily.dailyReviewView(r),html=daily.dailyReviewMarkup(r);
  assert.equal(view.feesLabel,'$0.03 recorded');assert.match(view.feesDetail,/more costs may arrive/);
  assert.match(html,/Account-wide costs/);assert.match(html,/not a final total or a per-strategy charge/);
  r.accounting.fees.observed_usd_cost='0.00';
  assert.equal(daily.dailyReviewView(r).feesLabel,'$0.00 recorded');
});

test('trade outcomes use verified net and show factual entry/exit reasons',()=>{
  const r=report();r.families.range_reversal.trades=[{symbol:'BTC/USD',created_at:'2026-09-18T23:00:00Z',completed_at:'2026-09-19T14:00:00Z',
    entry_reason:'Confirmed return inside range',exit_reason:'Protective stop filled',net_pnl:'-0.04',cost_status:'verified'},
    {symbol:'ETH/USD',net_pnl:'200.00',cost_status:'pending',exit_reason:'Target reached'}];
  const html=daily.dailyReviewMarkup(r,'range_reversal');
  assert.match(html,/Protective stop filled/);assert.match(html,/Loss after verified costs/);assert.match(html,/-\$0\.04/);
  assert.match(html,/Result not verified/);assert.doesNotMatch(html,/\$200\.00/);
  assert.match(html,/including earlier entries/);
});

test('all provider-derived text is escaped and no injected markup becomes active',()=>{
  const attack='<img src=x onerror="alert(1)">';const r=report();
  r.families.socrates.blockers=[{reason:attack,count:1}];
  r.families.socrates.trades=[{symbol:attack,entry_reason:attack,exit_reason:attack,cost_status:attack,net_pnl:attack}];
  r.investigations=[{title:attack,reason:attack}];r.missing_evidence=[attack];r.revision=attack;
  const html=daily.dailyReviewMarkup(r);
  assert.doesNotMatch(html,/<img|<script|src=x onerror="/);assert.match(html,/&lt;img/);assert.match(html,/Review revision Not recorded/);
});

test('review revisions are evidence revisions rather than application version hashes',()=>{
  assert.match(daily.dailyReviewMarkup(report()),/Review revision #3/);
  for(const revision of ['abcdef0123456789','3',0,-1,1.5,true,null]){
    assert.equal(daily.dailyReviewView({...report(),revision}).revision,'Not recorded');
  }
});

test('late revisions display the review time separately from the historical cutoff',()=>{
  const r={...report(),day:'2026-09-18',generated_at:'2026-09-19T16:00:00Z',as_of:'2026-09-19T04:00:00Z',period_status:'day_ended'};
  const view=daily.dailyReviewView(r);
  assert.notEqual(view.reviewed,view.generated);
  assert.match(daily.dailyReviewMarkup(r),/Reviewed Sep 19, 12:00 PM ET · Records through Sep 19, 12:00 AM ET/);
});

test('a stale or incomplete collector labels saved records visibly without erasing history',()=>{
  for(const status of ['unavailable','stale','error','overdue','refreshing','partial','starting','waiting_account','account_changed']){
    const r={...report(),collection:{status}};
    assert.ok(daily.dailyReviewView(r).notice);assert.match(daily.dailyReviewView(r).summary,/Saved records/);
    assert.match(daily.dailyReviewMarkup(r),/daily-review-notice/);
    assert.match(daily.dailyReviewMarkup(r),/Filled entries/);
  }
  for(const account_status of ['unavailable','stale']){
    assert.match(daily.dailyReviewView({...report(),collection:{status:'current',account_status}}).notice,/account check/i);
  }
  assert.equal(daily.dailyReviewView({...report(),collection:{status:'current'}}).notice,'');
});

test('only verified account movement is shown and never labeled strategy profit',()=>{
  const r=report();r.accounting.account_change={status:'observed',cash_flow_adjusted_change_usd:'0.0032',
    equity_change_usd:'25.0032',start_at:'2026-09-19T13:30:00Z',end_at:'2026-09-19T16:00:00Z',partial_day:true};
  let html=daily.dailyReviewMarkup(r);
  assert.match(html,/Account movement during recorded period: \$0.0032/);
  assert.match(html,/Includes open-position changes/);assert.match(html,/not strategy profit or a full-day return/);
  assert.doesNotMatch(html,/\$25.00/);
  for(const status of ['cash_flows_unverified','unavailable',null]){
    r.accounting.account_change.status=status;
    assert.doesNotMatch(daily.dailyReviewMarkup(r),/Account movement during recorded period/);
  }
});

test('gross results are explicitly before fees and intent creation is an entry plan',()=>{
  const r=report();r.families.socrates.trades=[{symbol:'QQQ',created_at:'2026-09-19T13:30:00Z',gross_pnl:'0.0042',gross_status:'verified_gross',cost_status:'pending'}];
  let html=daily.dailyReviewMarkup(r);
  assert.match(html,/\$0.0042 before fees/);assert.match(html,/Entry plan:/);assert.doesNotMatch(html,/>Entry:/);
  assert.match(html,/Result not verified/);assert.doesNotMatch(html,/Gain after verified costs/);
  r.families.socrates.trades[0].gross_status='unverified';
  assert.doesNotMatch(daily.dailyReviewMarkup(r),/\$0.0042/);
});

test('family-specific missing evidence follows the selected strategy',()=>{
  const r=report();r.missing_evidence=['Socrates: stock evidence','4H Range Reversal: crypto evidence','Shared account evidence'];
  const html=daily.dailyReviewMarkup(r,'range_reversal');
  assert.match(html,/crypto evidence|Shared account evidence/);assert.doesNotMatch(html,/stock evidence/);
});

test('unavailable and wrong-schema reports do not manufacture family totals',()=>{
  for(const r of [null,{},[],{...report(),status:'unavailable'},{...report(),schema:'other'},{...report(),day:'2026-02-31'}]){
    assert.equal(daily.dailyReviewView(r,'all').available,false);
    assert.doesNotMatch(daily.dailyReviewMarkup(r,'all'),/Filled entries|\$0\.00/);
  }
});

test('date navigation respects New York days and rejects nonexistent calendar dates',()=>{
  assert.equal(daily.reviewDay(Date.parse('2026-09-20T02:00:00Z')),'2026-09-19');
  assert.equal(daily.previousReviewDay('2026-03-09'),'2026-03-08');
  assert.equal(daily.previousReviewDay('2026-01-01'),'2025-12-31');
  for(const value of ['2026-02-29','2026-02-31','2026-9-1','not-a-date',null])assert.equal(daily.validReviewDay(value),false);
});

test('saved-review loader uses GET only and validates response date and schema',async()=>{
  const calls=[];const get=async(url,options)=>{calls.push({url:String(url),options});return {ok:true,json:async()=>report()};};
  assert.equal((await daily.fetchDailyReview(get,'https://example.test/pivot/api/','2026-09-19')).day,'2026-09-19');
  assert.equal(calls[0].url,'https://example.test/pivot/api/reviews?day=2026-09-19');assert.equal(calls[0].options.method,'GET');
  assert.equal(calls[0].options.body,undefined);
  await assert.rejects(daily.fetchDailyReview(get,'https://example.test/pivot/api/','2026-09-18'),/incomplete/);
  await assert.rejects(daily.fetchDailyReview(get,'https://example.test/pivot/api/','2026-02-31'),/valid/);
  await assert.rejects(daily.fetchDailyReview(async()=>({ok:false}),'https://example.test/api/','2026-09-19'),/Could not load/);
  const unavailable=await daily.fetchDailyReview(async()=>({ok:true,json:async()=>({schema:'daily-trading-review-v1',day:'2026-09-19',status:'unavailable'})}),'https://example.test/api/','2026-09-19');
  assert.equal(daily.dailyReviewView(unavailable).available,false);
});

const appSource=(await readFile(new URL('./app.js',import.meta.url),'utf8')).replace(/^import .*?;\n/gm,'').replace('refresh();setInterval(refresh,10000);','');
function appHarness(snapshotReport=report()){
  const nodes=new Map(),calls=[];
  const element=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',dataset:{},listeners:{},hidden:false,
    addEventListener(type,handler){this.listeners[type]=handler;},querySelectorAll(){return [];}});return nodes.get(id);};
  const context=vm.createContext({...model,...families,...daily,esc:model.escape,URL,Date,AbortSignal:{timeout:()=>({})},
    location:{port:'',hostname:'example.test'},document:{baseURI:'https://example.test/pivot/',getElementById:element,activeElement:null},setInterval(){},
    fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url:String(url),options,resolve,reject}))});
  vm.runInContext(appSource+`\nrenderStrategyFamilies=()=>{};renderStrategyControls=()=>{};
    acceptSnapshot(${JSON.stringify({daily_review:snapshotReport,server_at:'2026-09-19T16:00:00Z'})});
    globalThis.app={loadDailyReview,renderDailyReview,state:()=>({dailySelectedDay,dailyHistory,dailyReviewLoading,dailyReviewError})};`,context);
  return {app:context.app,nodes,element,calls};
}

test('an older report request cannot overwrite a later selected date',async()=>{
  const h=appHarness();const first=h.app.loadDailyReview('2026-09-18'),second=h.app.loadDailyReview('2026-09-17');
  h.calls[1].resolve({ok:true,json:async()=>({...report(),day:'2026-09-17'})});await second;
  h.calls[0].resolve({ok:true,json:async()=>({...report(),day:'2026-09-18'})});await first;
  assert.equal(h.app.state().dailyHistory.day,'2026-09-17');assert.ok(h.calls.every(call=>call.options.method==='GET'));
});

test('Today cancels a pending historical view without changing execution settings',async()=>{
  const h=appHarness();const pending=h.app.loadDailyReview('2026-09-18');
  h.element('daily-review-today').listeners.click();
  h.calls[0].resolve({ok:true,json:async()=>({...report(),day:'2026-09-18'})});await pending;
  assert.equal(h.app.state().dailySelectedDay,null);assert.equal(h.app.state().dailyHistory,null);
  assert.match(h.element('daily-review-title').textContent,/Today/);assert.equal(h.calls.length,1);
});

test('changing the strategy view immediately filters the displayed daily review',()=>{
  const h=appHarness();h.element('strategy-view').value='range_reversal';h.element('strategy-view').listeners.change();
  assert.match(h.element('daily-review-content').innerHTML,/data-review-family="range_reversal"/);
  assert.doesNotMatch(h.element('daily-review-content').innerHTML,/data-review-family="socrates"/);
  assert.equal(h.calls.length,0);
});

test('an older snapshot review is never titled as today',()=>{
  const h=appHarness({...report(),day:'2026-09-18'});h.app.renderDailyReview();
  assert.equal(h.element('daily-review-title').textContent,'Latest saved trading review');
  assert.match(h.element('daily-review-message').textContent,/Today’s review has not arrived/);
  assert.equal(h.element('daily-review-message').hidden,false);
});
