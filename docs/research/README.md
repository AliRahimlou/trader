# Research release: reproduce, review, and promote

Start with [the result report](REPORT.md), [primary-source rules](source-rule-map.md), and [execution validation](execution-validation.md). The original research was completed locally at `c3e41c3` without a push or deployment. The owner subsequently requested a $5 production release; see the [production review plan](../production-review-plan.md) for that later scope and the remaining fidelity limitations.

## Reproduce locally

Run from the isolated checkout. Install the locked dependencies in a fresh virtual environment if needed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r deploy/requirements.lock
.venv/bin/python -m pytest
node --test pivot/web/model.test.mjs
```

During this task the existing `/Users/alirahimlou/myapps/trader/.venv/bin/python` supplied the same Python dependencies; the worktree has no copy of live credentials or runtime database. Research tests are included in the default suite and release image test stage. The final runtime image does not contain the research package.

### Exact replay of the frozen dataset

Use a separate checkout of research commit `c3e41c3` for byte-identical reproduction of the original report. Later release commits correct target selection and add diagnostics; their outputs must be treated as a new run, not relabeled as the old frozen implementation.

```sh
.venv/bin/python -m research.verify_baseline \
  --dataset runtime/research/dataset-v1.json.gz \
  --out runtime/research/baseline-parity-repeat.json
.venv/bin/python -m research.run \
  --dataset runtime/research/dataset-v1.json.gz \
  --out runtime/research/repeat
```

Choose a new output directory: the runner refuses to overwrite experiment records. It blocks socket/HTTP connections and never reads credentials, instantiates the live executor, or submits orders. The original pure analyzer is retrieved from its recorded local Git commit solely for the parity comparison.

The immutable local dataset SHA-256 is `d30adb813a1aa60ac4585fcfd30edacd7aca578ee6fe550d144418d5988ff9f1`. Keep this file to reproduce the exact run; a later provider fetch may revise bars or corporate-action adjustments. Raw market data and videos remain local and are excluded from Git. The committed experiment plan, code, summary and source manifests are reviewable separately.

### Recollect an explicitly new dataset

```sh
.venv/bin/python -m research.collect \
  --env-file /path/to/private/Alpaca.env \
  --vix-cache /path/to/existing/vix-insight.sqlite3 \
  --out runtime/research/new-dataset.json.gz
```

This command only allows authenticated GET requests for Alpaca's calendar and stock bars. It reads the VIX SQLite database in read-only mode and performs **zero VIX API requests**. It does not copy keys, fetch account/order data, or update the provider quota ledger. A new collection is a new dataset, not an identical reproduction.

### Inspect decisions and experiments

`runtime/research/run-reviewed-v1/` contains:

- `decisions.jsonl`: every variant/checkpoint, exact blocking gate, eligible leader zones, current/persistent votes and ages, VIX evidence, and distinct live-versus-simulation permission.
- `experiments.jsonl`: all 144 predeclared variant/scenario/period combinations, including empty results. No discarded winners or adaptive combinations.
- `results.json`: cumulative gate funnels, chronological splits, fixed-policy expanding walk-forward results, period/regime metrics and benchmarks.
- `*-execution.json`: deterministic assumed fills, costs, rejects, equity and exposure. These are simulated events.
- `examples.json`: first observed examples at each failure/qualification stage. No empirical accepted trade exists in this run.
- `run-manifest.json`: dataset, plan, source and output hashes.

`run-v1` is retained but superseded by independent-review fixes to identity/funnel/config handling. `run-v2` verifies those fixes; `run-reviewed-v1` uses the final disconnected runner and documented source hashes. These are implementation corrections under the same frozen experiment plan, not retuning after a losing result.

The positive example in `research.synthetic_example.accepted_long()` is explicitly manufactured. It verifies that the shared analyzer can accept a complete long setup; it is not a source-video trade or evidence of returns.

## Forward evidence required

The existing cache contains 15-minute actual VIX, not the historical five-minute leader/VIX evidence needed to reproduce the visible examples. Obtain timestamped five-minute actual-index data and clarify the creator's leader-zone construction/session convention before naming any variant source-faithful. We have not substituted VIX futures, ETFs or CFDs.

Preserve point-in-time shadow decision traces, candle receipt/source timestamps, failed checks and hypothetical entries under one frozen policy. The new app trace recorder is prepared in this branch but has not been installed on AllSpark. Count distinct Nasdaq events and trading days; repeated checks and seven correlated companies do not become independent observations.

An untouched final period must begin after the policy is frozen, with at least 40 sessions and 60 independent opportunities. If such opportunities do not occur, evidence remains insufficient. Do not select a new rule by repeatedly checking that test period. All observed historical volatility here is below VIX 20; high-volatility robustness is untested.

## Promotion sequence and owner review

1. **Source review:** resolve leader-zone construction, five-minute examples and session alignment; keep proposed enhancements labeled separately.
2. **Historical and untouched evaluation:** meet the frozen sample, multiplicity-adjusted after-cost expectancy, adverse-cost, parameter-stability and best-day-removal requirements. Current candidates fail promotion for lack of evidence.
3. **Approve loss limits:** $25 is a purchase target. Proposed, unapplied ceilings are $0.50 planned stop risk, $1 daily realized-plus-unrealized loss and $3 drawdown, with one position. Reject oversized-risk setups instead of silently changing the purchase amount. Stops cannot guarantee these loss amounts.
4. **Shadow operation:** validate coverage, timestamp ordering, missing-data behavior and actual independent opportunities. New live orders remain disabled for the candidate.
5. **Paper commissioning:** use a separately reviewed, endpoint-locked paper harness and separate storage/credentials. The current live executor intentionally refuses paper accounts; do not relabel a paper account as live. Demonstrate 20 complete cycles, including five recovery cases, with no unexplained shares or orders. No paper credentials or broker paper cycles were available for this task.
6. **Owner-authorized live trial:** only after the preceding gates, explicit authorization and broker reconciliation; retain the small approved amount. Commissioning evidence is distinct from an edge, and neither supports automatic size increases.

## Rollback and incident handling

- Pause new entries on rejected/unconfirmed protection, unknown submissions, mismatched shares/account, stale required data, or an approved loss-limit breach. Some triggers exist in code; proposed financial loss limits remain unapplied pending review.
- Preserve and reconcile any existing position and broker-held stop. Turning entry permission Off does not abandon management. If cancellation or fills are unknown, inspect Alpaca; do not send a competing sale or retry an unknown purchase.
- Do not replace the worker or restore an old ledger while a trade is unresolved. Never reintroduce the retired local runner.
- For a code rollback, first require a fresh broker account/clock with zero positions, zero open orders and no active managed trade. Save a consistent SQLite backup. Use the last known image through the existing serialized updater, retaining current durable order/permission history. Verify revision and health before owner activation.
- Do not restore a pre-trade ledger to hide a failed deployment; that could erase submission knowledge. Keep unknown/rejected intent evidence until reconciled.
- This branch's execution policy version changed to require review of its new protection behavior. No permission record on AllSpark was changed. Do not merge to watched `main` as a shortcut around release approval.

## What this release establishes

Improved diagnostic and execution software, point-in-time replay tooling, primary-video evidence, and an honest negative/inconclusive research result. It does **not** establish a profitable strategy, actual fractional broker-stop acceptance, successful live fills, or proven loss bounds.
