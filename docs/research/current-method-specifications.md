# Current methods and separately sourced research candidates

Specification date: September 18, 2026. This document records the **v3 app interpretation**, and separates it from proposed experiments. It does not change live rules. The frozen experiment records the exact committed analyzer and its file hashes; `research/current-method-plan.json` defines the limited comparison before it runs.

## Evidence status

The primary recordings do not supply a complete numerical strategy. The public counterpart of the four-hour explanation is [Simple system that wins](https://www.youtube.com/watch?v=DR6OFHaqUSw). The previous-day explanation is the user's `17-25-23_1.MP4`, with its retained [V2 transcript](../video-transcripts/video-2.srt). Both are qualitative source evidence, not a performance record.

As of this research pass, complete transcripts of 16 public channel items have been reviewed, out of 655 unique items in the saved inventory. That is not all videos, and not continuous playback. The previously observed header count of 663 remains unreconciled. New public sources add examples of session limits and exits, but no exact five-agree/one-opposing formula. Private Discord material is not reproduced in this document.

## Definitions common to the two current methods

| Component | Precise current interpretation | Source status |
| --- | --- | --- |
| Instrument | QQQ signals and orders; seven distinct companies: AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA | Nasdaq and tech leadership are discussed; QQQ execution is our instrument translation from the futures examples. |
| Observation time | Only completed bars, aware timestamps, ordered and nonduplicated. QQQ uses regular-session 15-minute source bars aggregated to full 60-minute and 240-minute buckets anchored at 09:30 New York; incomplete end-of-session buckets are omitted. | Exact bucket/session alignment is an app convention. |
| QQQ history | Sixty calendar days within a verified exchange-session calendar. Previous-day high/low comes from the immediately preceding exchange session. | Lookback and regular-session definition are app choices. |
| QQQ repeated areas | Each historical four-hour low/high proposes a fixed band of price × (1 ± 0.001). The first chronological overlapping anchor wins. A second touch/crossing at least two bar indices later establishes the band; subsequent prices do not move it. Establishment must precede the event bar's start. | Repeated touches/crossings are source-aligned; tolerance, overlap precedence and confirmation count are app choices. |
| Leader areas | Same fixed-anchor procedure applied to genuine five-minute data over seven recent exchange sessions. | Five-minute Apple/Nvidia examples are visible; the colored-area construction is not supplied. Microsoft is shown at fifteen minutes in one example. |
| Leader reaction | Bar intersects an established area; a long vote requires close above the area's high, above its own open, and above the preceding close. A short vote reverses those conditions. | These exact inequalities are an app interpretation. |
| Reaction persistence | An event-scoped vote may originate at or after the Nasdaq event's origin, in the current session. Age must be strictly less than 15 minutes. Every intervening five-minute bar must exist. No later close may cross the invalidation edge, and latest close must remain at least as far in the reaction direction as the originating close. Newest reaction at each area replaces older evidence. Simultaneous opposing active areas make that company conflicting. | The source uses continuing reactions but supplies no exact lifetime/invalidation formula. |
| Participation | At least five votes in one direction; at most one opposite vote. Neutral is distinct from missing or conflicting. All seven latest observation timestamps must match, though reaction origins may differ. | Five/one is the owner's selected app interpretation. Numerical quorum and synchronization are not creator formulas. |
| VIX | Actual spot index, completed fifteen-minute bars. Latest reaction must be opposite the trade direction at a pre-existing repeated swing-extreme area. Use the existing `zones`/`reaction` geometry; exclude the two latest bars when constructing the area. Fresh actual-index quote admission remains separate from candle qualification. | Opposite VIX reaction/location is qualitative source evidence. Fifteen minutes appears in V3; its universal use and exact swing algorithm are interpretations. |
| Direction | Take the qualified leader direction, independently of Nasdaq's originating break direction, then require opposing VIX confirmation. | V3 conditionally considers a short after an upward Nasdaq break. |
| Event identity | QQQ + method + fixed zone bounds + establishment time + original event time; retests, direction changes and later receipts do not create a new identity. | Engineering duplicate-prevention rule. |
| Expiry | Original event + 180 minutes; current session only; missing intervening hourly data invalidates the event. Entry additionally respects the earliest reaction, latest-observation and data-freshness deadlines. A receipt refresh cannot renew a fixed candle/event deadline. | Numerical limits are engineering choices. |
| Context | Confirmed one-hour/four-hour swing structure is descriptive; it is not another entry gate. | Higher-timeframe context is discussed, but no universal deterministic trend veto is supplied. |

## Method A: four-hour area / hourly break and retest

Primary source: [V3, 0:18–0:57](https://www.youtube.com/watch?v=DR6OFHaqUSw&t=18s), then the conditional short example at 1:02–1:25.

1. Construct eligible fixed four-hour areas using only history known before the event.
2. On a completed hourly bar, start an upward event when previous close ≤ area high < current close; start a downward event when previous close ≥ area low > current close.
3. Require a **later** completed hourly retest. Upward: low touches/breaches area high while close finishes above area high and above open. Downward: high touches/breaches area low while close finishes below area low and below open.
4. Before confirmation, invalidate an upward event after a later hourly close below area low; downward after close above area high. Expiry/session/data-gap rules above also apply. A new crossing is required after invalidation/expiry.
5. Evaluate common leader/VIX/exit-geometry requirements. A retest confirms the location event; it does not itself dictate buy versus short.

The closed-hour trigger, later-bar retest, three-hour lifetime and price inequalities are a reproducible implementation, not facts proved by the video.

## Method B: previous-day boundary sweep

Primary source: V2 0:01–0:27, previous-day high/low and price breaking through a pivotal area. Its majority/mixed-evidence discussion follows at 0:33–1:14.

1. Obtain the verified immediately preceding regular session's exact high and low as zero-width levels.
2. On a completed hourly bar, start a high sweep when previous high ≤ prior-day high < current high. Start a low sweep when previous low ≥ prior-day low > current low.
3. The sweep immediately confirms the location event. **No mandatory close back inside the level is added.** Entry still requires the common leader/VIX/exit geometry checks.
4. A high-sweep event is invalidated by a later hourly close below its origin bar's low. A low-sweep event is invalidated by a later close above its origin bar's high. The same identity, expiry, session and continuity rules apply.

This branch is evaluated independently of Method A. A trade need not pass both location methods.

## Current exit and execution interpretation

Long stop: minimum of event-area low, origin low, confirming low and current hourly low, minus $0.01. Short stop uses the maximum corresponding highs plus $0.01. Round to cents. Target is the nearest pre-existing four-hour or previous-day opposing boundary beyond the current reference price; the target area must have existed before the origin bar began. Require strict stop < entry < target for a long, reversed for a short.

The saved dollar amount is **purchase notional**, not maximum loss. Planned stop-distance loss also depends on quantity and fill price; actual gap/outage losses can differ. Production has additional account, quote, size, permission, duplicate, reconciliation and protective-order checks. Candle qualification alone cannot send an order. The isolated experiment models one fractional long QQQ position and rejects shorts; it therefore does not measure futures trading or full production broker behavior.

The primary recordings do not specify these exact stops, target geometry, close-of-session policy or order types. The [September 26 discipline clip](https://www.youtube.com/watch?v=W9ELjAuRur4&t=20s) supports a preplanned loss and next pivotal range qualitatively. It does not certify this exact algorithm.

## Version 4.4 interpretation

Specification date: September 22, 2026. The definitions above remain the v3/4.3 record. Version 4.4.0 ([release note](../production-v4.4.0.md)) replaced the numerical interpretations below after the September 21 replay found that the 4.3 values admitted almost nothing (median target 0.52 R; VIX gate true on 28% of bars at random; one conflicting leader vetoing seven). Each is an app choice behind a new policy version and requires owner re-review; none is a formula the recordings state. The numbers are the committed constants at the tagged revision.

| Component | Version 4.4 interpretation | Replaces |
| --- | --- | --- |
| Target | Nearest opposing pre-existing four-hour or previous-day area beyond the reference price, **excluding the event's own area**; establishment before the origin bar still required. | Nearest opposing band including the swept/broken level itself. |
| Minimum reward-to-risk and stop distance | The nearest qualifying level must satisfy (target − entry) / (entry − stop) ≥ **1.0** (reversed for a short); nearer levels below the multiple are skipped for the next one. The stop distance must be at least **0.1% of the entry price**. Stop formula unchanged. Nothing is resized to reach the ratio. | No minimum; median 0.52 R over 223 replayed plans; three-cent stops admitted. |
| VIX areas | Repeated swing extremes from completed fifteen-minute actual-VIX candles, excluding the two latest bars, with tolerance **1%** of the VIX level (2% band). | 0.1% fixed band (≈0.015 points at VIX 15; median bar range 0.656%). |
| VIX reaction | The reaction candle may be either of the **last two** completed candles of the current session (contiguous fifteen-minute candles); no later close may cross back through the area against it, and the latest close must still sit beyond the area boundary. Direction opposite the QQQ trade, as before. Fresh actual-index quote before entry unchanged. | Single latest candle, no persistence, no invalidation. |
| Conflicting leader | Simultaneous opposing active areas make that company **neutral** (neither agreeing nor opposing). | Conflicting company vetoed the whole vote. |
| Reaction origin | A reaction may originate **before** the Nasdaq event's origin in the same session if it is still valid at evaluation. | Pre-origin reactions discarded. |
| Follow-through | Later closes must stay **beyond the area edge** (above the area high for a long vote, below the area low for a short vote); a close back through the area invalidates. | Every later close had to be at least as far as the originating close. |
| Participation | Unchanged: at least five agreeing, at most one opposing, all seven latest observations synchronized, age under 15 minutes, no missing intervening bar. | — |
| Session allowance | Two entry attempts **per strategy** per New York session, claimed durably before submission; protocol identifier `ny-session-two-v1` unchanged. | Two attempts shared by both engines. |
| Rejection response | Strategy-scoped pause with the reason retained; optional `PIVOT_ALERT_WEBHOOK_URL` alert; global Live untouched. | Global Live switched off silently. |
| Entry cutoff | No new Socrates entries in the final 30 minutes of the regular session; closing window five minutes before the close unchanged. | Ten minutes. |
| Clock skew | Session-open and quote-age comparisons tolerate 5 seconds of host/broker skew. Submission budgets unchanged. | Exact comparison. |
| Crypto cost gate | Net reward after 0.25%-per-side fees and the execution allowance ≥ **1.0** × net risk; the skip reason exposes fees, gross/net reward, net risk and ratio. Stop and 2 R gross target unchanged. | Positive net target only (admitted ≈ 0 net reward). |
| Crypto opening range | Valid with at least **44** of 48 five-minute bars present between 00:00 and 04:00 ET; range from present bars. A missing later slot has no close and cannot start or confirm an excursion; a slot missing inside an excursion retires it. The latest expected slot must be present for a confirmation to be current. | Any missing bar voided the day. |
| VIX retries | Retries every 30 seconds within the period, at most six attempts per period, stopping when the projected monthly need would exceed the allowance; late publications do not count as recoveries; up to four successful quote refreshes per period. | One attempt plus one recovery per period (up to 14-minute waits); one quote per period. |
| Versions | Socrates policy **`nasdaq-qqq-execution-v6-reward-risk-vix-persistence`** (analysis `nasdaq-video-interpretation-v4`); crypto rule **`range-reversal-v2`**; crypto policy **`range-spot-execution-v2`**. Event identities carry the rule version. | `nasdaq-qqq-execution-v5-five-leader-majority`, `range-reversal-v1`, `range-spot-execution-v1`. |

Deferred at 4.4.0, delivered in 4.5.0 (next section): an inverse-ETF proxy for Socrates shorts (the account cannot short and $15 cannot form a whole QQQ share) and the afternoon four-hour QQQ bucket (a data-pipeline change kept out of this release). The research candidates F1 and X1 below are unaffected and remain isolated from production.

## Version 4.5 interpretation

Specification date: September 22, 2026. Version 4.5.0 ([release note](../production-v4.5.0.md)) follows the owner's frame-by-frame re-read of the 18:11 recording: NQ futures with extended hours, four hand-drawn 4h lines at swing points touched more than once over five to six weeks, kept unchanged on the 1h, break-then-retest including a fade at a just-reclaimed line, leaders read against an indicator of period levels, and a VIX with no lines where "look left" means scrolling back to a consolidation base. Each row states the committed constant and separates it from what the recording shows; every number is an app choice behind a new policy version (`nasdaq-qqq-execution-v7-video-aligned`, analysis `nasdaq-video-interpretation-v5`) and requires owner re-review. Rows not listed here (target exclusion, 1.0 R minimum, 0.1% minimum stop, VIX two-candle persistence, session allowances, pause, cutoff, skew, crypto rules) are unchanged from the 4.4 section above.

| Component | Version 4.5 interpretation | Replaces | Video evidence |
| --- | --- | --- | --- |
| Four-hour frame | Two buckets per regular session: 09:30–13:30 (240 min) and 13:30–16:00 (150 min, emitted as a 4h bar); early-close sessions contribute the buckets their bars complete. Hourly frame unchanged. | One 09:30–13:30 bucket; afternoon extremes never became areas. | Continuous futures 4h candles, about six per weekday; no overnight bars exist for QQQ, so the afternoon bucket is the closest regular-session reading. |
| Four-hour area candidates | Confirmed 4h swing pivots (high above both neighbours or low below both) propose a band of price × (1 ± **0.001**); established by at least **two non-adjacent** interactions (wick or body, ≥ 2 bar indices apart); interaction count carried on the area; at most the **16** areas nearest the current price are kept; overlapping bands merge into the earlier anchor; 60-calendar-day window. | Every bar high/low proposed a band; second touch established; no swing requirement; no cap. | Lines at swing highs/lows "broken through or touched more than once" over ~5–6 weeks; "here's our high"; four lines in ~900 points. Tolerance, count, cap and window are app choices. |
| Method A retest | Later completed hourly bar whose range **returns to the area** (intersects the band), either direction. Trade direction comes from the leader majority and the opposite VIX reaction at confirmation, not from the break direction, so a fade at a just-reclaimed level qualifies. Invalidation through the far edge before the retest unchanged. Method B unchanged. | Retest had to touch the edge and close beyond it in the break direction; direction followed the break. | "We break and we trade the retest"; live example is a short at a line price just reclaimed, "if the leaders sell and VIX buys". |
| Event life | Origin bar to the **end of the next regular session**; missing intervening hourly bar still invalidates; one entry per event; entry deadlines unchanged. After the retest a plan exists only while the latest completed hourly candle touches the area or closes within 0.4% of it; otherwise the event waits for another return. Target levels must predate the entry candle (not the break), so a next-session retest can target the break day's high/low and afternoon four-hour areas. | Origin + 180 minutes, current session only. | Retests pointed at one to three days after the break; no limit stated. Two sessions is an app bound. |
| Leader areas | Union of 5-minute repeated-interaction zones (unchanged) and zero-width **period levels** from the company's data: previous day high/low/close, session open, previous week high/low/mid, Monday high/low, week open, month open, session VWAP. Sessions inside the seven-session 5-minute window use their regular-session 5-minute candles; older sessions use provider daily candles (an entry input, not a readiness gate; a failed daily read keeps the last validated candles). Same close-beyond reaction inequalities at either kind. | Interaction zones only. | Indicator-plotted period levels plus supply/demand boxes on AAPL/NVDA/MSFT; boxes and weekly/monthly VWAP are not reproduced. |
| Participation | At least **4 of 7** agreeing, at most **1** opposing; conflicting company neutral; all seven latest candles synchronized; missing candle blocks. | 5 of 7, at most 1 opposing. | "Majority buying → long, majority selling → short, mixed → no trade"; two names shown, four named, no count stated. |
| Reaction window | A reaction stays valid for **60 minutes** while later closes stay beyond the level in the vote direction; a close back through drops it. | 15 minutes. | A rejection candle hours old is cited; no window stated. |
| VIX areas | Union of ±1% repeated swing-extreme clusters (unchanged) and **consolidation bases**: ≥ **4** consecutive completed 15-minute candles whose highs and lows lie within **2%** of each other, bounded by the run's low and high. Reaction rule (either of the last two candles, opposite direction, persistence, fresh quote) unchanged. | Swing clusters only. | No lines on VIX; "look left" 2–3 days to the base a spike launched from (~21.0 on Oct 16). Bar count and width are app choices. |
| Short execution | A confirmed Socrates short buys **PSQ** (−1× daily inverse Nasdaq-100 ETF) for the saved dollar amount; stop and target translated from the QQQ plan by percentage, measured from the live QQQ bid at admission to the QQQ stop and target levels, and enforced on the PSQ price; intraday only (closed in the five-minute closing window, never held overnight); same quote, spread, budget, protection and exit rules as a QQQ long; one Socrates position at a time across both symbols. | Short setups skipped and reported on the cash account. | "Nasdaq short" on futures; the proxy is an owner-accepted instrument translation with tracking error, daily reset and a separate quote. |
| Visibility | Read-only `/api/chart` (`pivot/chart_data.py`) and the dashboard's "Look left" panel (`pivot/web/chart.mjs`) show the completed 4h/1h candles, areas with pivot price, bounds and interaction count, previous-day levels, live events, leader period levels, and VIX swing pivots and consolidation bases built as the analysis builds them. No analysis, no orders, no permission writes. | Levels were numbers in the analysis state. | He verifies every line by scrolling the chart. |
| Versions | Socrates policy **`nasdaq-qqq-execution-v7-video-aligned`**; analysis **`nasdaq-video-interpretation-v5`**; crypto rule `range-reversal-v2` and crypto policy `range-spot-execution-v2` unchanged. | `nasdaq-qqq-execution-v6-reward-risk-vix-persistence`, `nasdaq-video-interpretation-v4`. | — |

Not aligned and documented as such: Alpaca has no futures (QQQ/PSQ, no back-adjustment); regular session only (no overnight extremes, opening gaps); no supply/demand boxes; no 5-minute VIX (InsightSentry Free is 15-minute); no weekly or monthly VWAP beyond the session VWAP. The research candidates F1 and X1 below are unaffected and remain isolated from production.

## F1: closed five-minute location candidate

**Implemented only in the isolated research module; authentic native input evidence is still missing.** The algorithm is `research.five_minute_candidates.fast_location_candidates`. It cannot authorize production orders. No fifteen-minute OHLC is converted into five-minute candles.

- Keep Method A's four-hour areas and Method B's prior-day levels; construction and frozen zones are unchanged.
- Replace only the completed-hour location observation with **genuine completed five-minute QQQ candles**. Method A still requires a separate later five-minute retest; Method B confirms on the five-minute sweep. No forming-bar or guessed intrabar trigger is introduced.
- Keep event lifetime at 180 minutes, same-session scope, no missing intervening five-minute bar, fixed identity tied to the new origin, and the same leader interpretation. Use five-minute origin/confirming/current extremes for the declared stop formula; target levels remain four-hour/prior-day.
- Evaluate actual VIX with **genuine native five-minute observations** using the same explicitly declared repeated-swing reaction construction on that timeframe. Require enough authentic warmup and complete calendar coverage. This is a separately versioned paired-timeframe hypothesis, not an isolated causal estimate of changing only QQQ timing; a later factorial study would need a new predeclared plan.
- Causal decision time is bar close plus declared publication delay. Entry must precede all evidence deadlines at an observed future executable price. No bars are created by splitting a larger candle.

Motivation: the creator's [five-minute Nasdaq/VIX lesson](https://www.youtube.com/watch?v=egiUVtmGlZs), plus five-minute entries in the instructional videos. Those examples do not prove that this mechanical candidate is profitable or that it replaces the primary hourly method. Test net results, not simply an increase in signals.

## X1: VIX opposing-pivot exit candidate

**Implemented only in the isolated research module; authentic native input evidence is still missing.** `freeze_vix_exit`, `vix_exit_decision`, and `research_exit_reference` implement the frozen boundary, causal decision and conservative observed-open execution reference. Entry remains the current independently evaluated Method A or B.

- At entry, use only native five-minute VIX bars already available to establish repeated swing-extreme areas. Freeze the nearest relevant opposing area beyond the current VIX price: upper area for a QQQ short, lower area for a QQQ long. No eligible area means candidate unavailable, not an invented target.
- While holding, a subsequent completed five-minute VIX bar reaching that area proposes an exit. The entire bar must start at or after entry; an earlier touch in a bar spanning entry cannot be credited. The decision is knowable only after that bar closes plus declared publication delay. Sell/cover at the next actually observed QQQ execution price, with explicit spread/slippage/fees; do not backdate a fill to the VIX touch.
- QQQ protective stop, QQQ target, and session-close exit remain active. Conservative ordering applies when available OHLC cannot determine which event happened first; no extra entry is allowed on that ambiguous bar.
- Never use VIX futures, CFDs, ETFs or synthetic five-minute splits as substitutes. Keep data provenance, publication times and stale-feed behavior explicit.

Motivation: [April 8 stream, 3:03–3:20](https://www.youtube.com/watch?v=899Ojnsl18A&t=183s), where the creator describes exiting as five-minute VIX reached prior wicks. The frozen-area algorithm and closed-bar timing above are proposed translations, not a source formula.

## Session/risk candidates must remain separate

The [two-trade clip](https://www.youtube.com/watch?v=W9ELjAuRur4&t=4s) proposes one session/two total trades as a discipline challenge and also mentions consecutive losses. The [April 4 clip](https://www.youtube.com/watch?v=TOHLsVnrnpk&t=4s) illustrates four trades with two losing trades allowed. Those are distinct policies.

Before implementing any comparison, define total trades versus losing trades versus consecutive losses, whether a break-even result resets a streak, net fees, partial fills/exits, rejection counting, exchange-day reset, restart persistence and the owner's actual loss limits. A manual-trader comment about closing at a loss amount does not justify removing an unattended broker stop. No new loss dollar limit is imposed by this document.

## Reproducible bounded experiment

Run `python -m research.current_method_experiments --help` for the offline runner. It requires the saved native-leader dataset, native warmup, older actual-index dataset, an explicit committed engine revision, and a new output directory. Network access is denied while the run executes. The recorded manifest includes source, plan, input and output hashes.

The executable plan compares only current five/one against a predeclared four/zero control, independently for each current method. It uses a synthetic $100 research budget with a $5 purchase amount, never reads the account balance, and tests base/higher-cost/extra-latency assumptions. Session partitions are chronological, disjoint and close simulated positions within each session. All previously inspected history is labeled known; an untouched future test remains unavailable. The small sample cannot select a winner.

With only fifteen-minute QQQ execution history, the next available open may occur after a candidate's expiry. The runner must record that rejection, rather than fabricate a price between opens. A zero-trade result then describes insufficient executable evidence; it is not evidence of zero risk or profitability. F1/X1 have synthetic tests of their native-input contract and algorithms. Their historical comparison remains blocked by missing authentic five-minute QQQ/VIX inputs; the report does not attach fabricated returns to them.

The optional `research.native_method_experiments` runner and separately versioned `research/native-method-plan.json` implement the subsequent native-input evaluation path. The loader verifies source identity, native resolution and calendar alignment. Each checkpoint requires complete prior/current-session QQQ five-minute history and fourteen calendar days of VIX five-minute history, while retaining the base higher-timeframe/leader coverage contract. Partial native sessions are excluded from full-session comparisons. These explicit warmup assumptions belong to the research plan, not a claim about the creator's rule.

F1 is evaluated under the current five/one policy with the same cost scenarios and execution deadlines. X1 compares exits only for admissible baseline entries at the same entry price and quantity; proceeds are not reinvested, so this is a matched-entry exit comparison rather than a new portfolio equity curve. A source failure suppresses the discretionary VIX exit without disabling QQQ protective exits. Native candle history is still not executable quote history: even five-minute openings can occur after the ninety-second receipt deadline. The follow-on path has fixture tests, but has not been run against actual native VIX/QQQ data because the required capture remains unavailable.

Validation follows the distinction in [Alpaca's paper-trading documentation](https://docs.alpaca.markets/us/docs/paper-trading): simulated execution omits live effects. Multiple tested variants and previously viewed data also create selection bias; see [Bailey et al., Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). Preserve the complete experiment ledger and untouched future observations instead of tuning until a historical result looks profitable.
