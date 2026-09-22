# Version 4.4.0 — minimum reward-to-risk, VIX-scaled gate, neutral leaders, per-engine allowances

The September 21, 2026 audit (60-day replays of both engines, the first full market session on 4.3.x, and a review of every admission gate) explained why the app has never sent an order: not a missing setup, but four rules whose numerical interpretation made a qualifying day nearly impossible, plus six execution details that blocked or would have mis-managed the entries those rules did admit. This release changes the numerical interpretation of the Socrates target, VIX and leader gates, gives each engine its own session allowance, replaces the global Live-off reaction to a stock rejection with a strategy-scoped pause, and repairs the crypto cost and opening-range gates. Every change ships behind a new policy version, so **both strategies show “review required” after rollout and no new entry is sent until the owner accepts the new policies in the app.** Saved purchase amounts ($15 each), market selections (BTC/USD) and the global Live setting are unchanged. No database migration is required.

The unchanged safety properties are listed at the end. None of the changes below touches the durable claim-before-POST protocol, duplicate prevention, `client_order_id` reconciliation, exit ordering, the deployment-readiness contract or the `ny-session-two-v1` session-entry protocol identifier that the installed updater checks.

## Socrates rule changes

### Minimum reward-to-risk, with the event level excluded from targets

The 4.3 target was the nearest opposing pre-existing 0.1% band beyond the reference price. Over 39 replayed sessions (34 with an event, 54 distinct events, 223 valid rows) that gave a **median 0.52 R**: 67% of plans targeted less than 1 R, 48% less than 0.5 R, and only 20% reached 2 R. At the $15 purchase size the median plan risked about 4.6 cents to make 2.4 cents. The nearest band could also be the level that had just been swept or broken, so the “target” sat inside the event itself.

The 4.4 interpretation:

- The event's own area (the four-hour band or previous-day level that produced the sweep or break) is **excluded** as a target. The target is the nearest *other* opposing pre-existing area beyond the reference price; establishment before the origin bar is still required.
- A plan whose reward-to-risk is below **<<MIN_RR>>** (target distance divided by stop distance, both from the planned entry reference) is skipped with an explicit reason that shows both distances and the ratio. Nothing is resized or moved to manufacture the ratio; a nearer target that fails the ratio is not replaced with a farther one.
- The stop formula (event/origin/confirming/current extreme ± $0.01) is unchanged.

The minimum ratio is an app choice. The videos describe stopping beyond the swept area and aiming for the next pivotal range without naming a number; the September 26 discipline clip supports a preplanned loss only qualitatively.

### VIX gate at a VIX-scaled swing tolerance, with persistence and invalidation

The 4.3 gate required the latest completed 15-minute VIX candle to react at a zone built from a 0.1% band around a prior swing extreme — about 0.015 VIX points at VIX ≈ 15, or roughly one and a half ticks — with no persistence: whichever direction the single latest candle happened to show was the answer. On 495 replayed bars across 20 sessions the gate read “true” for a long on 28.3% of bars and for a short on 21.4% at random, while the **median 15-minute VIX bar range was 0.656%**, six times the band. On September 21 a unanimous 7/0 leader-long setup at 11:41 ET was blocked only because VIX had not printed a 1.5-tick reaction at one of 56 such zones, and at 11:51 the zone reaction it did print was in the wrong direction.

The 4.4 interpretation:

- Swing areas are still built from completed 15-minute actual-VIX candles, excluding the two latest bars, using repeated swing extremes. The area tolerance is now **<<VIX_TOL>>** of the VIX level (scaled so that a fifteen-minute bar can plausibly touch and reject an area rather than straddle it by construction).
- A reaction must **persist**: the reaction candle closes in the reaction direction relative to the area and the next **<<VIX_PERSIST>>** completed candle(s) must not close back through the area. The reaction is **invalidated** by any later completed candle closing beyond the area on the wrong side, or by the reaction ageing past the same 180-minute event window that bounds the Nasdaq event.
- The direction rule is unchanged: for a QQQ long the VIX reaction must be short (rejecting an area from below), for a QQQ short the VIX reaction must be long.
- The fresh actual-index quote check before submission is unchanged and separate from candle qualification.

The persistence count and tolerance are app choices: the 18:11 recording shows VIX at a fifteen-minute area “starting to buy”, which is a qualitative description of a reaction that holds, not a band width.

### Leader vote mechanics: conflicting is neutral, pre-origin reactions count, follow-through means still beyond the area

Three details of the 4.3 vote made a five-of-seven quorum rare even on a directional day (September 21 leader counts included thirty observations at 4/1 and thirty at 2/1 while QQQ trended):

- A company with active reactions at two areas in opposite directions was **conflicting**, and one conflicting company vetoed the whole seven-company vote. It now counts as **neutral** — it neither agrees nor opposes — so a 5/1 quorum can still be met by the other six. This is the owner-selected reading of the 17:25 recording's “mixed evidence → no trade”, applied at the vote level rather than the company level: mixed *across* the leaders still means no trade; one leader being unreadable does not.
- A reaction that began **before the Nasdaq event's origin bar, in the same session,** was discarded. It now counts if it is still valid at evaluation time under the same persistence rules. The leaders are asked whether they are reacting when the Nasdaq location event is being confirmed, not whether they started reacting afterwards.
- Follow-through previously required every later close to be at least as far in the reaction direction as the originating close, so a one-cent pullback discarded a vote. Follow-through is now **still beyond the area**: later closes must stay on the reaction side of the area edge (above the area high for a long vote, below the area low for a short vote). A close back through the area still invalidates.

The five-agree/one-opposing quorum, the 15-minute reaction age, the same-completed-candle synchronization across all seven companies and the missing-candle rule are unchanged.

## Execution changes

### Per-strategy session allowance of two attempts

The 4.3.0 allowance of two new entry attempts per New York calendar session was shared by both engines, so a morning crypto attempt could consume a Socrates attempt and vice versa. Each strategy now has **its own** allowance of two attempts per New York session, claimed in the same SQLite transaction as the durable submission operation, with the same conservative accounting: rejected, uncertain, expired-after-claim and already-claimed attempts consume; completing a trade or restarting does not refund; claims within ten seconds of New York midnight reserve both adjacent dates. Stop and exit operations never consume an attempt. The updater's `ny-session-two-v1` session-entry protocol identifier is unchanged — the protocol is still “two per session, claimed durably before submission”; the scope moved from the account to the strategy. Existing rows are read per strategy; the dashboard reports each engine's allowance separately.

The two-per-session number comes from the creator's two-trade discipline clip; applying it per engine rather than per account is an app choice, because the two engines watch unrelated markets on unrelated clocks.

### Strategy-scoped pause instead of global Live off, with an optional alert

A rejected stock order previously switched the **global** Live control off, which also halted crypto entries, and did so silently. A rejected stock order now pauses **Socrates entries only** (`socrates_paused`, with the broker's rejection reason retained in the event journal); crypto continues under its own permission and its own rejection handling. The pause is cleared by the owner from the strategy card after reviewing the reason, exactly as the crypto incident path already works. Global Live remains the owner's control and is no longer changed by the app.

If `PIVOT_ALERT_WEBHOOK_URL` is set in the container environment, a pause, an unresolved incident, a protection failure and a lost-order resolution POST a short JSON alert (strategy, reason, time, no credentials, no order payloads) to that URL from the worker. Delivery is best-effort with a short timeout; a failed alert is logged and never blocks management. Unset, nothing is sent and no network call is made.

### Thirty-minute entry cutoff

Entries were allowed until ten minutes before the close, but positions are flattened five minutes before the close, so an entry in that window bought a position that would be sold at market within minutes and could not reach a 2 R target. No new Socrates entries are now submitted in the **final 30 minutes** of the broker's regular session, including early closes. The five-minute closing window is unchanged.

### Five-second clock-skew tolerance

The `session_open` and quote-age checks compared host time with Alpaca's clock and quote timestamps exactly, so a host clock one second behind the broker reported the session as not yet open and current quotes as from the future. Those comparisons now tolerate **5 seconds** of skew in either direction. Deadlines that bound submissions (the 15-second budget from quote receipt, the 90-second crypto confirmation window) are unchanged and still use the host clock consistently.

## Crypto changes

### Net-expectancy cost gate after fees, with the numbers in the reason

The 4.0 cost gate required a positive projected target after 0.25%-per-side taker fees plus an execution allowance. With BTC's median five-minute stop distance of 0.119%, a 2 R target of 0.238% minus a 0.55% round trip is negative or a few hundredths of a percent, and the gate admitted trades whose net reward was effectively zero. Over 51 complete replay days the ten gated BTC trades went 6 wins / 4 losses for **−0.19% average net and −$0.29 total at $15**; the ungated population (47 wins / 94 losses) was worse.

The gate now requires the **net** reward after both fees and the execution allowance to be at least **<<CRYPTO_K>>** times the net risk (stop distance plus the same costs). A skipped setup states the fees, the allowance, the gross and net reward, the net risk and the ratio in its visible reason, so the owner can see why a visually valid reversal was not bought. The stop (breakout candle extreme) and the 2 R gross target are unchanged; the gate only decides whether the plan is worth sending.

### Gap-tolerant opening range with a minimum bar count

A single missing five-minute candle in the first four hours voided the whole New York day for that market (ETH/USD had 2 complete days out of 59; LINK, SOL and XRP had 13). The opening range now tolerates gaps: it is valid when at least **<<OPENING_MIN>>** of the 48 five-minute bars between 00:00 and 04:00 ET are present, the first and last bars are present, and no gap exceeds the documented maximum. The range high/low come from the present bars only. Reversal confirmation still requires a native five-minute close outside followed by a later native close inside; missing bars during confirmation still invalidate that excursion, never the range. Provenance records the bar count and each gap.

### Rule and policy versions

The crypto rule version advances from `range-reversal-v1` to **<<RANGE_RULE_VERSION>>** and the crypto policy from `range-spot-execution-v1` to **<<CRYPTO_POLICY_VERSION>>**; the Socrates policy advances from `nasdaq-qqq-execution-v5-five-leader-majority` to **<<QQQ_POLICY_VERSION>>**. Event identities include the rule version, so no event confirmed under the old rules can be replayed under the new ones. Saved permissions are bound to the policy version, which is why both strategies require re-review.

## VIX data changes

### Time-based retries within the free allowance

The InsightSentry slot policy allowed one history attempt per 15-minute period plus one bounded recovery. When a candle was published late, the attempt at the period boundary fetched nothing, the recovery fetched nothing a moment later, and entries then waited up to 14 minutes for the next period with a complete, current candle. Retries are now **time-based**: after a period boundary the worker may retry at fixed offsets until the candle arrives, within the same durable monthly allowance (the September budget used 139 of 900 requests including the 50-request reserve). The durable request limit, the reserve, the quote-before-entry rule and the one-quote-per-period rule are unchanged. The number of retries per period is bounded so a provider outage cannot drain the allowance.

## Deferred, and why

- **Inverse-ETF short proxy (e.g. SQQQ/PSQ for a Socrates short).** The account cannot short and a $15 target cannot form a whole QQQ share, so every short setup is skipped. Buying an inverse ETF would let those setups trade, but it is a different instrument with its own daily-reset decay, its own quote and its own areas; the video rule (“Nasdaq short”) says nothing about it. Deferred until the owner decides whether a proxy is an acceptable instrument translation. The skip reason remains visible.
- **Afternoon four-hour bucket (a second range for the 4H Range Reversal, from 09:30–13:30 ET or 12:00–16:00 ET).** The video demonstrates one range per day anchored at the session open; the app's midnight anchor is already a disclosed interpretation. Adding a second range doubles signals from a rule the source does not state, on markets where the replay is already net negative after fees. Deferred pending replay evidence with the new cost gate.
- **Short-capable crypto route (Bullpen/Hyperliquid).** Unchanged from 4.0.0: a different venue with separate authentication and position management; not silently substituted.

## Replay evidence

The numbers below come from the audit's 60-day replay of committed native bars (`research/`, network denied) and from the September 21 live session record. They are engineering evidence about rule frequency and cost, not a performance claim; no live fills exist.

| Measure | 4.3 interpretation | 4.4 interpretation |
| --- | --- | --- |
| QQQ plans, median reward-to-risk (223 rows) | 0.52 R; 67% below 1 R; 20% at or above 2 R | <<REPLAY_TABLE>> |
| QQQ plans targeting the event's own level | admitted | excluded |
| VIX gate true at random, long / short (495 bars, 20 sessions) | 28.3% / 21.4% at 0.1% band, 56 zones | <<REPLAY_TABLE>> at <<VIX_TOL>> with <<VIX_PERSIST>> persistence |
| Leader quorum (5/1) reached, September 21 | 11:20–11:24, 11:41–11:44, 11:51–11:54, 12:36–12:39 ET, all blocked by VIX | <<REPLAY_TABLE>> |
| BTC/USD complete range days (59 days) | 51 (strict, any gap voids the day) | <<REPLAY_TABLE>> at <<OPENING_MIN>> bars |
| BTC/USD gated long trades (51 days) | 10: 6 W / 4 L, −0.19% avg net, −$0.29 at $15 | <<REPLAY_TABLE>> at k = <<CRYPTO_K>> |
| ETH / LINK / SOL / XRP gated, total at $15 | −$0.15 / −$1.12 / −$0.85 / −$2.66 (13 or fewer complete days) | <<REPLAY_TABLE>> |

Replay sensitivity sweeps for `<<MIN_RR>>`, `<<VIX_TOL>>`, `<<VIX_PERSIST>>`, `<<CRYPTO_K>>` and `<<OPENING_MIN>>` are recorded in the release verification artifact; the chosen values are the ones recorded in `pivot/policy.py`, `pivot/strategy.py`, `pivot/range_reversal.py` and `pivot/crypto_execution.py` at the tagged revision.

## Owner action required

1. After the updater installs 4.4.0, both strategy cards show **review required**. Global Live stays at its saved value but neither engine can enter.
2. Open each strategy's policy, read the new rule statements (minimum reward-to-risk, VIX tolerance and persistence, neutral conflicting leaders, per-strategy allowance, strategy-scoped pause, 30-minute cutoff, crypto net-expectancy gate, gap-tolerant range), and accept them. Acceptance is bound to the connected live account and to the new policy version.
3. The saved purchase amounts ($15 Socrates, $15 Range Reversal) and market selection (BTC/USD) are carried forward unchanged; check them on the cards before accepting.
4. Optionally set `PIVOT_ALERT_WEBHOOK_URL` in the container environment to receive pause and incident alerts. Nothing is sent when it is unset.
5. A strategy that pauses after a rejection stays paused until cleared from its card; the other strategy and all exits continue.

## Verification boundary

- **Offline tests only.** The Python suite (`pivot/tests`, `deploy/tests`, `research/tests`) and the interface tests run with all network access denied against fake brokers and invented candle fixtures. New fixtures cover the excluded event level and ratio skip, the VIX tolerance/persistence/invalidation sequences, neutral conflicting leaders, pre-origin same-session reactions, area-edge follow-through, per-strategy allowance claims across both engines and a restart, the strategy-scoped pause with and without the webhook, the 30-minute cutoff, ±5-second skew, the crypto net-expectancy reason text, the gap-tolerant range at and below the minimum, and time-based VIX retries against the allowance. Existing fixtures that pinned the old behaviour (nearest-band target, 0.1% VIX zone, conflicting veto, shared allowance, global Live-off on rejection, ten-minute cutoff, strict range coverage, attempt-based VIX slots) were updated; the integrator's report lists each file.
- **Replays on 60 days of committed bars**, run by the research runner with network denied. They measure how often the rules fire and what the plans cost; they are not fills.
- **No live fills.** No real-money order, cancellation or paper-account fill was sent to produce this release. Zero orders have ever been sent by this app; the first live entry after the owner accepts the new policies will be the first end-to-end test of the broker path with real money, at the $15 purchase size.

## Unchanged safety properties

Durable claim before every POST; one `client_order_id` per attempt and no resubmission of an unknown order; unknown outcomes reconciled by identifier with the 4.3.2 60-second lost-request resolution; exits never compete (stop cancellation confirmed before a sale); the deployment-readiness contract and the `ny-session-two-v1` session-entry protocol identifier the updater checks; the composite deployment permission fingerprint (a changed policy version is exactly what makes both cards require review); shared cash reservation across engines; one Socrates position at a time and one crypto position per selected market; spot long-only crypto with no simulated short; the 15-second submission budget from quote receipt and the 90-second crypto confirmation window.
