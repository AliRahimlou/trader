import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {sessionEntryStatus,portfolioStatus,portfolioView} from './strategy-families.mjs';

const allowance={status:'available',session_day:'2026-09-21',timezone:'America/New_York',limit:2,used:0,remaining:2};
test('shared allowance reports attempts without mislabeling them as fills',()=>{
  const view=sessionEntryStatus({...allowance,used:1,remaining:1});
  assert.equal(view.blocked,false);
  assert.match(view.text,/1 of 2 entry attempts used across both strategies/);
  assert.match(view.text,/Rejected or uncertain submissions count; exits do not/);
});
test('exhausted allowance leaves existing management visible',()=>{
  const state={live_enabled:true,portfolio:{global_live_enabled:true,socrates:{enabled:true},range_reversal:{enabled:true}},
    entry_allowance:{...allowance,used:2,remaining:0},crypto_execution:{trades:[{symbol:'BTC/USD'}]}};
  const view=portfolioStatus(state,{});
  assert.match(view.title,/Managing 1 position.*new entries paused/);
  assert.match(view.text,/Session limit reached/);
  assert.match(view.text,/Existing positions remain managed/);
});
test('unknown and inconsistent allowance never looks like spare capacity',()=>{
  for(const value of [undefined,{...allowance,status:'blocked'},{...allowance,used:null},
    {...allowance,limit:3},{...allowance,used:2,remaining:2},{...allowance,timezone:'UTC'}]){
    const view=sessionEntryStatus(value);
    assert.equal(view.blocked,true);
    assert.match(view.text,/unverified/);
  }
});
test('family cards agree with paused entries while retaining saved selections',()=>{
  const base={execution_available:true,portfolio:{global_live_enabled:true,socrates:{enabled:true},range_reversal:{enabled:true,execution_available:true}}};
  for(const entry_allowance of [undefined,{...allowance,used:2,remaining:0}]){
    const state={...base,entry_allowance};
    assert.ok(portfolioView(state).families.every(f=>f.enabled && f.status==='On · new entries paused'));
    assert.match(portfolioStatus(state,{}).title,/New entries paused/);
  }
  assert.ok(portfolioView({...base,entry_allowance:allowance}).families.every(f=>f.status==='On · entries enabled'));
});
test('initial document does not report Live Off before saved permission arrives',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  const live=html.match(/<button id="live-status"[^>]*>[\s\S]*?<\/button>/)[0];
  assert.match(live,/disabled/);
  assert.match(live,/Checking/);
  assert.doesNotMatch(live,/<strong>Off<\/strong>/);
});
