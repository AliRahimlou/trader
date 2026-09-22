# Owner-controlled execution — September 16, 2026

This document records the original execution release. Some details below are historical: the current leader rule is five agreeing with up to one opposing (a conflicting leader is neutral since 4.4.0), AllSpark hosts the worker, VIX has time-based retries within its allowance, the entry cutoff is 30 minutes before the close, and a rejected stock order pauses Socrates only instead of switching global Live off. See the [4.4.0 release note](production-v4.4.0.md) for the current rule interpretations. See the [current method specification](research/current-method-specifications.md), [3.2 monitoring changes](production-v3.2.md), and [3.2.2 recovery audit](production-v3.2.2.md) for the current implementation and validation limits.

## What changed

The previous rebuild deliberately exposed no broker order path. Its Live money button only opened an explanation. Version `video-execution-v3` adds a persistent, owner-operated control and a new Alpaca execution worker. It does not load the archived strategy or order engines.

The app starts with permission **Off** unless the owner has previously enabled this exact execution-policy version. Enabling requires reviewing the policy in the local interface. Permission is bound to the connected Alpaca account; changing credentials to another account cannot reuse it. Missing market data does not prevent saving permission: the status instead says what the app is waiting for. **On is permission to trade, not a claim that a trade is available or happening.**

## Executable interpretation

QQQ is the selected Alpaca Nasdaq-100 ETF instrument. It is not the futures chart in the videos. The existing primary analyzer supplies the two videos' location → event → leaders → actual VIX flow. The first recording adds no conditions. Numerical rules remain documented implementation choices, not verbatim video instructions.

- Fresh, completed hourly event; pre-existing four-hour levels or previous-session extrema.
- Four of seven technology companies reacting at their own zones, with none opposing; actual VIX reaction in the opposite direction.
- Stop beyond the event/zone and target at the next opposing level.
- One managed position at a time; existing broker exposure or foreign orders block new entries.
- No new entries in the last ten minutes of the broker's regular session. Begin closing five minutes before its actual close, including early closes.
- Fresh quotes, valid spread and price geometry, no more than 1% movement from the signal reference, account permissions, current buying power and asset eligibility are checked immediately before entry.

Long purchases use the saved dollar notional when QQQ is fractionable. Whole-share orders, including all shorts, must fit within 1% below the requested dollar amount. A small dollar target cannot open a fractional short and is skipped rather than silently resized. Shorts also require account permission, borrow eligibility and sufficient broker buying-power reserve. Exits close the actual remaining quantity; sale proceeds are not fixed to the original purchase amount.

## Lifecycle and recovery

Each event/direction has one durable trade identifier. SQLite permits only one active trade. Each order gets a unique `client_order_id`, and its single POST attempt is recorded before the request. A missing response is reconciled through broker lookup; an unknown order is never blindly resubmitted. A process lock prevents two production workers using the same runtime folder.

An unfinished or partially filled entry is canceled before its final filled quantity is managed. Broker stop orders are submitted after entry fills. The worker monitors the target and session close. Before sending an exit, it requests cancellation of its stop, waits for terminal broker confirmation, and rereads the position. A stop filling during cancellation therefore does not produce a second sale. Actual filled quantities are reconciled before any subsequent exit. Foreign orders, changed share counts and changed accounts prevent automatic interference.

Turning Off prevents new entry submissions and cancels unfinished entries. It continues managing positions opened under prior permission. A rejected protective order triggers an attempt to close the filled position and pauses new entries. A rejected exit requires owner attention in Alpaca. Unknown submissions remain pending reconciliation, including across restarts.

## Limits that remain

- Actual VIX now comes from InsightSentry Free. Completed 15-minute candles are cached; an actual-index quote no older than 90 seconds is required before an otherwise valid entry. A failed history or quote attempt waits until the next 15-minute period. Missing candles, stale data and the durable request cap block new entries. No paid fallback is enabled.
- The stock feed is currently IEX (partial market), clearly labeled in the app.
- This release has offline simulated-broker lifecycle tests, not verified paper-account or live fills. No real orders or cancellations were submitted during development.
- Stops are submitted after fills, so there is an unprotected interval. Network outages, partial fills and broker rejection can extend that interval. Broker stops do not guarantee execution price. Alpaca may convert buy stops for short positions into stop-limit orders.
- Fractional stops are DAY orders and expire at the session end. Targets and end-of-day closing require the local worker and connection. A sleeping or disconnected Mac can leave a position open and without protection after expiry. This is not a hosted service.
- Historical replay, costs and profitability have not been established. Mechanical interpretation is not proof of equivalence to a discretionary demonstration.

## Verification

The latest [completion audit](completion-audit.md) records the connected InsightSentry feed, policy handoff, data budget, and 467 passing backend tests. Earlier checks added malformed-quote protection, a quote-bounded submission deadline, reconciliation of manual resolutions, handling for replaced entry/exit orders, and durable fill economics. The initial-release validation below is retained as historical context.

75 Python tests passed with all real HTTP requests prohibited. They cover source rules, sizing, admission, control persistence, origin protection, account identity, single-position reservation, restart/timeout reconciliation, partial entries, partial stops, stop-cancel fill races, rejection recovery, target exits, session-close exits and external position changes. Three frontend model tests and the production build passed.

Browser testing used a separate fixture with an in-memory broker, temporary database and a network-denial guard. The confirmation checkbox enabled the button, On persisted through a refresh, missing VIX showed a waiting status, and Off persisted. The actual account's control was not activated by the assistant.

Provider references: [Alpaca fractional order types](https://docs.alpaca.markets/us/docs/fractional-trading), [order lifecycle and time-in-force](https://docs.alpaca.markets/us/docs/orders-at-alpaca). Supported API order types are not evidence that this account's complete lifecycle has been commissioned.

## Original local installation verification (before InsightSentry)

The managed local backend was restarted with permission Off after a fresh broker snapshot confirmed zero positions and zero open orders. The running API reports `video-execution-v3`, `execution_available=true` and `legacy_loaded=false`. Read-only provider checks confirmed an active live account and fractionable/tradable QQQ; actual VIX returned an entitlement denial.

During final browser inspection, live permission was enabled from another UI interaction. The assistant's subsequent verification click switched it Off; this was immediately disclosed to the owner with instructions to reactivate it themselves. The assistant did not enable the actual account or submit broker orders. The temporary offline UI server and its tab were closed after testing.
