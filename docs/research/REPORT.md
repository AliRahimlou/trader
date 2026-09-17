# Pivot research result — September 17, 2026

## Decision

**Improved software and reproducible diagnostics. No demonstrated trading edge. Do not promote a different strategy to live trading.**

The baseline and seven predeclared alternatives produced **zero trades over nine usable historical sessions**. There is no trade expectancy estimate or meaningful profit confidence interval. Staying in cash during this short sample is not evidence of a repeatable active-trading advantage.

All work is isolated in `codex/research-validation-20260917`, at `/Users/alirahimlou/myapps/trader-research-20260917`. The hosted revision was verified as `facc507ae2761a76916031994bcf9f956f9369fe`; the original checkout started at the same revision. The original checkout's untracked `launchd/` was preserved. No code was pushed, deployed, or used to send a broker order. No live permission, trade amount, credential, or production ledger was changed.

## What was verified

At 15:27 UTC / 11:27 a.m. Eastern, authenticated hosted inspection showed Live permission On, the $25 purchase target, current equity and actual-VIX data, no account/data errors, no active trade, and zero open positions/orders. Separate same-account Alpaca GETs at 15:16 UTC confirmed zero orders of any status and zero fills that day. QQQ was active, tradable and fractionable; account shorting was disabled. These are dated observations, not a successful execution cycle. LAN SSH was unavailable; protected public API state and same-account broker GETs supplied current evidence, rather than the retired local ledger or unavailable host logs.

A final read-only check at **12:07 p.m. Eastern** still showed the same deployed revision, Live On, $25 target, current VIX, zero positions/open orders and no account/data errors.

The source clips were actually inspected at timestamped chart frames, alongside retained transcripts. Key differences from the app:

- Leaders visibly use **five-minute** candles in V3; the app uses fifteen-minute reactions.
- Nasdaq areas are manually marked through repeated touches **or crossings**. The app only clusters repeated confirmed swing extrema.
- The videos do not show how the leaders' colored areas are constructed. Repeated four-hour leader zones are an app assumption.
- Full-session/ETH settings are visible; the app uses regular hours anchored at 09:30 Eastern and omits the partial afternoon four-hour bucket.
- Numeric zone width, participation/neutral-vote treatment, vote expiry, exits and sizing are not fully specified by the clips.

See the [timestamped source-rule comparison](source-rule-map.md) and [annotated original frames](../../runtime/research/video-evidence/annotated-examples.html). No source-faithful winner is claimed; five-minute historical VIX and clarified zone/session rules are missing.

## Why the current method is inactive

The measured baseline funnel used **234 repeated checkpoints**, representing **18 distinct Nasdaq event timestamps**, not 234 independent opportunities:

| Cumulative stage | Checkpoints surviving |
| --- | ---: |
| Complete required input history | 234 |
| Current completed hourly Nasdaq observation | 207 |
| Premarked levels | 207 |
| Nasdaq level event | 70 |
| Required leader confirmation | 0 |
| Completed entry/protection/exit | 0 |

Twenty-seven checks occurred before the day's first completed hourly candle: under this convention it closes at **10:30**, so no entry is possible in the first hour. That is a rule limitation, not a stalled feed. At every baseline checkpoint at least one company had no eligible repeated-pivot zone. Among the 70 event checks, 56 had zero agreeing votes, 13 had one, and one had two; none had four. Persistent confirmation alone did not overcome the sparse/remote zone definition. Broader bands changed detected areas but still did not produce a complete setup.

VIX availability was audited and VIX reactions were recorded independently. Since the leader gate never passed, this sample cannot establish the downstream VIX, exit or fill quality of the strategy.

## Frozen experiments and results

The plan was committed as `c5894bd` **before candidate evaluation**: eight total policies, seven one-factor alternatives, no adaptive combinations, zero paid data and zero additional VIX calls. Zone alternatives affect Nasdaq/leader construction; the VIX rule stays fixed. Persistence is tied to the same event and session, expires after the declared candle count, and is invalidated by a newer opposite reaction or close through its zone. It cannot collect old votes from unrelated events.

| Variant | Distinct Nasdaq event times | Leader checks passing | Trades | Net after assumed costs | Expectancy / uncertainty |
| --- | ---: | ---: | ---: | ---: | --- |
| baseline | 18 | 0 | 0 | $0.0000 | N/A: no trades |
| zone_20bp | 16 | 0 | 0 | $0.0000 | N/A: no trades |
| zone_30bp | 17 | 0 | 0 | $0.0000 | N/A: no trades |
| persistence_2bars | 18 | 0 | 0 | $0.0000 | N/A: no trades |
| persistence_3bars | 18 | 0 | 0 | $0.0000 | N/A: no trades |
| leaders_3 | 18 | 0 | 0 | $0.0000 | N/A: no trades |
| opposition_1 | 18 | 0 | 0 | $0.0000 | N/A: no trades |
| entry_drift_50bp | 18 | 0 | 0 | $0.0000 | N/A: no trades |

The simulated drawdown, worst day, exposure, turnover and losing streak are all zero for these inactive policies. Win rate, average win/loss and expectancy are **undefined**, not successful. All six execution scenarios—base costs, higher costs, extra delay, partial fill, rejected entry and failed protection—are retained. Their zero-trade outcomes **do not constitute stress-test success**. No combination was added after seeing these results.

### Benchmarks on the same $92.05 starting capital and $25 exposure target

| Method | Completed trades/investments | Net after costs | Return on starting capital | Observed drawdown |
| --- | ---: | ---: | ---: | ---: |
| Cash, without interest | 0 | $0.0000 | 0.0000% | $0.0000 |
| Baseline / each candidate | 0 | $0.0000 | 0.0000% | $0.0000 |
| QQQ buy-and-hold, remainder cash | 1 continuous investment | $-0.2487 | -0.2702% | $0.6979 |
| Simple first-positive-hour momentum | 9 | $-0.3714 | -0.4035% | $0.5786 |

The momentum reference had 33.3% winners, average win $0.1110, average loss $-0.1174, mean net per trade $-0.0413, worst day $-0.2951, and a 4-trade losing streak. Removing its best trade/day leaves $-0.5395. Turnover was $449.80; average bar-end invested capital was $16.79, with 67.1% of bar-end observations exposed. Buy-and-hold retains overnight risk and 100% session exposure; the candidate has none. These differences matter: cash is not an active strategy with demonstrated superior expectancy.

Base costs assume a 2-basis-point full spread, 2 basis points of slippage each side, and $0.01 per filled order. These are **hypothetical research costs**, not observed Alpaca charges or fills. The momentum reference incurred $0.1800 in fee assumptions and $0.1349 in spread/slippage assumptions. At $25, cents matter. Current incremental subscription/hosting spend is $0; electricity is unknown. A future $5 or $20 monthly recurring bill requires that much additional net trading profit; the inactive candidates cover neither.

### Chronological splits

| Period | Baseline and every candidate | QQQ hold | Momentum reference |
| --- | ---: | ---: | ---: |
| development (2026-09-03, 2026-09-04, 2026-09-08, 2026-09-09, 2026-09-10) | $0.0000 | $-0.1071 | $-0.1855 |
| validation (2026-09-11, 2026-09-14, 2026-09-15, 2026-09-16) | $0.0000 | $-0.4097 | $-0.1858 |

Each phase starts with $92.05 and resets exposure. Separate buy-and-hold phase returns are not additive to the continuous full-period investment. Expanding fixed-policy walk-forward evaluation after the first four sessions is retained for every variant; no fitting used a future evaluation day and all strategy positions close within each session.

Nine sessions and no candidate trades are insufficient for uncertainty estimation. The tooling groups all trades in the same day for bootstrap intervals; its normal minimum is 20 sessions, while promotion requires at least 40 and 60 independent opportunities. Seven comparisons use a family-wise alpha of 0.05 (per-comparison confidence approximately 99.286%). This cannot correct every interday dependence or source-selection bias. No interval is reported here. All prior-session VIX regime labels are below 20; both prior-day trend signs occur, but high-volatility behavior is untested.

**The final untouched test has not been run.** Historical dates already seen in videos/diagnostics are not relabeled as untouched. Future data must be collected under a frozen policy without repeated tuning.

## Annotated decision examples

**Observed rejection A — 2026-09-03T10:31:00-04:00:** the app recognized `sweep and reclaim` but withheld entry at `Magnificent Seven at their zones`. Its leader evidence was: AAPL: no zone reaction, MSFT: no zone reaction, NVDA: no eligible zones, AMZN: no zone reaction, META: no zone reaction, GOOGL: no zone reaction, TSLA: no eligible zones. This is a subsequently reconstructed market decision, not a captured live order attempt.

**Observed rejection B — 2026-09-04T10:31:00-04:00:** the strongest baseline agreement at a Nasdaq event was still only two leaders. Evidence: AAPL: no zone reaction, MSFT: no zone reaction, NVDA: no eligible zones, AMZN: short, META: no zone reaction, GOOGL: short, TSLA: no eligible zones. The rule withheld entry; subsequent profits or losses cannot be inferred from that rejection.

**Accepted logic fixture — synthetic only:** `research.synthetic_example.accepted_long()` deliberately constructs seven long leader reactions, opposite VIX behavior and valid stop/target geometry. The shared analyzer returns `SETUP_READY`, but `can_enter` and trace order authorization remain false. This demonstrates logical reachability, not real-market frequency, a source-video trade, or profits. There was no empirical accepted trade to illustrate, and none was invented. See [the manufactured fixture output](../../runtime/research/synthetic-positive-example.json).

## Implemented improvements

1. **Decision evidence:** a compact trace of zones, each company's vote and age, VIX evidence, input timestamps and exact blocker. Durable deduplication distinguishes repeated polls from changed decisions. Snapshots expose restored/stale trace status and sanitized diagnostic failures without changing trading permission.
2. **Research tooling:** strict calendar/OHLC validation; identical default analyzer; completed-bar, session/DST and future-pivot checks; event-bound persistence alternatives; explicit long-only fractional simulation; immutable plans/data hashes; complete experiment records.
3. **Execution protection:** asynchronous order rejections now pause new entries. Unconfirmed protection has a persisted 30-second proposed acceptance deadline; suspended/expired/unknown protection triggers a safe pause and reconciliation. The worker never submits a competing sale before the original protection is terminal and remaining shares are verified. Existing position management continues with new entries Off.
4. **Review boundary:** the candidate policy version changes so old permission cannot silently authorize its changed protection behavior. No production permission was changed.

The $25 target remains a purchase amount. **Proposed, unapplied** limits are $0.50 planned stop risk, $1 daily realized-plus-unrealized loss and $3 drawdown, one position. A $25 purchase with a 2% planned stop distance is still a $25 purchase; its planned price risk is roughly $0.50. These are for owner review and are not guaranteed loss bounds. Setups exceeding a reviewed limit should be skipped instead of silently resizing the purchase.

## Validation and remaining limits

- Default analyzer parity: **494 historical checkpoints, zero differences** from the deployed source.
- Full test/build counts and deterministic rerun hashes: [verification record](verification.json).
- Current broker capability and source documentation: [execution checklist](execution-validation.md).
- Paper order lifecycle: **NOT RUN**. Actual live buy/protect/sell lifecycle: **NOT DEMONSTRATED**. Replays assume order behavior and cannot prove real fractional stop acceptance or liquidity.
- The cache provided 19 full VIX sessions plus one partial session. Requiring the app's full 14-calendar-day VIX history leaves nine usable evaluation sessions. There are 497 regular-session VIX candles in the frozen dataset.
- IEX is one exchange. Split-adjusted history was fetched later and cannot reproduce every contemporaneous publication delay, spread or correction. No historical <=90-second VIX entry quotes are available. Fifteen-minute OHLC cannot establish five-minute video triggers.
- Entries occur at the next available bar opening after the completed signal plus a 60-second publication assumption. Stop wins if stop and target are touched in one candle; adverse gaps use the worse open. Closing at 15:55 cannot be reconstructed from 15-minute OHLC, so the replay exits at the earlier 15:45 opening. Reported drawdown samples bar ends and can miss larger intrabar losses.
- The Docker daemon is unavailable on this Mac. Local Python tests, interface tests and UI build can be verified, but a fresh container image build is **not claimed**. CI image construction remains a release gate.

## Promotion recommendation

**Do not deploy a different strategy or increase size.** Review the safety/diagnostic code separately from an unproven trading hypothesis. Resolve the primary-source ambiguities, collect timestamped five-minute actual-index/leader data and forward shadow observations, approve loss limits, then complete isolated paper commissioning. Only afterward consider an explicitly authorized owner-run small live trial. More fills or passing tests alone cannot promote a candidate.

The [reproduction and release guide](README.md) defines exact commands, proposed sample/acceptance gates, forward observations and rollback. Rollback requires a fresh flat account and no unresolved orders; retain durable trade history, never erase an unknown submission or restart the retired local trader over an active position.

## Evidence locations

- [Frozen protocol](experiment-plan.md) and [machine-readable plan](../../research/experiment-plan.json).
- [Reviewed results](../../runtime/research/run-reviewed-v1/results.json), [all experiments](../../runtime/research/run-reviewed-v1/experiments.jsonl), [decision traces](../../runtime/research/run-reviewed-v1/decisions.jsonl), [replay manifest](../../runtime/research/run-reviewed-v1/run-manifest.json).
- [Point-in-time source evidence](source-rule-map.md), [frame manifest](source-evidence.json), and [annotated frames](../../runtime/research/video-evidence/annotated-examples.html).
- [Dated operational baseline](../../runtime/research/baseline.json) and [unchanged analyzer check](../../runtime/research/baseline-parity.json).

**Measured outcome: improved software; no demonstrated historical edge; live execution still uncommissioned.**
