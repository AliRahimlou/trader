import test from 'node:test';
import assert from 'node:assert/strict';
import {strategyViews,leaderOverview,appStatus} from './model.mjs';
const now=Date.parse('2026-09-17T17:00:00Z');
const at=new Date(now).toISOString();
const check=(passed,detail='Wait for a level')=>({name:'Nasdaq level event',passed,detail});
const snapshot=()=>({analysis_at:at,setup:{strategy_id:'previous_day_sweep',strategies:[
  {id:'four_hour_retest',label:'Four-hour break & retest',state:'AT_LEVEL',checks:[check(false)]},
  {id:'previous_day_sweep',label:'Previous-day sweep',state:'SETUP_READY',checks:[check(true)]}
]}});
test('both methods retain independent qualification and selection',()=>{
  const rows=strategyViews(snapshot(),now);
  assert.equal(rows.length,2);assert.equal(rows[0].qualified,false);assert.equal(rows[1].qualified,true);
  assert.equal(rows[1].selected,true);assert.match(rows[1].detail,/broker checks/);
});
test('expired or absent analysis cannot display an entry-ready result',()=>{
  const rows=strategyViews(snapshot(),now+91000);
  assert.ok(rows.every(r=>!r.qualified && !r.current));
  assert.ok(rows.every(r=>r.detail.includes('cannot authorize')));
  assert.equal(strategyViews({},now)[0].current,false);
});
test('ready label requires all checks and a nonempty check list',()=>{
  const s=snapshot();s.setup.strategies[1].checks=[];
  assert.equal(strategyViews(s,now)[1].qualified,false);
  s.setup.strategies[1].checks=[check(false)];
  assert.equal(strategyViews(s,now)[1].qualified,false);
});
test('leader overview never invents agreement or counts a second Google company',()=>{
  const s={decision_trace:{captured_at:at,current_at_snapshot:true,leaders:{AAPL:{vote:'long'},NVDA:{vote:'short'},GOOG:{vote:'long'},MSFT:{vote:'invalid'}}}};
  const summary=leaderOverview(s,now);
  assert.equal(summary.rows.length,7);assert.match(summary.detail,/1 up \/ 1 down/);
  assert.equal(summary.rows.find(r=>r.symbol==='MSFT').vote,null);
  assert.equal(leaderOverview(s,now+91000).current,false);
  assert.equal(leaderOverview({...s,diagnostic_error:'Save failed'},now).current,false);
});
test('missing stock inputs are visible instead of a misleading VIX wait',()=>{
  const s={analysis_at:at,live_enabled:true,data_health:{stocks:{status:'incomplete'}}};
  assert.equal(appStatus(s,now).title,'Waiting for complete stock data');
});
test('nearest watched area uses real QQQ observations and vanishes with stale analysis',()=>{
  const s=snapshot();s.observations=[{symbol:'QQQ',price:100}];
  const levels=[{low:110,high:111},{low:99,high:101},{low:null,high:102}];
  s.setup.strategies[0].levels=levels;
  assert.deepEqual(strategyViews(s,now)[0].area,{low:99,high:101});
  assert.equal(levels[0].low,110); // rendering does not mutate server evidence.
  assert.equal(strategyViews(s,now+91000)[0].area,null);
  s.observations=[];assert.equal(strategyViews(s,now)[0].area,null);
});
test('verified market closure distinguishes saved history from fresh entry signals',()=>{
  const s={...snapshot(),clock:{is_open:false},account_at:at};
  const rows=strategyViews(s,now);
  assert.ok(rows.every(row=>row.state==='Market closed'&&!row.qualified));
  assert.ok(rows.every(row=>row.detail.includes('Saved history')));
  assert.ok(leaderOverview(s,now).rows.every(row=>row.label==='Closed'&&row.vote===null));
  // An old or failed account/clock observation must not invent a closed session.
  s.account_at=new Date(now-61000).toISOString();
  assert.equal(strategyViews(s,now)[1].state,'Setup found');
  assert.equal(leaderOverview(s,now).rows.length,0);
  s.account_at=at;s.account_error='refresh failed';
  assert.equal(strategyViews(s,now)[1].state,'Setup found');
});
