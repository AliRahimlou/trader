# Current-method experiment — September 18, 2026

## Result

The corrected offline replay completed, but **does not establish profitability or choose a winning strategy**. The current five-agree/one-opposing policy produced three distinct prior-day sweep events across six sessions. Saved execution prices are too coarse to test whether those opportunities could have been entered before their deadlines. All cost scenarios therefore contain zero simulated trades; zero P&L and drawdown describe an empty simulation, not evidence of safe or profitable trading.

The two faster research candidates are implemented and tested with manufactured native-five-minute fixtures. Their historical evaluation remains blocked by missing genuine five-minute QQQ and actual VIX history in the saved inputs. No fifteen-minute candle was split into invented five-minute prices.

## Frozen experiment and data

- Engine: commit `a5e3936985be92a84e95b59463356452f5d7f4b4`, analysis version `nasdaq-video-interpretation-v3`. The freeze includes strategy, models, feeds, history-health and data-health modules.
- Replay loader: the corrected current-v3 contract requires leader five-minute frames; legacy v2 retains its own frame requirements. Loader SHA-256: `ea8cf4b4c47a31907e343e4843c95ef88624baa022c6b969a8666ef817bfc289`.
- Plan: [current-method-plan.json](../../research/current-method-plan.json), version `2026-09-18-current-methods-v2`, SHA-256 `ca60470256da58cf24decc3ce4517d5eb49c09a4b539ada57ff02547adc5f943`.
- Real saved sources: Alpaca IEX QQQ fifteen-minute candles and native five-minute leader candles, plus cached InsightSentry actual `CBOE:VIX` fifteen-minute history. The VIX metadata identified zero delay when received. Historical per-checkpoint arrival times and executable quotes are **not** available. Receipt times in replay are simulated.
- Native input SHA-256: `8e8c2fbc804384a6ff5678fdbeb7f3d1570ad5e4e10e8d63c4ec2c4a2db17653`; warmup: `bfaa8bb54436071d05117b1629589710d936f9f15ccc5ac2ef423f7835775744`; older prefix/VIX input: `d30adb813a1aa60ac4585fcfd30edacd7aca578ee6fe550d144418d5988ff9f1`.
- Completed private run: `runtime/research/current-method-experiment-20260918-v2`. Its manifest records all input, source and output hashes; completed report SHA-256: `4d3464d4fc1713c139ef38849afdc3947124b1527a1fd7ca131282b968a795a4`.

The earlier `v1` attempt stopped on a replay calendar-scoping defect before producing a final report. A focused regression test now verifies the scoped calendar. The corrected `v2` attempt completed without changing any hashed source during execution. That first failure is retained in the local experiment history rather than presented as a completed trial.

## Time split and observations

The first three sessions — September 9, 10 and 11, 2026 — form development. September 14, 15 and 16 form **known-history validation**. These dates had already been inspected before this experiment, so neither partition is an untouched holdout.

There were 462 five-minute evaluation checkpoints per policy, with a declared 60-second publication delay and no evaluation at/after session close. All 462 passed the corrected complete-history contract. Passing this contract does not imply that a trade setup qualified or that execution data are sufficient.

| Policy | Independent method | Qualified observations | Distinct event IDs | Long / short observations | Executable observations |
| --- | --- | ---: | ---: | ---: | ---: |
| Five agree, at most one opposing | Four-hour area / hourly retest | 0 | 0 | 0 / 0 | 0 |
| Five agree, at most one opposing | Prior-day sweep | 6 | 3 | 4 / 2 | 0 |
| Four agree, none opposing | Four-hour area / hourly retest | 1 | 1 | 0 / 1 | 0 |
| Four agree, none opposing | Prior-day sweep | 9 | 4 | 4 / 5 | 0 |

An observation is a checkpoint at which a candidate qualified; repeated observations of the same event are not additional independent trades. Distinct event IDs are useful deduplication units, not a claim of statistically independent market outcomes. Methods were evaluated separately and must not be summed as a shared-capital portfolio. Changing both quorum and allowed opposition is a policy comparison, not an isolated estimate of either component's effect.

For the current five/one sweep method, development contained two observations from one event; known-history validation contained four observations from two events. The control's retest event occurred in development. Its sweep method had four observations/two events in development and five observations/two events in known-history validation.

## Why no simulated fills were credited

Each qualified observation expired 90 seconds after its simulated receipt in this run. The first available future QQQ execution opening in the saved fifteen-minute series occurred after that deadline. The replay rejected those observations as `available_open_after_signal_expiry` rather than reusing an earlier bar's opening or inventing an intrabar quote.

For example, the current policy qualified a sweep at 11:06 New York on September 11. Its earliest validity deadline was 11:07:30; the next saved QQQ bar opening was 11:15. That dataset cannot establish an admissible fill between 11:06 and 11:07:30. It does not establish that the live market lacked an executable quote then.

The fractional research executor is long-only. Short observations are reported to describe strategy qualification; they would not be traded by this executor even with adequate prices. This run is not a futures, short-stock or options simulation.

## Costs and latency

Every method/policy/partition was evaluated under all three declared assumptions with a synthetic $100 research budget and $5 purchase amount:

| Scenario | Spread assumption | Slippage assumption | Fee per order | Additional latency |
| --- | ---: | ---: | ---: | ---: |
| Base | 2 basis points | 2 basis points | $0.01 | 0 seconds |
| Higher costs | 4 basis points | 5 basis points | $0.02 | 0 seconds |
| Delayed entry | 2 basis points | 2 basis points | $0.01 | 60 seconds |

These are research assumptions, not advertised broker fees or observed quotes. With no admissible entries, all 24 scenario/partition combinations had zero trades. Their identical results **do not demonstrate cost robustness**. Separate fixture tests verify that higher costs reduce net results, latency respects deadlines, insufficient capital rejects an exact-size purchase, and ambiguous stop/target candles use conservative stop-first treatment.

## Implemented alternatives and remaining evidence

The precise source distinctions and assumptions are in [current-method-specifications.md](current-method-specifications.md). The isolated [five-minute candidate module](../../research/five_minute_candidates.py) has no broker or account interface and cannot authorize orders.

| Candidate | Implemented behavior | Historical status |
| --- | --- | --- |
| F1: closed five-minute location | Existing higher-timeframe levels; later five-minute retest or immediate completed-bar prior-day sweep; current leader rule; opposing native five-minute VIX reaction; explicit publication delay and validity deadlines | Blocked: genuine QQQ five-minute and actual VIX five-minute inputs absent from this dataset |
| X1: VIX opposing-pivot exit | Freeze an opposing VIX area known at entry; require a subsequent completed five-minute wick touch; exit only at a later observed QQQ opening; gap-stop and conservative OHLC ordering remain active | Blocked: same native inputs absent; historical trade/exit comparison not performed |

The creator's [five-minute Nasdaq/VIX lesson](https://www.youtube.com/watch?v=egiUVtmGlZs) motivates F1. His [April 8 stream at 3:03–3:20](https://www.youtube.com/watch?v=899Ojnsl18A&t=183s) motivates X1. The precise formulas are app research hypotheses. Neither source establishes the five/one quorum or proves these mechanical rules profitable.

Before a historical comparison can be completed, capture authentic native candles with sufficient warmup, verified source identity/resolution and exchange-session coverage. Finer execution observations must fit each signal's deadline; merely obtaining five-minute candles does not guarantee that. Quote timestamps, receipt delays, realized slippage and partial-fill behavior remain separate evidence requirements. An untouched future test was not performed; the written plan reserves at least 40 sessions and 60 completed distinct setups before consideration, without retuning that holdout. Those minimums alone would not establish an edge.

### Optional follow-on path

The separate [native-method-plan.json](../../research/native-method-plan.json), version `2026-09-18-native-methods-v3`, and [native evaluation runner](../../research/native_method_experiments.py) are implemented. They preserve this completed v2 result and require a new output directory. The native loader checks actual-index identity, five-minute resolution, split-adjusted IEX identity, calendar agreement and complete warmup. Baseline results and frozen input/engine identities are hash-checked before reuse. Publication delays remain modeled; partial native sessions are not used as full-session comparisons.

For X1 the comparison fixes each admissible baseline entry and quantity, evaluates the alternate exit with costs, and does not reinvest proceeds. It therefore evaluates matched exits rather than asserting a full alternative portfolio return. Missing VIX suppresses that discretionary exit while leaving the QQQ stop/target active.

**The follow-on historical run was not performed.** The required native dataset was not captured in this work. Eleven additional manufactured-fixture tests passed for the loader, timing/coverage checks, matched exits, costs and end-to-end split/deadline orchestration. No provider response, real candidate count or real performance result is inferred from those fixtures. This optional path does not change the completed v2 report hash or its conclusions.

## Verification

The two new research test files passed **29 tests**, including both methods and directions, missing/proxy/stale/gapped VIX rejection, no future-bar confirmation, publication timing, frozen exit levels, entry-spanning-bar exclusion and execution deadlines. The full research suite then passed **164 tests**. These establish implementation behavior for tested cases, not profitability or production broker commissioning.

The replay made no network requests, account reads or orders. Live rules and settings were not changed by this experiment. Simulation differs from live execution, as described by [Alpaca's paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading); repeated selection on inspected history also risks overfitting, as discussed in [Bailey et al.](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
