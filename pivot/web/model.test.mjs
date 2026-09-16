import test from 'node:test';
import assert from 'node:assert/strict';
import {money,escape,age,settingsError,vixStatus,appStatus} from './model.mjs';
const now=Date.now(),snapshot={account_at:new Date(now).toISOString(),account:{buying_power:100},positions:[],orders:[]};
test('target validation preserves dollar semantics',()=>{assert.equal(settingsError({target_dollars:'25'},snapshot,now),'');assert.match(settingsError({target_dollars:'101'},snapshot,now),/buying power/);for(const target_dollars of ['NaN','0','-1','25.001'])assert.ok(settingsError({target_dollars},snapshot,now));});
test('stale and exposed account cannot change target',()=>{assert.match(settingsError({target_dollars:25},{...snapshot,account_at:new Date(now-61000).toISOString()},now),/current account/);assert.match(settingsError({target_dollars:25},{...snapshot,positions:[{}]},now),/finished/);});
test('untrusted provider text is escaped',()=>{assert.equal(escape('<script>"x"</script>'),'&lt;script&gt;&quot;x&quot;&lt;/script&gt;');assert.equal(money(null),'—');assert.equal(age('bad'),Infinity);});
test('VIX candle readiness cannot imply a current cached quote',()=>{
  const health={status:'current',candles_current:true,valid_until:new Date(now+900000).toISOString(),verification:{
    latest_value:18.05,latest_value_at:new Date(now-300000).toISOString(),budget:{used:572,limit:1000},next_refresh_at:new Date(now+800000).toISOString(),
  }};
  const status=vixStatus(health,now);
  assert.equal(status.label,'Candles ready');
  assert.equal(status.tone,'pass');
  assert.equal(status.lastQuote,'Last quote check: 18.05 · 5m ago');
  assert.match(status.quoteNote,/fresh VIX quote.*before an entry/);
  assert.equal(status.budgetText,'428 requests available within the app limit');
  assert.equal(status.nextRefreshAt,health.verification.next_refresh_at);
});
test('VIX budget distinguishes app requests from reserved capacity',()=>{
  const status=budget=>vixStatus({verification:{budget}},now).budgetText;
  assert.equal(status({used:52,limit:900,actual_requests:2,reserved:50}), '2 app requests used · 50 reserved · 848 available');
  assert.equal(status({used:52,limit:900,actual_requests:52,reserved:50}), '848 requests available within the app limit');
  assert.equal(status({used:52,limit:900,actual_requests:'2',reserved:50}), '848 requests available within the app limit');
  assert.equal(status({used:901,limit:900,actual_requests:851,reserved:50}), '');
});
test('expired or unverified history does not remain green between refreshes',()=>{
  for(const health of [undefined,{status:'current'},{status:'current',candles_current:false,valid_until:new Date(now+1000).toISOString()},
    {status:'current',candles_current:true,valid_until:new Date(now-1).toISOString()},
    {status:'blocked',candles_current:true,valid_until:new Date(now+1000).toISOString()}]){
    assert.equal(vixStatus(health,now).label,'Waiting');
    assert.equal(vixStatus(health,now).tone,'wait');
  }
});
test('closed market and malformed provider details are labeled safely',()=>{
  const status=vixStatus({status:'market_closed',verification:{latest_value:Infinity,latest_value_at:'bad',budget:{used:'2',limit:1000},next_refresh_at:'bad'}},now);
  assert.equal(status.label,'Market closed');
  assert.equal(status.tone,'wait');
  assert.equal(status.lastQuote,'');
  assert.equal(status.budgetText,'');
  assert.equal(status.nextRefreshAt,null);
  assert.equal(status.error,null);
});
test('updated permission review is visible and does not alter the saved state',()=>{
  const state={...snapshot,review_required:true,live_enabled:false,clock:{is_open:false},execution:{message:'old'}};
  const before=structuredClone(state);
  const status=appStatus(state,now);
  assert.equal(status.title,'Review updated live rules');
  assert.match(status.text,/Live money switch at the top right/);
  assert.deepEqual(state,before);
});
test('known closed session takes priority over an old waiting for VIX message',()=>{
  const state={...snapshot,live_enabled:true,clock:{is_open:false},analysis_at:new Date(now).toISOString(),execution:{message:'Waiting for VIX'}};
  const status=appStatus(state,now);
  assert.equal(status.title,'Live money on · market closed');
  assert.match(status.text,/next session/);
  assert.doesNotMatch(status.text,/Waiting for VIX/);
  assert.notEqual(appStatus({...state,account_at:new Date(now-61000).toISOString()},now).title,status.title);
  assert.equal(appStatus({...state,review_required:true,execution:{trade:{stage:'open'},message:'Managing stop'}},now).title,'QQQ · open');
});
