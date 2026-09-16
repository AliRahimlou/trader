# Completion audit · September 16, 2026

## Current result

**The local app now has actual VIX data through InsightSentry Free.** Alpaca continues to supply the account, market clock, QQQ quotes and all eight equity histories. The previous Massive entitlement denial is no longer the active data path. No subscription or upgrade was purchased.

This completes the VIX software integration and its automated checks. It does not establish live broker fills, source-equivalent discretionary decisions or profitability. The updated execution policy is `nasdaq-qqq-execution-v2-insightsentry`; the owner must review it in the top-right Live money control before new entries are allowed. The old saved permission record is retained and cannot authorize the changed policy.

## Installed data behavior

- The source must identify `CBOE:VIX`, instrument type `INDEX`, with explicit zero delay. Futures, CFDs, volatility ETFs and delayed substitutes are rejected.
- Completed 15-minute candles are collected once per period, after a 30-second publication allowance, with one after-hours catch-up for a missing final candle. Failed history attempts wait for the next period.
- Every candle is checked for finite prices, valid OHLC bounds, aligned ordered unique timestamps and provider watermark. A cached forming candle never becomes completed through elapsed wall-clock time.
- The most recent 14 calendar days are checked against Alpaca's exchange calendar. Missing sessions or candles block readiness. Regular-session and early-close bounds are enforced explicitly.
- Original collection and source timestamps persist. A candle is usable only until the next expected candle plus the 90-second publication allowance, subject to metadata expiry. Closed-session history is displayed as saved history, never as entry readiness.
- Immediately before an otherwise actionable entry, the broker adapter requires an actual VIX quote with a source timestamp no more than 90 seconds old. Failed or stale quote attempts wait for the next 15-minute period. QQQ's independent 15-second quote deadline still applies, including after the VIX request.
- Instrument metadata is cached for 24 hours. Failed metadata requests may retry next hour, within the shared allowance.

## Zero-cost request controls

[InsightSentry Free](https://insightsentry.com/payment) advertises 1,000 REST requests per month and five per minute. The app stops at a conservative local ledger total of 900, including 50 reserved for earlier/manual tests: at most 850 app requests per calendar month. This leaves another 100 requests outside the app's limit. External account use is not observable by this local ledger; provider denials still block entries.

The allowance, in-flight reservations, successes and failures persist in `runtime/pivot-v2/vix-insight.sqlite3`. Processes share the same limits. A crash or uncertain request consumes its reservation; it cannot trigger repeated requests. The app never upgrades a plan or selects a paid fallback. Metadata plus roughly 26 candle closes per trading day leave room for occasional entry quote checks, not continuous quote streaming. Frequent signals or repeated outages may exhaust the allowance and pause entries.

The API key remains in a private, git-ignored `.env`; it is never included in snapshots, source evidence or request-ledger records. Snapshot diagnostics expose only whitelisted provider, timestamp and allowance fields.

## Verification completed

- **467 backend tests and 9 interface tests passed**, with real HTTP disabled in the test suite; the frontend production build passed.
- New tests cover persistence and concurrency of request limits, failed-request accounting, hourly metadata recovery, missing sessions, source identity, delayed/future data, cache expiry, forming-candle exclusion, the stock-calendar dependency and policy migration.
- Simulated execution verifies current VIX proof before entry, expired quotes, failures, no quote use for blocked or previously handled setups, off during an in-flight check, and the existing restart/partial-fill/cancel-race/stop/exit paths.
- A production-path read at **20:15:44 UTC / 4:15:44 p.m. Eastern** returned **260 completed regular-session VIX candles**, zero missing expected candles, and the final candle ending at **4:00 p.m. Eastern**. The provider declared zero delay.
- Before restart, a fresh read-only snapshot confirmed no positions, no open orders, no active trade and a closed market. The execution database was backed up privately. The managed backend restarted successfully.
- The running homepage shows the Alpaca balance, **8/8 current equity histories**, actual InsightSentry VIX saved history, a **$25 purchase target**, and the updated-policy review message with Live money effectively Off. No live control, order submission or cancellation was performed by the assistant.

Earlier fixes remain: malformed quote handling after fills, quote-bounded entry deadlines, reconciliation of manual resolutions and replaced orders, and durable cumulative fill economics. Confirmed-fill results remain gross until fees are reconciled.

## Remaining validation and operating limits

| Item | What remains |
| --- | --- |
| Owner activation | Review the changed policy and enable Live money in the local interface if desired. |
| Next open session | Observe scheduled candle refreshes and on-demand quote timing during the next market session; the final integration was installed after the close. |
| Broker commissioning | Simulated tests do not establish real fractional stop acceptance, fills, slippage or rejection behavior for this account. No real-money test order was placed. |
| Strategy validation | Numerical zone rules, leader consensus, sizing and exits are documented interpretations of the two primary clips. Annotated historical examples and out-of-sample performance still need validation. |
| Equity coverage | The free Alpaca IEX feed covers one exchange; it is not consolidated US market data. |
| Small-target shorts | Shorts require whole shares. A $25 target cannot short one QQQ share at current observed prices; the app skips such entries. Exits close actual shares, so proceeds need not equal the original purchase target. |
| Runtime | This deployment runs on the Mac. It must stay awake and connected for targets and end-of-day exits. A hosted service is not installed by this change. |
| Results | Reliable execution and positive returns are not guaranteed. No profitability claim follows from passing software tests. |

The video rule map remains in [the strategy audit](video-strategy-audit.md), and detailed order behavior is in [the execution guide](live-execution.md).
