# Version 4.6.0 · Socrates rules decided by evidence

The owner asked for expert decisions instead of choosing rule settings themselves.

**How the rules were chosen.** Every open question was replayed on a year of real setups (October 2025 to September 2026) through the unmodified analyzer. Each result was re-derived by an independent checker. A panel of three expert viewpoints then voted: a prop-firm risk manager, a quant researcher, and a Nasdaq-futures day trader using the Socrates method. Majority won, and the conservative option won ties.

**Source material.** Socrates' own rules come from every upload on his channel (655: 14 live streams, 29 videos, 612 shorts).

**Real money stays on.** The owner decided to keep live trading. The panel had recommended shadow mode, because no tested variant showed an edge after costs.

## What changes

| Rule | 4.5 | 4.6 | Why |
|---|---|---|---|
| Take profit | nearest opposing level at least 1R away | today's 09:30 open when it is in the trade direction and at least 0.20% from the live entry price; otherwise the nearest key level or pre-existing area at least 0.20% away (previous-day high/low, previous four-hour high/low, week open, Monday high/low, previous-week high/low, month open); no qualifying level → the entry is skipped | Socrates: "take profit at the next level", most often the daily open. Best of 154 tested exits: paired +0.043%/trade against the 1R target (t 2.3), positive in both halves. |
| Setup admission | needs a 1R level | unchanged: still needs a 1R level to qualify | the untested extra setups are not traded |
| Entry hours | 10:30-15:30 ET | 10:00-12:00 ET (the first hourly close is 10:30) | his stated window; halves exposure |
| Shorts (PSQ) | traded | recorded, not traded; `PIVOT_SOCRATES_SHORTS_LIVE=1` re-enables 4h-retest shorts; previous-day-sweep shorts never trade | every short group lost after PSQ costs; previous-day-sweep shorts lost in almost every month |
| Stop | beyond the event area and break candles | unchanged; the entry is skipped when the stop is more than 1.5% from the live entry price | cuts the worst replayed trade from -2.3% to -1.3% |

## Unchanged

- hourly-candle signals
- the 4-of-7 leader rule and the VIX gate
- the 4h level construction
- $15 per trade, two entries per session, one position at a time
- flat by 15:55

## Rejected by the panel

These ideas lost, or did not help, in replay:
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
