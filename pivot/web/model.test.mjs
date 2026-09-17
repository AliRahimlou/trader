import test from 'node:test';
import assert from 'node:assert/strict';
import {money,escape,age,settingsError,vixStatus,appStatus,releaseStatus} from './model.mjs';
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
const installedRevision='452bce2'+'a'.repeat(33),candidateRevision='abcdef0'+'b'.repeat(33);
const releaseSnapshot={hosting:{mode:'hosted'},app_version:'2.1.0',revision:installedRevision,
  deployment:{state:'current',active_revision:installedRevision,checked_at:new Date(now).toISOString()}};
test('release label identifies the installed snapshot and only a matching fresh current check says latest',()=>{
  const original=structuredClone(releaseSnapshot);
  const status=releaseStatus(releaseSnapshot,now);
  assert.equal(status.label,'Installed v2.1.0 · 452bce2');
  assert.equal(status.detail,'Latest checked release.');
  assert.equal(status.current,true);
  assert.equal(status.tone,'current');
  assert.equal(status.revision,installedRevision);
  assert.deepEqual(releaseSnapshot,original);
  assert.equal(releaseStatus({...releaseSnapshot,deployment:{...releaseSnapshot.deployment,checked_at:new Date(now-600000).toISOString()}},now).current,true);
});
test('queued candidate never replaces the installed version label',()=>{
  const status=releaseStatus({...releaseSnapshot,deployment:{...releaseSnapshot.deployment,state:'waiting_off',candidate_revision:candidateRevision}},now);
  assert.equal(status.label,'Installed v2.1.0 · 452bce2');
  assert.equal(status.candidateRevision,candidateRevision);
  assert.equal(status.queued,true);
  assert.equal(status.tone,'pending');
  assert.equal(status.current,false);
  assert.match(status.detail,/abcdef0 queued · not installed/);
  assert.doesNotMatch(status.detail,/latest/i);
});
test('stale future or malformed update check cannot claim latest',()=>{
  for(const checked_at of [new Date(now-600001).toISOString(),new Date(now+1).toISOString(),'bad','2026-09-17T12:00:00',null]) {
    const status=releaseStatus({...releaseSnapshot,deployment:{...releaseSnapshot.deployment,checked_at}},now);
    assert.equal(status.current,false);
    assert.equal(status.tone,'unknown');
    assert.doesNotMatch(status.detail,/latest/i);
    assert.equal(status.revision,installedRevision);
  }
});
test('mismatched active or candidate metadata cannot say running latest',()=>{
  for(const deployment of [
    {...releaseSnapshot.deployment,active_revision:candidateRevision},
    {...releaseSnapshot.deployment,active_revision:undefined},
    {...releaseSnapshot.deployment,candidate_revision:candidateRevision},
    {...releaseSnapshot.deployment,state:'updated'},
  ]) {
    const status=releaseStatus({...releaseSnapshot,deployment},now);
    assert.equal(status.current,false);
    assert.doesNotMatch(status.detail,/latest/i);
  }
});
test('old app without semantic version uses its installed revision and unknown metadata stays unconfirmed',()=>{
  const status=releaseStatus({revision:installedRevision,hosting:{mode:'hosted'}},now);
  assert.equal(status.label,'Version 452bce2');
  assert.equal(status.current,false);
  for(const state of ['mystery','toString','constructor',{}]) {
    const unknown=releaseStatus({revision:installedRevision,deployment:{...releaseSnapshot.deployment,state}},now);
    assert.equal(unknown.current,false);
    assert.equal(typeof unknown.detail,'string');
    assert.match(unknown.detail,/unconfirmed/);
  }
  assert.equal(releaseStatus(undefined,now).label,'Version unavailable');
  assert.equal(releaseStatus({app_version:'2.1.0',hosting:{mode:'hosted'}},now).label,'Version 2.1.0 · revision unavailable');
});
test('local preview cannot imply hosted latest release',()=>{
  for(const state of [{hosting:{mode:'local'}},{...releaseSnapshot,hosting:{mode:'local'}}]) {
    const status=releaseStatus(state,now);
    assert.match(status.label,/^Local preview/);
    assert.equal(status.current,false);
    assert.equal(status.detail,'Running on this machine.');
    assert.equal(status.candidateRevision,null);
  }
});
test('invalid release identifiers are never rendered as version evidence',()=>{
  for(const value of ['<script>bad</script>',123,'abc','A'.repeat(40)]) {
    const status=releaseStatus({...releaseSnapshot,app_version:value,revision:value,deployment:{...releaseSnapshot.deployment,candidate_revision:value}},now);
    assert.equal(status.label,'Version unavailable');
    assert.equal(status.revision,null);
    assert.equal(status.candidateRevision,null);
    assert.equal(status.current,false);
  }
  assert.match(releaseStatus({...releaseSnapshot,app_version:'2.1.0-rc.1+build.5'},now).label,/v2\.1\.0-rc\.1\+build\.5/);
});
test('stale queued metadata is reported as historical rather than currently queued',()=>{
  const status=releaseStatus({...releaseSnapshot,deployment:{...releaseSnapshot.deployment,state:'waiting_off',candidate_revision:candidateRevision,checked_at:new Date(now-700000).toISOString()}},now);
  assert.match(status.detail,/Last reported update abcdef0 is not installed/);
  assert.match(status.detail,/out of date/);
  assert.equal(status.queued,false);
  assert.equal(status.current,false);
});
