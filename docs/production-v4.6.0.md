# Version 4.6.0 · Socrates execution rules

The owner asked for expert decisions instead of choosing rule settings themselves.

**Evidence correction, October 9, 2026.** The original release note claimed a year of replay, an independent checker and a three-viewpoint panel. The inspected repository and retained research do not contain the claim-specific input manifest, complete variant grid, paired outcomes or checker report needed to reproduce those claims. They remain unverified; the numerical comparisons below describe what the earlier release note asserted, not validated performance.

**Source material.** The source audit inventoried 655 channel items but documents complete transcript review of 11, not every upload. Later reviews examined specific recordings. See [the source coverage record](research/socrates-channel-review-20260918.md). Metadata inventory must not be represented as content review.

**Live permission.** This release preserved the owner's saved permission after acceptance of its changed policy. That permission is not evidence of an after-cost trading edge. Profitability has not been established.

## What changes

| Rule | 4.5 | 4.6 | Why |
|---|---|---|---|
| Take profit | nearest opposing level at least 1R away | today's 09:30 open when it is in the trade direction and at least 0.20% from the live entry price; otherwise the nearest key level or pre-existing area at least 0.20% away (previous-day high/low, previous four-hour high/low, week open, Monday high/low, previous-week high/low, month open); no qualifying level → the entry is skipped | Socrates: "take profit at the next level", most often the daily open. Earlier release-note claim, not reproduced: best of 154 exits, paired +0.043%/trade versus 1R, t=2.3, positive in both inspected halves. Selection among many exits and inspected halves do not establish unseen performance. |
| Setup admission | needs a 1R level | unchanged: still needs a 1R level to qualify | the untested extra setups are not traded |
| Entry hours | 10:30-15:30 ET | 10:00-12:00 ET (the first hourly close is 10:30) | reviewed execution restriction; hourly candles make the effective signal window 10:30–12:00; the exposure-reduction claim is unverified |
| Shorts (PSQ) | traded | recorded, not traded; `PIVOT_SOCRATES_SHORTS_LIVE=1` re-enables 4h-retest shorts; previous-day-sweep shorts never trade | earlier release note asserted losses in tested short groups; supporting claim-specific results were not located |
| Stop | beyond the event area and break candles | unchanged; the entry is skipped when the stop is more than 1.5% from the live entry price | limits planned stop distance; earlier worst-loss comparison (-2.3% to -1.3%) is unverified and is not a guaranteed loss bound |

## Unchanged

- hourly-candle signals
- the 4-of-7 leader rule and the VIX gate
- the 4h level construction
- $15 per trade, two entries per session, one position at a time
- flat by 15:55

## Alternatives excluded from this release

The earlier note said the following alternatives lost or did not help in replay. Their claim-specific comparisons and panel record were not located; this list records excluded alternatives, not independently validated conclusions:
- the tight "first stop" (next 15-minute pivot)
- 15-minute entries
- rejection or liquidity-grab entries
- fewer levels
- NVDA/AAPL-weighted leaders
- a VIX co-movement veto
- news-day rules
- breathe, time-stop or partial exits
- conviction or fixed-risk sizing
- multi-day holds

## Owner step

The policy version changed to `nasdaq-qqq-execution-v8-socrates-4-6`. Open the app and accept the new Socrates rules from Live money; entries resume after that.

## Code

- `pivot/strategy.py` `key_levels()`: computed from completed regular-session 15-minute QQQ candles only. Each qualified candidate carries `exit_levels`.
- `pivot/execution.py`:
  - `socrates_target()`;
  - the `entry_window` and `stop_distance` gates;
  - the longs-only candidate filter, which skips shorts so that a ready long is never hidden behind them.
- `pivot/feature_flags.py` `socrates_rules()`.
- Tests: `pivot/tests/test_v460_rules.py` runs the production defaults. Older scenario tests keep the 4.5 rules through the test-only `PIVOT_SOCRATES_LEGACY_RULES` override in `conftest.py`.
