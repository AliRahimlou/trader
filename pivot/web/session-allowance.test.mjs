import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {sessionEntryStatus,pauseNotice,portfolioStatus,portfolioView} from './strategy-families.mjs';

const counts=(socrates,crypto)=>({socrates:{used:socrates,remaining:Math.max(0,2-socrates)},range_reversal:{used:crypto,remaining:Math.max(0,2-crypto)}});
const allowance=(socrates=0,crypto=0)=>({status:'available',session_day:'2026-09-21',timezone:'America/New_York',limit:2,
  used:socrates+crypto,remaining:Math.max(0,2-socrates)+Math.max(0,2-crypto),families:counts(socrates,crypto)});
test('per-family allowance reports each strategy without mislabeling attempts as fills',()=>{
  const view=sessionEntryStatus(allowance(1,0));
  assert.equal(view.blocked,false);
  assert.match(view.text,/Socrates 1 of 2 entry attempts used · 4H Range Reversal 0 of 2 entry attempts used/);
  assert.match(view.text,/Each strategy has its own limit/);
  assert.match(view.text,/Rejected or uncertain submissions count; exits do not/);
  assert.deepEqual(view.families.socrates,{used:1,remaining:1,blocked:false});
});
test('one exhausted family blocks only that family',()=>{
  const view=sessionEntryStatus(allowance(2,1));
  assert.equal(view.blocked,false);
  assert.equal(view.families.socrates.blocked,true);
  assert.equal(view.families.range_reversal.blocked,false);
  assert.match(view.text,/Session limit reached for Socrates\./);
  const state={live_enabled:true,execution_available:true,portfolio:{global_live_enabled:true,socrates:{enabled:true},range_reversal:{enabled:true,execution_available:true}},
    entry_allowance:allowance(2,1),crypto_execution:{trades:[]}};
  const families=portfolioView(state).families;
  assert.equal(families[0].status,'On · new entries paused');
  assert.equal(families[1].status,'On · entries enabled');
  assert.match(portfolioStatus(state,{}).title,/Live money on · Socrates \+ 4H Range Reversal/);
});
test('both exhausted families leave existing management visible',()=>{
  const state={live_enabled:true,portfolio:{global_live_enabled:true,socrates:{enabled:true},range_reversal:{enabled:true}},
    entry_allowance:allowance(2,2),crypto_execution:{trades:[{symbol:'BTC/USD'}]}};
  const view=portfolioStatus(state,{});
  assert.match(view.title,/Managing 1 position.*new entries paused/);
  assert.match(view.text,/Session limit reached for Socrates and 4H Range Reversal/);
  assert.match(view.text,/Existing positions remain managed/);
});
test('unknown and inconsistent allowance never looks like spare capacity',()=>{
  const good=allowance(1,1);
  for(const value of [undefined,{...good,status:'blocked'},{...good,used:null},{...good,limit:3},{...good,timezone:'UTC'},
    {...good,families:undefined},{...good,families:{socrates:good.families.socrates}},
    {...good,families:counts(1,1),used:0},{...good,families:{...counts(1,1),socrates:{used:1,remaining:2}}},
    {...good,families:{...counts(1,1),range_reversal:{used:-1,remaining:2}}},
    {status:'available',session_day:'2026-09-21',timezone:'America/New_York',limit:2,used:1,remaining:1}]){
    const view=sessionEntryStatus(value);
    assert.equal(view.blocked,true);
    assert.equal(view.families,null);
    assert.match(view.text,/unverified/);
  }
});
test('family cards agree with paused entries while retaining saved selections',()=>{
  const base={execution_available:true,portfolio:{global_live_enabled:true,socrates:{enabled:true},range_reversal:{enabled:true,execution_available:true}}};
  for(const entry_allowance of [undefined,allowance(2,2)]){
    const state={...base,entry_allowance};
    assert.ok(portfolioView(state).families.every(f=>f.enabled && f.status==='On · new entries paused'));
    assert.match(portfolioStatus(state,{}).title,/New entries paused/);
  }
  assert.ok(portfolioView({...base,entry_allowance:allowance()}).families.every(f=>f.status==='On · entries enabled'));
});
test('an app pause explains Socrates Off until the owner saves a Socrates setting',()=>{
  const paused={at:'2026-09-21T15:00:00+00:00',kind:'strategy_paused',detail:{family:'socrates',reason:'The broker rejected a QQQ entry order.'}};
  const base={execution_available:true,entry_allowance:allowance(),crypto_execution:{trades:[]},
    portfolio:{global_live_enabled:true,socrates:{enabled:false},range_reversal:{enabled:true,execution_available:true}}};
  const view=portfolioView({...base,events:[paused]});
  assert.equal(view.families[0].status,'Paused by the app · no new entries');
  assert.equal(view.families[0].pauseReason,'The broker rejected a QQQ entry order.');
  assert.match(view.pause.text,/Global Live and 4H Range Reversal are unchanged/);
  assert.equal(view.families[1].status,'On · entries enabled');
  assert.equal(view.globalOn,true);
  const superseded={at:'2026-09-21T15:05:00+00:00',kind:'strategy_settings',detail:{socrates:{enabled:false}}};
  assert.equal(pauseNotice({...base,events:[superseded,paused]}),null);
  assert.equal(portfolioView({...base,events:[superseded,paused]}).families[0].status,'Off · no new entries');
  const cryptoOnly={at:'2026-09-21T15:05:00+00:00',kind:'strategy_settings',detail:{range_reversal:{enabled:true}}};
  assert.ok(pauseNotice({...base,events:[cryptoOnly,paused]}));
  assert.equal(pauseNotice({...base,portfolio:{...base.portfolio,socrates:{enabled:true}},events:[paused]}),null);
  assert.equal(pauseNotice({...base,events:[{kind:'strategy_paused',detail:'garbled'}]}),null);
});
test('initial document does not report Live Off before saved permission arrives',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  const live=html.match(/<button id="live-status"[^>]*>[\s\S]*?<\/button>/)[0];
  assert.match(live,/disabled/);
  assert.match(live,/Checking/);
  assert.doesNotMatch(live,/<strong>Off<\/strong>/);
});
