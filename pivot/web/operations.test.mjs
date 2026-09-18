import test from 'node:test';
import assert from 'node:assert/strict';
import {operationStatus, sessionReview, vixStatus} from './model.mjs';

const now=Date.parse('2026-09-18T16:00:00Z');
test('worker liveness and archive freshness are independent of provider readiness',()=>{
  const state={worker_health:{ready:true,workers:[{name:'data',status:'running'}]},
    data_health:{ready:false},input_archive:{status:'recording',captured_at:new Date(now).toISOString()}};
  assert.equal(operationStatus(state,now).workerLabel,'Running');
  assert.equal(operationStatus(state,now).archiveLabel,'Recording inputs');
  assert.equal(operationStatus(state,now+91000).archiveLabel,'Waiting for inputs');
  state.worker_health={ready:false,workers:[{name:'execution',status:'stalled'}]};
  assert.equal(operationStatus(state,now).workerLabel,'Needs attention');
});
test('partial entry incident remains visible until durable reconciliation',()=>{
  const partial={raised_at:new Date(now).toISOString(),filled_qty:'0.1'};
  const state={execution:{trade:{partial_entry:partial}}};
  assert.match(operationStatus(state,now).incident,/0.1 shares/);
  partial.resolved_at=new Date(now+30000).toISOString();
  assert.equal(operationStatus(state,now+30000).incident,'');
});
test('small targets explain whole-share short constraint without raising the target',()=>{
  const state={settings:{target_dollars:'5.00'},quote_health:{ask:700}};
  assert.match(operationStatus(state).directionNote,/cannot open a short/);
  assert.equal(state.settings.target_dollars,'5.00');
  assert.doesNotMatch(operationStatus({...state,quote_health:{}}).directionNote,/cannot open a short/);
});
test('historical funnel preserves distinct-event counts and partial-history warning',()=>{
  const value=sessionReview({session_review:{status:'available',events_seen:3,checkpoints:70,
    complete_candidate_coverage:false,latest_execution_blockers:{purchase_size:1},
    latest_signal_blockers:{'Magnificent Seven at their zones':2},orders:{confirmed_entries:0}}});
  assert.equal(value.events,3);
  assert.equal(value.checkpoints,70);
  assert.match(value.detail,/partial/);
  assert.equal(value.blockers[0].name,'purchase size');
  assert.equal(sessionReview({}).available,false);
});
test('VIX display exposes validated recovery times without declaring quote approval',()=>{
  const value=vixStatus({verification:{retry_at:'2026-09-18T16:00:30Z',entry_quote_retry_at:'invalid'}},now);
  assert.equal(value.retryAt,'2026-09-18T16:00:30Z');
  assert.equal(value.quoteRetryAt,null);
  assert.equal(value.label,'Waiting');
});
