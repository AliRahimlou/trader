# Persisted decision evidence

The research branch adds observational traces to each completed analysis refresh. This is software instrumentation; it does not change signal conditions, saved settings, permissions, orders or deployment.

## Remote review

The hosted `GET /api/decisions` route returns saved history through the same password-protected Caddy route as the rest of the app. It performs no provider request or broker action. Pages default to 50 entries, accept at most 100, and return `next_before_id` for the next page. Pass that value as `before_id`; entries are ordered by descending insertion ID. Repeated evidence updates its original row, so use each row's observation timestamps rather than treating insertion order or polling count as independent opportunities. `historical_only` is always true; saved permission or freshness never authorizes a current order.

## What is recorded

`snapshot.decision_trace` contains:

- The actual analyzer result, its ordered checks, and the exact first failed **signal** check. A missing Nasdaq input is explicitly identified.
- `signal_qualifies`, separately from data-health readiness and effective Live money permission. `broker_admission_evaluated` and `order_authorized_by_trace` are always false. The existing executor remains responsible for broker admission.
- Nasdaq eligible zones, event time, reference entry/stop/target, source receipt and latest completed candle times.
- All seven leaders, including those the analyzer never reached: zones, reaction, reaction candle, age, no-zone/missing-data reason, and the latest two candles. `age_minutes` is measured from the latest completed leader candle; `age_at_observation_minutes` is elapsed wall-clock age at collection.
- Actual-VIX freshness, areas, observed reactions, expected direction, and whether the actual analyzer reached its VIX gate. Evidence collected after an earlier failed gate is observational and is not labeled as an admitted signal.
- Structural data-health gaps, timestamps and the existing VIX request allowance. It does not request extra data or quotes.

Raw account data, account identifiers, provider errors, credentials and quote payloads are excluded. The account observation timestamp, broker clock timestamp and regular-session flag provide limited operational context without recording account identity. Receipt times are not substituted for candle times.

## Persistence and deduplication

`Store` migrates existing audit ledgers by adding a separate `decision_traces` table and recency index. Existing settings, controls, events and trades are unchanged. A fifteen-minute checkpoint plus a canonical evidence fingerprint identifies one state. Repeated refreshes increment `observation_count` and update the latest trace body/receipt time; their original first-observed timestamp remains available.

Polling-clock fields are excluded from the fingerprint. Candle times, zone inventories, OHLC values, vote ages relative to the latest candle, gate results, health status, request allowance and effective permission changes are included. Thus data becoming stale or a provider correcting a candle produces new evidence, while another receipt of the same current data does not inflate opportunity counts.

Retention is capped at **24,000 distinct states**, enough for more than sixty regular sessions at one changed state per minute. Typical unchanged markets create approximately 26 regular-session checkpoints daily. Exceptional state changes and after-hours checkpoints also consume retention. Trace counts are diagnostic observations, never a claim about independent trades or setups. The stored body has a 512,000-byte ceiling; exceeding it surfaces an error rather than silently dropping parts of an explanation.

A restart restores the last saved trace. Snapshot fields `matches_current_analysis` and `current_at_snapshot` distinguish fresh collection from restored/stalled evidence. These fields only describe trace collection freshness; they do not override the independently recorded market-data health or broker readiness.

Trace generation/read/write failures set a dedicated sanitized `diagnostic_error`. They preserve the actual setup and execution permission. The last saved trace can remain visible with its original timestamp and `current_at_snapshot=false`; it is not relabeled as newly collected.

## Validation

The focused offline tests cover source result equality before/after tracing, ready signal versus order authority, exact first failure, unreached VIX evidence, missing/stale data, secret exclusions, repeated-state deduplication, new evidence and new checkpoints, existing-ledger migration, restart restoration, bounded retention, snapshot exposure and sanitized generation/write failures. Tests block real HTTP calls. No paper or live execution is demonstrated by these tests.

```sh
/Users/alirahimlou/myapps/trader/.venv/bin/python -m pytest pivot/tests/test_decision_diagnostics.py
```

The production release must still be reviewed and authorized separately; this task did not deploy it.
