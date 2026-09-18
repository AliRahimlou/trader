# Leader context and entry admission audit

September 18, 2026. Candidate version **3.1.0**, based on `ad28f1029426f691a6bc9af5c0a5f4cb967b5b92` (3.0.1). This document describes local changes, not a verified production installation.

## Source conclusion

The owner selected **at least five agreeing leaders, with at most one opposing**, then requested verification against Socrates Investments. The [channel review](socrates-channel-review-20260918.md) identifies the public source matching the primary V3 recording and records eleven complete transcript reviews. None establishes an exact five/one threshold. This remains an explicitly labeled app interpretation, not the creator's formula or a demonstrated improvement in returns.

The recordings support location, broader context, leader reactions, and VIX confirmation qualitatively. They do not require every qualifying reaction to start on the same candle. The app already retained still-valid reactions for up to fifteen minutes; that duration remains an engineering choice. Five-minute scalp examples and hourly break/retest examples are distinct explanations and have not been merged into one mandatory checklist.

## Changes

| Area | Problem | Candidate behavior |
| --- | --- | --- |
| Participation | An opposing leader vetoed five otherwise agreeing leaders under the old four/zero interpretation. | Five/one implements the owner's selected interpretation. Current data for all seven companies is still required; internal contradictions remain invalid. |
| Comparable observations | Provider publication could leave different companies on different completed candles within the freshness allowance. | Compare the latest observations at one common completed candle time. The original reaction times may differ. |
| Evidence expiry | A saved ready setup could survive a leader reaction's expiry or the latest candle's publication allowance between analysis updates. | Carry separate reaction and observation deadlines into entry admission, prepared orders and UI. Use the earliest expiry. Rechecking or restarting cannot extend it. Existing attempted orders still require reconciliation and protection. |
| Opportunity selection | An expired leading candidate could conceal another current, independent candidate. | Skip structurally valid expired or already-consumed candidates. Malformed contracts still block; broker failures do not trigger a competing order. |
| Unsent entry reservations | An account-read failure after reserving an event could consume the opportunity even though no order was submitted. | Only durably never-attempted, expired prepared entries may be reserved again. The abandoned record is preserved transactionally. Uncertain, attempted or rejected orders remain consumed. |
| Competing workers | A stale prepared-order copy could overwrite a newer attempted state during expiry, or attempt submission after another worker retired/replaced it. | Atomically compare the exact active durable reservation before expiry or entry claim. A stale worker waits for reconciliation; it cannot retire or submit the changed reservation. |
| Delayed order claim | Waiting for a database claim could cross an already-checked input deadline. | Recheck the current time immediately after claiming and before submission. A claimed operation remains consumed if that deadline has passed; it is never automatically rearmed. |
| Data collection | Unused fifteen-minute/higher-timeframe leader history could fail collection or readiness. | Request native five-minute MAG7 history; request fifteen-minute QQQ history and derive only its required higher frames. Refresh the cache when its schema changes. |
| Required candle deadlines | A copied healthy snapshot could survive the deadline for the next required QQQ or leader candle. | Bound stock verification by the exchange-calendar publication deadline as well as receipt age. Saved frame health becomes stale at that boundary. |
| Overview | Higher-timeframe location existed, but the homepage had no separate direction description. | Display one-hour and four-hour confirmed QQQ swing structure, evidence age, mixed conditions and invalidation. This is descriptive context, not a forecast or additional trade veto. |
| Homepage | Neutral, missing and conflicting readings were all shown as waiting; expired setup cards could remain green. | Distinct labels, reaction ages, majority/dissent explanation, actual rule metadata and explicit expired confirmation states. |
| Audit trail | Saved diagnostics omitted some expiry and overview evidence. | Record shared observation time, leader deadline, active rule and descriptive context. Ignore polling timestamps when deduplicating unchanged evidence. |

The execution-policy version changes because five/one is a material strategy interpretation. This local candidate has not enabled live permission, submitted broker orders, or been installed on AllSpark.

## Paired historical check

The same 468 complete five-minute checkpoints across September 9, 10, 11, 14, 15 and 16 were replayed with network access disabled. The original engine/input hashes and checkpoint results remain preserved under `runtime/research/leader-consensus-validation-20260918/`. The final strategy comparison is separately preserved under `runtime/research/leader-consensus-validation-final-20260918/`. The user chose five/one before this comparison; no threshold was tuned afterward.

The final comparison freezes the final strategy bytes, including the leader-observation deadline. Its data-health snapshot predates the last calendar-deadline integration; the report records that hash explicitly. It is not represented as a replay of every final application file. Every variant summary and paired checkpoint difference matched the original comparison; final data-health integration is covered by the regression suite.

| Frozen variant | Four-hour method: qualified observations / distinct events | Previous-day method: qualified observations / distinct events |
| --- | ---: | ---: |
| Committed v2, four agree / zero oppose | 1 / 1 | 9 / 4 |
| Corrected v3 control, four agree / zero oppose | 1 / 1 | 9 / 4 |
| Candidate v3, five agree / at most one opposes | 0 / 0 | 6 / 3 |

Five/one admitted ten additional leader-check observations, all still blocked by VIX confirmation. Its higher minimum removed fourteen four-only leader-check passes. Across complete setups it added no qualified observations and removed four. These are observations/events, not orders or profits.

This sample was already inspected, so it is development data rather than an unseen holdout. Cached, split-adjusted IEX candles may contain later corrections and cover one exchange. Historical candle checks do not reproduce live publication timing, broker admission, executable quotes, fills, costs, slippage or returns.

## Verification and remaining limits

Final local verification passed:

- **1,095 Python tests** across application, deployment and research suites, with network denied by the test harness.
- **40 browser-model tests**, including immediate reaction/input expiry and unknown timing.
- Production frontend build and whitespace/diff checks.
- Synthetic desktop and 390-pixel mobile layout review; no browser warnings or errors observed. Final timing-label behavior is additionally covered by browser-model tests.

The regression cases cover unsent-entry retry, both stale-worker expiry paths racing a claimed order, claim/retirement/replacement races, restart reconciliation and stop protection, expiry during broker reads or database claiming, current alternative candidates, calendar publication deadlines, and stable evidence logging across receipt-only refreshes. These tests use simulated brokers and do not validate actual fills.

The synthetic preview uses a fake account and GET-only routes with no broker connection. It is not evidence of actual order execution. No production settings or live controls were changed during this audit. No full-channel viewing claim, creator-exact formula claim, or profitability claim is made.

At small dollar targets the QQQ implementation supports fractional long purchases; whole-share short requirements remain a separate execution constraint. A bearish leader majority alone is not a reason for a long purchase and cannot establish that a trade should have occurred.
