import test from 'node:test';
import assert from 'node:assert/strict';
import {loggingStatus} from './model.mjs';
const now=Date.parse('2026-09-17T17:30:00Z');
const record={persisted:true,current_at_snapshot:true,captured_at:new Date(now).toISOString()};
const state={decision_trace:record,execution_check:{...record,from_current_process:true}};
test('saved current checks can be recording while the strategy still waits',()=>{
  const status=loggingStatus({...state,live_enabled:true,setup:{state:'AT_LEVEL'}},now);
  assert.equal(status.label,'Recording');
  assert.match(status.detail,/does not mean an entry has qualified/);
});
test('missing restored stale future and failed execution records never claim recording',()=>{
  const invalid=[undefined,{...record,from_current_process:false},
    {...record,from_current_process:true,captured_at:new Date(now-31000).toISOString()},
    {...record,from_current_process:true,captured_at:new Date(now+1000).toISOString()},
    {...record,from_current_process:true,persisted:false}];
  for(const execution_check of invalid) assert.notEqual(loggingStatus({...state,execution_check},now).label,'Recording');
  assert.equal(loggingStatus({...state,execution_diagnostic_error:'Unable to save'},now).label,'Needs attention');
});
test('stalled signal recorder remains visible even while execution keeps recording',()=>{
  assert.equal(loggingStatus({...state,decision_trace:{...record,captured_at:new Date(now-91000).toISOString()}},now).label,'Checking records');
  assert.equal(loggingStatus({...state,diagnostic_error:'Unable to save'},now).tone,'error');
});
