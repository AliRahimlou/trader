# Primary-source follow-up: leader timing, zones, and the two entry variants

Reviewed September 17, 2026. This is an additional source audit, not a performance result or deployment approval. It preserves the original [rule map](source-rule-map.md) and historical [research report](REPORT.md).

## Conclusion

The current mechanical interpretation is **not an exact implementation of these recordings**. The strongest newly inspected evidence is that a leader's rejection can already be under way: at V3 01:15 and 01:19, Nvidia is below its blue upper area while the narrator discusses its rejection and selling. A rule requiring every counted leader's newest candle to touch its own algorithmic four-hour zone is not demonstrated. Replacing that rule with the color of the newest candle alone would also lose the source's location and rejection context.

Five-minute leader candles are directly visible. Four-hour **Nasdaq** location, hourly event context, qualitative leader participation, and opposite VIX reaction at an area are source-backed. Four-hour zones on every leader, exactly four votes with no opposing vote, a one-candle reaction lifetime, and a precise candle formula remain numerical translations introduced by the app.

The two clips describe different initial Nasdaq location/event variants with substantially shared confirmation logic. Evaluating those variants separately is consistent with the owner's instruction and avoids hiding one behind the other's priority. Neither clip establishes two wholly independent complete strategies with separate exit or sizing systems.

## Sources and method

Only these two primary recordings contribute source requirements:

| ID | Recording | SHA-256 |
| --- | --- | --- |
| V2 | `ScreenRecording_09-14-2026 17-25-23_1.MP4` | `e91af8d2ef2a6b6570da05f146cc105ba650379771706368244cec79613a17f7` |
| V3 | `ScreenRecording_09-14-2026 18-11-24_1.MP4` | `b6d885efc6a93e90c37012a5cd8f1d854e2e221ba94b4b905bd899d050a879ad` |

The owner's spoken filename `11-25-23_1` matches no supplied local file; `17-25-23_1` is the other repeatedly supplied primary recording. V1 is not imported into these requirements.

This follow-up reread both complete retained [V2](../video-transcripts/video-2.srt) and [V3](../video-transcripts/video-3.srt) reading transcripts, inspected relevant retained frames, and extracted and visually inspected **12 additional unaltered frames** from hash-verified original MP4s. It did not generate a new audio transcription or claim frame-by-frame coverage of every instant. The retained transcripts are machine-assisted; the [transcription method and limitations](../video-transcripts/README.md) still apply.

New frames and exact decoded timestamps are preserved privately in `runtime/research/video-evidence-followup/`, with `extraction-manifest.json` recording source and frame hashes. Original images can contain incidental on-screen notifications; no source media were uploaded or added to Git.

| Source | Requested seconds | Decoded source times in seconds |
| --- | --- | --- |
| V2 | 44, 49, 55, 60 | 44.003333, 49.005000, 55.005000, 60.005000 |
| V3 | 70, 75, 79, 88, 101, 104, 137, 145 | 70.006667, 75.006667, 79.006667, 88.008333, 101.010000, 104.010000, 137.013333, 145.013333 |

These are elapsed **recording** timestamps, not synchronized chart timestamps or an order clock. The charts display historical dates different from the phone-recording filenames. They are not September 17 market observations.

To reproduce any additional frame without modifying its chart pixels:

```sh
ffmpeg -hide_banner -loglevel info -ss 79 -copyts \
  -i '/Users/alirahimlou/Downloads/ScreenRecording_09-14-2026 18-11-24_1.MP4' \
  -frames:v 1 -vf showinfo runtime/research/video-evidence-followup/v3-079s.png
```

Check the source SHA-256 before extraction; `showinfo` reports the decoded timestamp. Do not infer precision from a requested integer seek time.

## Timestamped answers to the implementation questions

| Question | Source evidence | What it supports | What it does not establish |
| --- | --- | --- | --- |
| Which leader timeframe? | V3 retained frame 01:35.008333 shows Apple with `5`; retained 02:23.013333 shows Nvidia with `5`. Additional 01:15.006667 and 01:19.006667 also show Apple's `5` header. | Five-minute candles are the demonstrated leader chart interval. | A universal completed-candle-only requirement, permission to use an unfinished candle, or a fifteen-minute leader rule. V2 supplies no leader interval. |
| Must the current leader candle touch the zone? | V3 01:13.600–01:25.440 discusses rejection from supply and subsequent selling. At 01:15.006667 and 01:19.006667, Nvidia's recent candles sit below the upper blue area after earlier rejection candles. | A rejection and the continuing behavior after it are relevant. Location and current behavior can be distinct pieces of evidence. | A requirement that the latest candle itself overlaps the area, exact rejection age, or a maximum number of following candles. No complete accepted entry is shown. |
| Does every company need its own zone? | V3 01:08.560–01:25.440 emphasizes Nvidia and Apple at supply. At 01:30.960–01:40.680 the speaker asks whether the leaders are buying/selling and also at supply/demand or pivotal ranges. V2 00:51.480–01:06.400 includes conflicting location among reasons to abstain. | Leader location matters to the narrative, particularly in the shown short example. | A universal requirement that all seven be touching zones, that four must touch zones simultaneously, or that a company away from an area must always be neutral. |
| Are leader areas repeated four-hour pivots? | The explicit four-hour marking passage is V3 00:18.160–00:41.880 on the Nasdaq chart. Leader views later show blue/orange areas plus daily/weekly/monthly reference lines; their construction is never demonstrated. | Four-hour repeated touches or crossings are a Nasdaq location method. Leader supply/demand areas exist on the displayed charts. | Applying the exact same repeated-extrema algorithm to every leader. The origin, indicator, timeframe, width, age, and invalidation of the blue/orange areas remain unknown. Additional frames make some reference-line labels clearer but do not establish those lines as entry gates. |
| Are simple price-direction votes enough? | V2 00:41.000–00:51.480 describes majority buying versus selling. V3 adds supply rejection and selling pressure, including 01:15.720–01:25.440. | Directional participation is necessary qualitative evidence. | Equating buying with `close > open`, selling with `close < open`, a return over an arbitrary window, or ignoring zone context in the chart example. Those are candidate proxies, not recovered source formulas. |
| Is four of seven with zero opposition exact? | V2 00:41.000–00:51.480 says majority; 00:51.480–01:06.400 says abstain if mixed. Additional V2 frames confirm this is narration rather than a displayed seven-company vote table. | Majority direction and avoidance of contradictory evidence are both spoken. | Equal weighting, the denominator with neutral/missing companies, four as a mandatory threshold, or one opposing company as a universal veto. The overlap between majority and mixed evidence is unresolved. |
| Is five minutes the VIX rule? | V3 01:44.010000 displays the VIX on five minutes while it falls. At 01:47.400–01:52.600 the speaker explicitly switches to fifteen minutes for additional confirmation; retained 01:55.010000 shows `15`. | Fifteen-minute VIX is demonstrated as an additional confirmation view. Actual opposite movement must arise from a relevant area. | A universal five-minute VIX requirement, using rising/falling alone without location, or entry based solely on anticipating a turn. |
| Is the four-hour variant always a continuation? | V3 00:41.880–00:57.440 describes break/retest; 01:01.520–01:25.440 considers a short after a break above a Nasdaq area. Additional 01:28.008333 and 02:17.013333 show the Nasdaq above the red line in that discussion. | Location/event and chosen trade direction are separate. Leaders and VIX determine the conditional short example. | Equating an upward break with automatic long permission or requiring every short to begin with a downward break. Exact retest geometry remains unspecified. |
| Are V2 and V3 separate variants? | V2 00:01.360–00:22.720 marks prior-day high/low and defines sweep as breaking a pivotal boundary on a higher timeframe. V3 00:18.160–00:57.440 marks repeated four-hour areas and discusses hourly breaks/retests. Both proceed to leaders and VIX. | Two source location/event variants with shared confirmation concepts. | Requiring both initial events sequentially, prioritizing one so the other is never evaluated, or asserting separate source-defined exits, sizing, and performance. |

## What the current code actually adds

The inspected `pivot/strategy.py` uses these rules; they must not be mislabeled as verbatim source instructions:

1. `leader_diagnostics` builds each leader's levels from confirmed four-hour swing extrema and observes fifteen-minute candles.
2. `reaction` requires the current candle to overlap a pre-existing zone, close outside it, have the intended body direction, and close beyond the previous close. The source does not state this conjunction.
3. Default `leader_confirmation` requires at least four agreeing zone reactions and zero opposing reactions. Neutral and absent zones are implementation states, not source-defined voting categories.
4. Default vote persistence is one latest candle. The inspected Nvidia example does not establish immediate expiry after the rejection candle.
5. `analyze` chooses a four-hour event first and checks the previous-day sweep only if no such event exists. It reports one chosen event rather than independently reporting both branch evaluations.
6. The app uses QQQ and regular-session aggregation. The displayed Nasdaq view shows ETH; the exact traded instrument and universal session convention are not conclusively recoverable from these clips.
7. Stop, target, order types, one-position restriction, fractional sizing, and end-of-day exits are implementation policies; these recordings omit them.

These observations explain fidelity gaps. They do not establish that removing a restrictive condition is profitable, or that a new rule should be selected because it creates more entries.

## Deterministic specification: supported structure and explicit unknowns

A reviewable specification can preserve the source-backed structure now:

- **Branch A — prior-day sweep:** establish previous-day high/low before the trading decision, observe a higher-timeframe boundary break, then require leader and opposite VIX confirmation. Record that the choice of instrument, session, precise bar interval, break detection, and event lifetime is an app interpretation. Do not add a mandatory close-back-inside and describe it as V2's definition of sweep.
- **Branch B — four-hour location / hourly event:** identify areas from repeated historical interaction before the decision, observe the described hourly break/retest context, then evaluate leader and opposite VIX confirmation. A method limited to swing extrema does not cover all source-described crossings. The source does not define which crossings count or an algorithm for their grouping.
- **Leader evidence:** record area provenance and location separately from rejection onset, current buying/selling evidence, evidence time, age, and invalidation. Five-minute sampling matches the demonstrated interval. Numerical zone, reaction, vote and lifetime rules must each be labeled as a versioned interpretation.
- **VIX evidence:** use actual VIX; an index short needs buying from a relevant VIX area, and a long needs the opposite. The demonstrated additional view is fifteen minutes. Quantitative area and reaction rules remain interpretations.
- **Branch attribution:** retain both branch results even if they refer to the same market event. Shared execution should deduplicate one underlying order opportunity; two labels must not cause duplicate purchases.
- **Execution:** evaluate whether the account can execute the resulting direction and size independently of strategy eligibility. Unsupported shorting must remain an explicit execution blocker; a long purchase does not reproduce the source's short example.

The source does **not** supply enough information to fill all the unknowns with a unique algorithm. An honest specification can be exact about the data and declared interpretation without claiming the creator used that same algorithm.

## Immediate fidelity work and evidence needed next

1. **Preserve separate measurements of location and behavior.** Observe five-minute leader bars, record the last evidenced reaction and continuing behavior, and compare them with the current fifteen-minute/current-touch rule. Lock the candidate semantics before looking at outcomes. Do not silently substitute green/red candles for the source's rejection context.
2. **Audit each branch independently.** Produce previous-day-sweep and four-hour-break/retest diagnostics at the same decision times, with distinct source labels and one deduplicated execution opportunity. Avoid a hidden fallback priority becoming a source rule.
3. **Resolve area construction.** Build hand-labeled, point-in-time source examples first, with marked levels and outcomes hidden. The supplied clips can illustrate location concepts but cannot yield the missing leader-area formula. A stronger claim requires the creator's area construction method or additional examples showing the areas before price reacts.
4. **Resolve participation and timing.** Several synchronized accepted and deliberately skipped examples must show all seven companies and VIX together. They should distinguish neutral evidence from contradiction and identify when an earlier rejection remains valid. The supplied two-leader view cannot establish a seven-vote threshold.
5. **Keep performance and order evidence separate.** Collect after-cost fills, rejected orders, protection acknowledgments, exits, and reconciliations. A software test, an accepted chart illustration, and a profitable completed trade are different claims.

For live monitoring, each decision should answer: which source branch is active; which pre-existing Nasdaq area/event qualifies; each leader's area, reaction time and current behavior; what actual VIX is doing at its area; the exact remaining gate; and whether execution was eligible, attempted, rejected, filled, protected or closed. Report a source mismatch explicitly instead of treating a no-trade day as proof of either failure or fidelity.

## Evidence limits that remain after this follow-up

There is still no complete synchronized source setup with a known executable entry, stop, target, exit and result. There is no source-supported exact stop-loss formula, profit target, position size, daily loss limit, confirmation lifetime or numerical participation threshold. Additional frames improve confidence about the demonstrated five-minute timeframe and continuing-rejection context; they do not fill those missing rules.

No runtime code, broker settings, orders, or deployment state were changed by this source audit.
