# Version 3.2.0: execution recovery and observable decisions

Implementation date: September 18, 2026. This release fixes the four reproduced audit defects, adds bounded data recovery and worker monitoring, and supplies an isolated paper-broker commissioning workflow. It does not promote a new entry or exit strategy. Actual paper commissioning and profitability remain unverified.

## Reproduced defects fixed

| Defect | Resulting behavior |
| --- | --- |
| Permission or purchase amount changes between planning and submission | Each plan binds to the exact saved permission, settings and a monotonically increasing generation. Preflight, reservation and submission claims reject changed authorization, including an Off/On cycle that restores the original values. |
| Locally aborted entry leaves an active trade stuck | A proven pre-submission abort releases the active slot, records a terminal consumed event and sends no order. An uncertain network outcome stays unresolved until broker reconciliation; it is not treated as a safe abort. |
| Entry-only lifecycle remains stuck after an external/manual flatten | Persistent, repeated terminal-order and flat-position evidence raises an incident, pauses new live entries and requires reconciliation. The app does not invent an exit price or realized return. |
| Replay rejects leader timeframes that production no longer requires | The current analyzer requires native five-minute leader history. Older analyzers retain their own fifteen-minute/four-hour contracts, with explicit versioned replay behavior. |

Partial-entry handling also records the first observation, confirmation deadline, filled quantity, incident and resolution. Persistent unconfirmed cancellation pauses new live entries and continues reconciliation without issuing a competing exit. Restart tests retain unknown outcomes and order ownership.

## Data collection and monitoring

- A successful stock read missing only its newest expected native candle can receive one bounded, stock-only publication retry. Historical gaps, absent inputs, closed sessions and failed initial reads do not qualify.
- VIX collection distinguishes transient provider/publication failures from invalid identity, timestamps or payloads. Eligible recovery uses a 30-second backoff and one extra attempt per slot. Recovery remains inside the existing total request allowance, with additional limits of four per day and fifty per month. Fresh-quote recovery cannot repeatedly renew a stale cached quote.
- Account, analysis and execution workers report independent progress. A web server that answers requests while a worker stalls is no longer reported as fully ready. Incidents and recovery are retained in the activity ledger; the replacement-health check now waits for workers to complete healthy cycles.
- Scheduling measures from the preceding cycle start, avoiding an extra full poll interval after every data fetch. Overruns do not launch an unbounded catch-up burst.

Worker readiness measures process progress. Data freshness, broker connectivity and strategy qualification remain separate observations. These monitors do not prove continuous availability or restart a stuck worker themselves.

## What the homepage adds

The account total remains prominent. The homepage now includes a session review with distinct setup events, the checks that stopped them, order attempts, observed fills, protection and completed lifecycles. Repeated polls are not counted as separate trades. Existing retained history can be incomplete and is labeled accordingly.

Worker failures, archive problems, VIX retry eligibility and unresolved partial fills are visible. The purchase area explains that a small fractional QQQ target supports long purchases, while a short requires a supported whole-share quantity. Each current method states its earliest possible completed-hour event time; those times are not promised trades.

## Private replay evidence

A separate owner-readable SQLite archive stores complete market frames, original receipt times, provider corrections, both methods' candidate evidence, optional purchase settings and an engine hash. It has no broker interface. Content-addressed compressed frames reduce repeated storage. The 512 MiB ceiling fails visibly rather than silently deleting history; operators must preserve/export the archive before increasing capacity.

Decision-summary and input-archive writes are independent: a summary-ledger failure does not silently skip a writable input archive. Archive replay requires the recorded engine and compares both methods, all candidates and supporting signal evidence. It does not replay broker decisions, account state or fills. Use `python -m research.replay_observation --help` for the private local replay command.

A fresh read-only capture of all eight Alpaca stock inputs on September 18 passed current-data checks and reproduced the captured analysis exactly. That capture intentionally contained no VIX input; it is stock/archive evidence only.

## Separate research and paper work

- [Precise current rules and separately sourced alternatives](research/current-method-specifications.md) distinguish creator statements, app formulas and unresolved interpretations. Five agreeing/one opposing remains an owner-selected interpretation, not a verified numerical creator rule. The documented coverage is sixteen complete public transcripts, not the whole channel or Discord archive.
- [Completed historical experiment](research/current-method-experiment-results-20260918.md) compares declared policies on six previously viewed sessions with separate chronological periods and explicit costs. Current rules found three distinct sweep events, but saved execution prices were too coarse for the deadlines. The result is inconclusive; zero simulated fills do not establish low risk or profitability.
- Faster native-five-minute entries and VIX exits are implemented only in disconnected research code. The separate native-input runner validates provenance, interval, warmup and coverage, and preserves the completed experiment. Authentic five-minute QQQ/VIX acquisition and its historical comparison remain unperformed. Existing free API documentation supports a prospective request, but the authoritative AllSpark quota ledger was unavailable for a safely recorded research reservation from this network.
- [Paper commissioning instructions](research/paper-commissioning.md) describe the fixed paper endpoint, separate credentials, ledger and process lock, default read-only preflight and explicit simulated-money lifecycle. Synthetic fixtures cannot authorize the production executor, even when outer labels are stripped. Actual broker responses still need to verify entry, protection, cancellation, exit and final flat state using separate paper access.

## Verification and release boundary

The combined offline suite passes **1,246 Python tests** and **45 browser-model tests**; the frontend production build also passes. Regression coverage includes authorization races, pre-submit aborts, unknown requests, partial fills, external exposure, archive failures/replay, worker stalls, stock catch-up, bounded VIX recovery, causal research evaluation and paper/live separation. No broker order or live-permission change was made during implementation.

The release preserves the owner's purchase target and saved Live setting under the existing deployment gate. Experimental rules are absent from the production strategy path. Passing these tests is not actual paper commissioning, live-fill verification, complete source review or a profitability claim.
