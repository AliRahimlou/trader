import test from 'node:test';
import assert from 'node:assert/strict';
import {appStatus} from './model.mjs';

const now=Date.parse('2026-09-17T17:30:00Z');
const state={live_enabled:true,analysis_at:new Date(now).toISOString(),account_at:new Date(now).toISOString(),
  clock:{is_open:true},execution:{message:'Old strategy waiting message'},
  data_health:{vix:{status:'current',valid_until:new Date(now+60000).toISOString()}},
  deployment_gate:{configured:true,locked:false,hold_present:false,hold_valid:false}};

test('exclusive deployment lock pauses new entries without relabeling saved Live money',()=>{
  const snapshot={...state,deployment_gate:{...state.deployment_gate,locked:true}};
  const before=structuredClone(snapshot);
  const status=appStatus(snapshot,now);
  assert.equal(status.title,'Live money on · entries paused for update');
  assert.match(status.text,/temporarily holding new entries/);
  assert.match(status.text,/saved Live money setting is unchanged/);
  assert.deepEqual(snapshot,before);
  assert.equal(appStatus({...snapshot,live_enabled:false},now).title,'New entries paused for update');
});

test('durable hold remains visible after updater lock is released',()=>{
  const status=appStatus({...state,deployment_gate:{...state.deployment_gate,hold_present:true,hold_valid:true}},now);
  assert.match(status.title,/entries paused for update/);
  assert.match(status.text,/Existing positions continue their exits/);
});

test('malformed hold and unavailable lock cannot be described as watching for a setup',()=>{
  const malformed=appStatus({...state,deployment_gate:{...state.deployment_gate,hold_present:true}},now);
  assert.match(malformed.text,/hold needs verification/);
  const unavailable=appStatus({...state,deployment_gate:{...state.deployment_gate,locked:true,error:'entry_gate_unavailable'}},now);
  assert.match(unavailable.text,/cannot verify its update lock/);
  for(const status of [malformed,unavailable]){
    assert.match(status.title,/entries paused for update/);
    assert.doesNotMatch(status.text,/Old strategy waiting message/);
  }
});

test('active positions and policy review retain priority over the deployment pause',()=>{
  const paused={...state,deployment_gate:{...state.deployment_gate,hold_present:true,hold_valid:true}};
  assert.deepEqual(appStatus({...paused,review_required:true,execution:{trade:{stage:'open'},message:'Managing stop'}},now),
    {title:'QQQ · open',text:'Managing stop'});
  assert.equal(appStatus({...paused,review_required:true},now).title,'Review updated live rules');
  assert.equal(appStatus({...paused,execution:{review_required:true}},now).title,'Review updated live rules');
});

test('cleared unconfigured and absent deployment gates preserve ordinary status',()=>{
  for(const deployment_gate of [undefined,state.deployment_gate,{configured:false,locked:true,hold_present:true}]){
    assert.equal(appStatus({...state,deployment_gate},now).title,'Live money on · watching for a setup');
  }
});
