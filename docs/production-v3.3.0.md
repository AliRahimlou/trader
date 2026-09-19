# Version 3.3.0: separate strategy views and Bitcoin observations

The September 19 recording describes a different method from Socrates. This release adds **4H Range Reversal** as an independent Bitcoin observation family, alongside a top-left **Socrates / 4H Range Reversal / All strategies** view selector. Account information and actual Socrates execution controls stay visible in every view. The selector changes only a browser preference; it cannot enable, disable or redirect orders.

The new observer reads genuine native five-minute BTC/USD candles from Alpaca. It marks a provisional first four-hour daily range, records outside-close/inside-close reversals, and shows reference entry, stop and 2R target prices. Full closed-candle coverage, source identity, freshness and event identity are checked. Historical observations are labeled separately from a current event. The observer runs independently of stock/VIX polling and never enters the existing executor's state.

The recording leaves the daily candle anchor and discretionary stop decisions unresolved. New York midnight plus four elapsed hours and the first outside-close candle's extreme are explicit provisional conventions. This release does not route new orders or permit simultaneous positions. The existing live toggle still controls Socrates only. See the [detailed timestamped comparison and exact rules](research/range-reversal-video-20260919.md).

Private evidence storage deduplicates unchanged candles and retains provider corrections. It checks regular-file permissions, rejects symlinks/FIFOs, and enforces a 64 MiB SQLite page cap. Archive errors are visible. Optional observer initialization, startup and snapshot failures cannot take down the main account/strategy response.

## Validation

- **1,418 Python tests passed**, including 80 new analyzer, reader and integration checks.
- **72 interface tests passed**; the production frontend build passed.
- Tests cover exact candle boundaries, same-day resets, DST, wick-only and boundary-equality rejection, multiple excursions, immutable stop references, missing/native/stale inputs, error sanitization, archive deduplication/corrections/limits, worker startup failure and execution isolation.
- Actual app event-handler tests establish that changing strategy views sends no network mutation and preserves live permission and purchase settings. Stale data suppresses current setup references.
- An independent review reproduced observer exception propagation, FIFO blocking and pre-insertion-only storage limits; all three issues were fixed with regressions.
- A read-only Alpaca collection verified actual native Bitcoin bars and a complete first range. This establishes connectivity and observation behavior, not accepted orders, realized performance or the narrator's claimed win rate.

No Bullpen login, account permission, leverage, order, open position or signing state is changed by this release. Existing Socrates broker policy and the saved purchase target are unchanged. AllSpark uses the existing tested updater and its exposure/recovery checks.
