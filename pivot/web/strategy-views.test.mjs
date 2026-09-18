import test from 'node:test';
import assert from 'node:assert/strict';
import {strategyViews,leaderOverview,marketOverview,appStatus} from './model.mjs';
const now=Date.parse('2026-09-17T17:00:00Z');
const at=new Date(now).toISOString();
const check=(passed,detail='Wait for a level')=>({name:'Nasdaq level event',passed,detail});
const snapshot=()=>({analysis_at:at,setup:{strategy_id:'previous_day_sweep',strategies:[
  {id:'four_hour_retest',label:'Four-hour break & retest',state:'AT_LEVEL',checks:[check(false)]},
  {id:'previous_day_sweep',label:'Previous-day sweep',state:'SETUP_READY',checks:[check(true)],
    leader_evidence_valid_until:new Date(now+900000).toISOString(),event_expires_at:new Date(now+1800000).toISOString(),
    leader_observation_valid_until:new Date(now+390000).toISOString()}
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
test('setup cards expire immediately with leader or event evidence, including between refreshes',()=>{
  for(const field of ['leader_evidence_valid_until','event_expires_at']){
    const s=snapshot();s.setup.strategies[1][field]=new Date(now+1000).toISOString();
    assert.equal(strategyViews(s,now)[1].qualified,true);
    const view=strategyViews(s,now+1000)[1];
    assert.equal(view.qualified,false);assert.equal(view.state,'Confirmation expired');
    assert.match(view.detail,/window has ended/);
    s.setup.strategies[1][field]='bad timestamp';
    assert.equal(strategyViews(s,now)[1].qualified,false);
    delete s.setup.strategies[1][field];
    assert.equal(strategyViews(s,now)[1].state,'Waiting for confirmation timing');
  }
});
test('leader overview never invents agreement or counts a second Google company',()=>{
  const s={decision_trace:{captured_at:at,current_at_snapshot:true,leaders:{AAPL:{vote:'long',reason:'current reaction'},NVDA:{vote:'short',reason:'current reaction'},GOOG:{vote:'long'},MSFT:{vote:'invalid',reason:'no zone reaction'}}}};
  const summary=leaderOverview(s,now);
  assert.equal(summary.rows.length,7);assert.match(summary.detail,/1 up \/ 1 down/);
  assert.equal(summary.rows.find(r=>r.symbol==='MSFT').vote,null);
  assert.equal(leaderOverview(s,now+91000).current,false);
  assert.equal(leaderOverview({...s,diagnostic_error:'Save failed'},now).current,false);
});
test('saved setup and leader display expire with input freshness before the reaction deadline',()=>{
  const s=snapshot();s.setup.strategies[1].leader_observation_valid_until=new Date(now+1000).toISOString();
  assert.equal(strategyViews(s,now)[1].qualified,true);
  const expired=strategyViews(s,now+1000)[1];
  assert.equal(expired.qualified,false);assert.equal(expired.state,'Leader data out of date');
  assert.match(expired.detail,/candles are too old/);
  s.decision_trace={captured_at:at,current_at_snapshot:true,leaders:{AAPL:{vote:'long',reason:'persistent reaction',
    latest_bar_at:new Date(now-389000).toISOString(),observation_valid_until:new Date(now+1000).toISOString(),
    evidence_valid_until:new Date(now+900000).toISOString()}}};
  assert.equal(leaderOverview(s,now).rows[0].vote,'long');
  const leader=leaderOverview(s,now+1000).rows[0];
  assert.equal(leader.vote,null);assert.equal(leader.label,'Data stale');assert.equal(leader.expired,false);
  for(const invalid of [undefined,'bad timestamp']){
    s.setup.strategies[1].leader_observation_valid_until=invalid;
    assert.equal(strategyViews(s,now)[1].state,'Waiting for confirmation timing');
  }
});
test('leader overview reports dissent, age, and owner-selected rule without requiring simultaneous reactions',()=>{
  const names=['AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA'];
  const leaders=Object.fromEntries(names.map((symbol,i)=>[symbol,{vote:i<5?'short':i===5?'long':null,
    reason:i<6?'persistent reaction':'no zone reaction',latest_bar_at:at,
    reaction_at:new Date(now-(i%3)*300000).toISOString(),evidence_valid_until:new Date(now+300000).toISOString(),observational_only:true}]));
  const s={decision_trace:{captured_at:at,current_at_snapshot:true,leaders,
    setup:{leader_rule:{minimum_agree:5,maximum_opposing:1,persistence_minutes:15}}}};
  const view=leaderOverview(s,now);
  assert.match(view.detail,/1 up \/ 5 down/);
  assert.equal(view.alignment,'Downward majority; GOOGL opposes.');
  assert.match(view.ruleText,/at least 5 agreeing; up to 1 opposing/);
  assert.match(view.timing,/different five-minute candles/);
  assert.match(view.scope,/observations only/);
  assert.deepEqual(view.rows.slice(0,3).map(row=>row.reactionLabel),['0m ago','5m ago','10m ago']);
});
test('expired, conflicting and missing leader evidence are not presented as neutral or agreeing',()=>{
  const leaders={AAPL:{vote:'long',reason:'persistent reaction',evidence_valid_until:at},
    MSFT:{vote:'short',reason:'conflicting active area reactions',conflicting_reactions:true},
    NVDA:{vote:'short',reason:'current 5-minute data missing'}};
  const view=leaderOverview({decision_trace:{captured_at:at,current_at_snapshot:true,leaders}},now);
  assert.match(view.detail,/0 up \/ 0 down/);
  assert.deepEqual(view.rows.slice(0,3).map(row=>row.label),['Expired','Conflicting','Data missing']);
  assert.match(view.alignment,/AAPL: expired/);
});
test('different observation times are reported separately from differing reaction times',()=>{
  const leaders=Object.fromEntries(['AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA'].map((symbol,i)=>
    [symbol,{vote:'short',reason:'current reaction',latest_bar_at:new Date(now-(i===6?300000:0)).toISOString()}]));
  const view=leaderOverview({decision_trace:{captured_at:at,current_at_snapshot:true,leaders}},now);
  assert.match(view.alignment,/updating to the same observation time/);
});
test('broader market structure remains descriptive and becomes unavailable when analysis or data is stale',()=>{
  const s={analysis_at:at,data_health:{stocks:{status:'current'}},market_context:{data_current:true,frames:[
    {timeframe_minutes:60,status:'ready',direction:'down',latest_bar_at:at,detail:'Lower swing highs and lows.'},
    {timeframe_minutes:240,status:'ready',direction:'up',latest_bar_at:at,detail:'Higher swing highs and lows.'}
  ]}};
  const view=marketOverview(s,now);
  assert.deepEqual(view.frames.map(f=>f.direction),['down','up']);
  assert.match(view.explanation,/not a forecast or an extra entry rule/);
  assert.equal(marketOverview(s,now+91000).current,false);
  s.data_health.stocks.status='incomplete';
  assert.ok(marketOverview(s,now).frames.every(f=>f.direction==='unknown'));
  s.clock={is_open:false};s.account_at=at;
  assert.match(marketOverview(s,now).detail,/Market closed/);
});
test('per-method views preserve precise leader timing and data blockers',()=>{
  const s=snapshot();s.setup.strategies[0].checks=[{name:'Magnificent Seven at their zones',passed:false,
    detail:'Latest five-minute observations are not synchronized'}];
  assert.match(strategyViews(s,now)[0].detail,/not synchronized/);
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
