# Version 4.4.0 — minimum reward-to-risk, VIX-scaled gate, neutral leaders, per-engine allowances

The September 21, 2026 audit (60-day replays of both engines, the first full market session on 4.3.x, and a review of every admission gate) explained why the app has never sent an order: not a missing setup, but four rules whose numerical interpretation made a qualifying day nearly impossible, plus six execution details that blocked or would have mis-managed the entries those rules did admit. This release changes the numerical interpretation of the Socrates target, VIX and leader gates, gives each engine its own session allowance, replaces the global Live-off reaction to a stock rejection with a strategy-scoped pause, and repairs the crypto cost and opening-range gates. Every change ships behind a new policy version, so **both strategy cards show “Review updated rules” after rollout and no new entry is sent until the owner accepts the new policies in the app** (see “Owner action required” for the exact screens and the two acceptance steps). Saved purchase amounts ($15 each), market selections (BTC/USD) and the saved global Live permission are unchanged. The only schema change is an additive column migration of the VIX request ledger (`insight_requests`), applied automatically on first open; no data is rewritten and no manual step is needed.

The unchanged safety properties are listed at the end. None of the changes below touches the durable claim-before-POST protocol, duplicate prevention, `client_order_id` reconciliation, exit ordering, the deployment-readiness contract or the `ny-session-two-v1` session-entry protocol identifier that the installed updater checks.

## Socrates rule changes

### Minimum reward-to-risk, with the event level excluded from targets

The 4.3 target was the nearest opposing pre-existing 0.1% band beyond the reference price. Over 39 replayed sessions (34 with an event, 54 distinct events, 223 valid rows) that gave a **median 0.52 R**: 67% of plans targeted less than 1 R, 48% less than 0.5 R, and only 20% reached 2 R. At the $15 purchase size the median plan risked about 4.6 cents to make 2.4 cents. The nearest band could also be the level that had just been swept or broken, so the “target” sat inside the event itself.

The 4.4 interpretation:

- The event's own area (the four-hour band or previous-day level that produced the sweep or break) is **excluded** as a target. The target is the nearest *other* opposing pre-existing area beyond the reference price; establishment before the origin bar is still required.
- The target must be at least **1.0 R** away (target distance at least the stop distance, both from the planned entry reference). A nearer opposing level below that multiple is skipped and the next one is considered; if no pre-existing opposing level qualifies, the plan is skipped with an explicit reason that names the stop distance and the multiple. Nothing is resized to manufacture the ratio.
- The stop formula (event/origin/confirming/current extreme ± $0.01) is unchanged, but the stop distance must now be at least **0.1% of the entry price**; a plan with a smaller stop is skipped as noise (the September 21 short had a three-cent stop).

The minimum ratio and the minimum stop distance are app choices; the replay (below) showed that higher multiples such as 1.5 R or 2 R lose targets and wins faster than they add reward inside a single session. The videos describe stopping beyond the swept area and aiming for the next pivotal range without naming a number; the September 26 discipline clip supports a preplanned loss only qualitatively.

### VIX gate at a VIX-scaled swing tolerance, with persistence and invalidation

The 4.3 gate required the latest completed 15-minute VIX candle to react at a zone built from a 0.1% band around a prior swing extreme — about 0.015 VIX points at VIX ≈ 15, or roughly one and a half ticks — with no persistence: whichever direction the single latest candle happened to show was the answer. On 495 replayed bars across 20 sessions the gate read “true” for a long on 28.3% of bars and for a short on 21.4% at random, while the **median 15-minute VIX bar range was 0.656%**, six times the band. On September 21 a unanimous 7/0 leader-long setup at 11:41 ET was blocked only because VIX had not printed a 1.5-tick reaction at one of 56 such zones, and at 11:51 the zone reaction it did print was in the wrong direction.

The 4.4 interpretation:

- Swing areas are still built from completed 15-minute actual-VIX candles, excluding the two latest bars, using repeated swing extremes. The area tolerance is now **1% of the VIX level** (a 2% band, about 0.30 index points at VIX 15, roughly three typical fifteen-minute ranges; after adjacent pivots merge, the median area is 3.7% wide).
- A reaction may sit on **either of the last two** completed candles of the current session (contiguous fifteen-minute candles), so a VIX reaction and a leader quorum no longer have to land on the same bar. It is **invalidated** if any later completed candle closes back through the area against the reaction, and it only counts while the latest close still sits beyond the area boundary in the reaction direction.
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

A rejected stock order previously switched the **global** Live control off, which also halted crypto entries, and did so silently. A rejected stock order now pauses **Socrates entries only**: a `strategy_paused` journal entry (family `socrates`) whose reason names the cause as the HTTP status and Alpaca's numeric error code (for example `HTTP 403, Alpaca error code 40310000`); the same cause is stored on the trade record, in the `order_rejected` journal entry and in the webhook alert. The broker's response text is never stored, because it can echo request or account details. A 4xx rejection creates no order at Alpaca, so the app's journal is where the cause is read; the code is looked up in Alpaca's error documentation, and the rejected attempt still counts against the session allowance. Crypto continues under its own permission and its own rejection handling (its incident carries the same status and code). The pause is cleared by the owner from the strategy card after reviewing the cause, exactly as the crypto incident path already works. Global Live remains the owner's control and is no longer changed by the app.

If `PIVOT_ALERT_WEBHOOK_URL` is set in the container environment, the worker POSTs a short JSON alert (`{kind, body, at}`: strategy, reason, time, no credentials, no order payloads) to that URL for the journal kinds `strategy_paused`, `order_rejected`, `order_not_found`, `crypto_order_not_found`, `incident_opened`, `execution_needs_attention`, `partial_entry_needs_attention`, `protection_failed` and `exit_needs_attention`. The last two are alerted in their own right because a strategy pause is a no-op once Socrates is already Off (an owner toggle or an earlier pause): a live position whose stop never became working, or whose exit or stop cancellation stayed unconfirmed, still raises exactly one alert while it is closed at market or reconciled. Delivery is best-effort from a daemon thread with a five-second timeout; a failed alert is dropped silently (nothing is logged) and never blocks management. Unset, nothing is sent and no network call is made.

### Thirty-minute entry cutoff

Entries were allowed until ten minutes before the close, but positions are flattened five minutes before the close, so an entry in that window bought a position that would be sold at market within minutes and could not reach a 2 R target. No new Socrates entries are now submitted in the **final 30 minutes** of the broker's regular session, including early closes. The five-minute closing window is unchanged.

### Five-second clock-skew tolerance

The `session_open` and quote-age checks compared host time with Alpaca's clock and quote timestamps exactly, so a host clock one second behind the broker reported the session as not yet open and current quotes as from the future. Those comparisons now tolerate **5 seconds** of skew in either direction. Deadlines that bound submissions (the 15-second budget from quote receipt, the 90-second crypto confirmation window) are unchanged and still use the host clock consistently.

## Crypto changes

### Net-expectancy cost gate after fees, with the numbers in the reason

The 4.0 cost gate required a positive projected target after 0.25%-per-side taker fees plus an execution allowance. With BTC's median five-minute stop distance of 0.119%, a 2 R target of 0.238% minus a 0.55% round trip is negative or a few hundredths of a percent, and the gate admitted trades whose net reward was effectively zero. Over 51 complete replay days the ten gated BTC trades went 6 wins / 4 losses for **−0.19% average net and −$0.29 total at $15**; the ungated population (47 wins / 94 losses) was worse.

The gate now requires the **net** reward after both fees and the execution allowance to be at least **1.0 times** the net risk (stop distance plus the same costs). A skipped setup states the fees, the allowance, the gross and net reward, the net risk and the ratio in its visible reason, so the owner can see why a visually valid reversal was not bought. The stop (breakout candle extreme) and the 2 R gross target are unchanged; the gate only decides whether the plan is worth sending.

### Gap-tolerant opening range with a minimum bar count

A single missing five-minute candle in the first four hours voided the whole New York day for that market (ETH/USD had 2 complete days out of 59; LINK, SOL and XRP had 13). The opening range now tolerates gaps: it is valid when at least **44 of the 48** five-minute bars between 00:00 and 04:00 ET are present (at most twenty minutes missing). The range high/low come from the present bars only. After the range, a missing slot has no close, so it cannot start or confirm an excursion; a slot missing **inside** an excursion (between the outside close and the close back inside) retires that excursion, because the candle the provider may still publish for it could have changed the outcome. The latest expected slot must still be present for a confirmation to be current. Coverage diagnostics still record every missing slot.

Known limit of this interpretation: the analysis is of the bars available at the time of the read. If Alpaca were to publish an intermediate five-minute bar late (absent on one poll, present on the next), the complete sequence could invalidate an excursion the incomplete sequence confirmed, or key a second event on the backfilled slot; a $15 entry already sent on the earlier read keeps its native stop and is managed on the stop and target frozen at entry. The replay data show Alpaca's crypto gaps as permanent no-trade slots rather than late publications (BTC/USD had 261 of 261 slots on September 21), so this is documented rather than gated; a contiguity requirement between the breakout and the confirmation is a candidate rule change for a later policy version.

### Rule and policy versions

The crypto rule version advances from `range-reversal-v1` to **`range-reversal-v2`** and the crypto policy from `range-spot-execution-v1` to **`range-spot-execution-v2`**; the Socrates policy advances from `nasdaq-qqq-execution-v5-five-leader-majority` to **`nasdaq-qqq-execution-v6-reward-risk-vix-persistence`** (analysis `nasdaq-video-interpretation-v4`). Event identities include the rule version, so no event confirmed under the old rules can be replayed under the new ones. Saved permissions are bound to the policy version, which is why both strategies require re-review.

## VIX data changes

### Time-based retries within the free allowance

The InsightSentry slot policy allowed one history attempt per 15-minute period plus one bounded recovery. When a candle was published late, the attempt at the period boundary fetched nothing, the recovery fetched nothing a moment later, and entries then waited up to 14 minutes for the next period with a complete, current candle. Retries are now **time-based**: after a period boundary the worker may retry at fixed offsets until the candle arrives, within the same durable monthly allowance (the September budget used 139 of 900 requests including the 50-request reserve). The durable request limit, the reserve and the quote-before-entry rule are unchanged; up to four successful quote refreshes per period are allowed so a ready setup is not locked out until the next quarter hour. Retries wait 30 seconds, are capped per period, and stop when the projected monthly need would exceed the allowance, so a provider outage cannot drain it.

## Deferred, and why

- **Inverse-ETF short proxy (e.g. SQQQ/PSQ for a Socrates short).** The account cannot short and a $15 target cannot form a whole QQQ share, so every short setup is skipped. Buying an inverse ETF would let those setups trade, but it is a different instrument with its own daily-reset decay, its own quote and its own areas; the video rule (“Nasdaq short”) says nothing about it. Deferred until the owner decides whether a proxy is an acceptable instrument translation. The skip reason remains visible.
- **Afternoon four-hour bucket for QQQ levels.** The app's four-hour frame is one 09:30–13:30 bar per session, so afternoon extremes never become four-hour areas. Including the 13:30–16:00 partial bucket touches the data pipeline, data-health and market-context contracts; deferred to a separate release so it cannot destabilise this one.
- **Short-capable crypto route (Bullpen/Hyperliquid).** Unchanged from 4.0.0: a different venue with separate authentication and position management; not silently substituted.

## Replay evidence

The numbers below come from the audit's 60-day replay of committed native bars (`research/`, network denied) and from the September 21 live session record. They are engineering evidence about rule frequency and cost, not a performance claim; no live fills exist.

| Measure | 4.3 interpretation | 4.4 interpretation |
| --- | --- | --- |
| QQQ plans, median reward-to-risk | 0.52 R; 67% below 1 R; 20% at or above 2 R (223 rows) | 1.87 R; 97 of 108 event-direction rows keep a target (43 of 54 longs); blind walk-forward +0.31 R per trade both directions, +0.31 R for longs (about 1.3 cents at $15) versus −0.02 R for longs under 4.3 |
| QQQ plans targeting the event's own level | admitted | excluded |
| VIX gate true at random, long / short (497 bars, 20 sessions) | 28.3% / 21.5% at 0.1% band, 56 zones | 17.6% / 12.6% at the 1% tolerance with two-candle persistence, 13 zones |
| Leader quorum (5/1) reached, September 21 | 11:20–11:24, 11:41–11:44, 11:51–11:54, 12:36–12:39 ET, all blocked by VIX | Still blocked: VIX rose from 14.60 to 15.09 through those windows, so no opposite reaction existed under any tested setting; separately, QQQ was at new highs with no pre-existing level above, so no target would have qualified either |
| BTC/USD range days (59 days) | 51 (strict, any gap voids the day) | 59 at the 44-bar minimum; ETH / LINK / SOL / XRP 32 / 48 / 40 / 39 (from 11 / 30 / 21 / 23) |
| BTC/USD gated long trades | old gate: 18 of 167 long signals, 8 W / 10 L, −0.39% avg net, −$1.06 at $15 | net-ratio 1.0: 0 admitted (the implied minimum stop distance is 1.16% of price; BTC five-minute breakout stops have a median of 0.10%) |
| ETH / LINK / SOL / XRP gated, total at $15 | −$1.12 / −$2.65 / −$2.48 / −$4.59 under the old gate (44-bar ranges) | 0 / 0 / 1 (+$0.32) / 5 (−$0.96) admitted at net-ratio 1.0; every ungated long population is negative after fees (−0.53% to −0.66% average) |

The sweeps behind these choices (minimum reward-to-risk 1.0 / 1.25 / 1.5 / 2.0 / 2.5; VIX tolerance 0.1% to 3% with one to three candles of persistence; crypto net ratio 0.75 / 1.0 / 1.5 and opening minimum 40 / 44 / 48) are recorded in the release verification artifact. Socrates outcomes were measured without the leader and VIX gates (no sixty-day leader history offline), on bar prices rather than fills; they establish geometry and rule frequency, not strategy performance. The crypto result means the BTC engine will idle at these stop distances; that is the intended outcome of a gate that refuses fee-negative trades.

## Owner action required

1. After the updater installs 4.4.0, the header switch reads **Live money Off** and the status line **Review updated live rules**, although the saved global permission is still On: a saved permission is bound to the policy version it was accepted under, and the new version has not been accepted yet. Both strategy cards read **Review updated rules**. Neither engine can enter; existing positions and exits are unaffected. This is the expected screen, not a failed rollout or a switch the app turned off.
2. **Socrates** is accepted through the global Live switch: click **Live money Off**, read the listed Socrates rule statements (minimum reward-to-risk, VIX tolerance and persistence, neutral conflicting leaders, per-strategy allowance, strategy-scoped pause, 30-minute cutoff), tick the acceptance and confirm. This writes the Socrates permission for the new policy version only. The dialog also lists the crypto rule lines when 4H Range Reversal is enabled, but it does **not** accept the crypto policy.
3. **4H Range Reversal** is accepted separately on its own card: click **Review updated rules**, read the crypto statements (net-expectancy cost gate, gap-tolerant range, `range-reversal-v2`), tick and confirm. Until this second click the crypto card keeps reading “Review updated rules” and no crypto entry is sent. Acceptance of each policy is bound to the connected live account and to that policy version.
4. The saved purchase amounts ($15 Socrates, $15 Range Reversal) and market selection (BTC/USD) are carried forward unchanged; check them on the cards before accepting.
5. Optionally set `PIVOT_ALERT_WEBHOOK_URL` in the container environment to receive the alerts listed above. Nothing is sent when it is unset.
6. A strategy that pauses after a rejection stays paused until cleared from its card; the journal entry (`strategy_paused` for Socrates, a crypto incident for Range Reversal) carries the HTTP status and Alpaca error code to review. The other strategy and all exits continue.

## Verification boundary

- **Offline tests only.** The Python suite (`pivot/tests`, `deploy/tests`, `research/tests`) and the interface tests run with all network access denied against fake brokers and invented candle fixtures. New fixtures cover the excluded event level and ratio skip, the VIX tolerance/persistence/invalidation sequences, neutral conflicting leaders, pre-origin same-session reactions, area-edge follow-through, per-strategy allowance claims across both engines and a restart, the strategy-scoped pause with and without the webhook, the rejection status and code in the trade record, journal, pause reason and alert (with the response text excluded), protection and exit alerts while Socrates is already Off, the 30-minute cutoff, ±5-second skew, the crypto net-expectancy reason text, the gap-tolerant range at and below the minimum, and time-based VIX retries against the allowance. Existing fixtures that pinned the old behaviour (nearest-band target, 0.1% VIX zone, conflicting veto, shared allowance, global Live-off on rejection, ten-minute cutoff, strict range coverage, attempt-based VIX slots) were updated; the integrator's report lists each file.
- **Replays on 60 days of committed bars**, run by the research runner with network denied. They measure how often the rules fire and what the plans cost; they are not fills.
- **No live fills.** No real-money order, cancellation or paper-account fill was sent to produce this release. Zero orders have ever been sent by this app; the first live entry after the owner accepts the new policies will be the first end-to-end test of the broker path with real money, at the $15 purchase size.

## Unchanged safety properties

Durable claim before every POST; one `client_order_id` per attempt and no resubmission of an unknown order; unknown outcomes reconciled by identifier with the 4.3.2 60-second lost-request resolution; exits never compete (stop cancellation confirmed before a sale); the deployment-readiness contract and the `ny-session-two-v1` session-entry protocol identifier the updater checks; the composite deployment permission fingerprint (a changed policy version is exactly what makes both cards require review); shared cash reservation across engines; one Socrates position at a time and one crypto position per selected market; spot long-only crypto with no simulated short; the 15-second submission budget from quote receipt and the 90-second crypto confirmation window.
