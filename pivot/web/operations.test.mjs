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
test('direction note explains the PSQ short proxy and fractional purchases without raising the target',()=>{
  const state={settings:{target_dollars:'5.00'},quote_health:{ask:700}};
  const note=operationStatus(state).directionNote;
  assert.match(note,/Short setups buy PSQ, the inverse Nasdaq-100 ETF/);
  assert.match(note,/\$5\.00 buys a fraction of a share/);
  assert.doesNotMatch(note,/whole share|cannot open a short/);
  assert.equal(state.settings.target_dollars,'5.00');
  assert.doesNotMatch(operationStatus({...state,quote_health:{}}).directionNote,/fraction of a share/);
});
test('timing note carries the 4.5 rules, not the retired same-day windows',()=>{
  const note=operationStatus({}).timingNote;
  assert.match(note,/end of the next session/);assert.match(note,/within 0\.4%/);assert.match(note,/last 30 minutes/);
  assert.doesNotMatch(note,/11:30|180|15-minute/);
});
test('unconfirmed exit remains prominent after Live pauses and across snapshots until resolved',()=>{
  const state={live_enabled:false,execution:{trade:{stage:'exiting',exit_pending:{
    first_observed_at:new Date(now-60000).toISOString(),raised_at:new Date(now-30000).toISOString()}}}};
  assert.match(operationStatus(structuredClone(state),now).incident,/Exit needs attention/);
  assert.match(operationStatus(state,now).incident,/without sending a competing order/);
  state.execution.trade.exit_pending.resolved_at=new Date(now).toISOString();
  assert.equal(operationStatus(state,now).incident,'');
  assert.equal(operationStatus({...state,execution:{trade:null}},now).incident,'');
});
test('account shorting restriction remains visible even with a whole-share target',()=>{
  const state={account:{shorting_enabled:false},settings:{target_dollars:'800'},quote_health:{ask:700}};
  assert.match(operationStatus(state).directionNote,/disabled on this Alpaca account/);
  assert.doesNotMatch(operationStatus({...state,account:{shorting_enabled:true}}).directionNote,/disabled/);
  assert.doesNotMatch(operationStatus({...state,account:{}}).directionNote,/disabled/);
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
