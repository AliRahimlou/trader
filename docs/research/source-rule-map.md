# Primary-video reconstruction and source fidelity

Reviewed September 17, 2026. Deployed baseline code: `facc507ae2761a76916031994bcf9f956f9369fe`. Research protocol frozen at `c5894bdee6508cde8876a215304fa2f7326ef557` before candidate evaluation.

## Finding

The recordings support a **location → Nasdaq event → leader behavior → VIX behavior** workflow. They do not specify a complete executable strategy. Four-hour Nasdaq levels, hourly breaks/retests, leader participation, and a directionally opposite VIX reaction at an area are supported qualitatively. Exact zone geometry, numerical participation, confirmation lifetimes, stop/target mechanics, sizing and risk limits remain interpretations or engineering choices.

Fresh frame inspection adds a material qualification to the prior audit: **the displayed Nvidia and Apple charts use five-minute candles**, while the code uses fifteen-minute leader reactions at algorithmically constructed four-hour zones. The videos do not show how the leaders' blue/orange zones were constructed. There is no basis to call that part of the code an exact reproduction.

Neither recording supplies a complete executed trade or after-cost return. This review establishes source evidence and its limits; it does not establish an edge or justify loosening rules to produce trades.

## Evidence and method

The original local MP4 files were probed and hashed again. All retained reading transcripts were read and compared with 26 newly extracted, visually inspected original frames: 8 from V2 and 18 from V3. The break/retest passage was examined at 00:45, 00:48, 00:50 and 00:53. This review uses the retained machine-assisted transcripts; it does not claim a fresh audio transcription or frame-by-frame review of every instant. Their marked ambiguities remain unresolved.

| Source | Original file | Duration | SHA-256 |
| --- | --- | ---: | --- |
| V2, primary | `ScreenRecording_09-14-2026 17-25-23_1.MP4` | 100.138753 s | `e91af8d2ef2a6b6570da05f146cc105ba650379771706368244cec79613a17f7` |
| V3, primary | `ScreenRecording_09-14-2026 18-11-24_1.MP4` | 150.359501 s | `b6d885efc6a93e90c37012a5cd8f1d854e2e221ba94b4b905bd899d050a879ad` |

Both videos are 1320×2868; the nominal video rate is 60 frames/second. Files are in `/Users/alirahimlou/Downloads`. V1 is supplementary and contributes no new execution requirements here.

Sources: [V2 reading transcript](../video-transcripts/video-2.srt), [V3 reading transcript](../video-transcripts/video-3.srt), [transcription method](../video-transcripts/README.md), [frame/annotation manifest](source-evidence.json).

Regenerate unaltered frame evidence and the annotated gallery, entirely locally:

```sh
python3 docs/research/extract_source_frames.py
```

The extractor checks both source hashes before producing frames. `--source-root` supports another location containing the same MP4s. Outputs are excluded from Git under `runtime/research/video-evidence/`: individual PNGs, `extraction-manifest.json` with exact decoded source timestamps and frame hashes, and [annotated-examples.html](../../runtime/research/video-evidence/annotated-examples.html). The committed [extractor](extract_source_frames.py) and manifest reproduce the evidence from the original files. No media or incidental on-screen messages were uploaded. Annotations sit alongside unaltered frames; chart pixels were not redrawn or enhanced.

Times below are elapsed recording time, **not** chart candle timestamps or the phone clock. A requested integer second resolves to the first decoded frame at or after it; the extraction manifest records the exact source presentation timestamp. The underlying chart dates differ from the September 2026 phone-recording filenames and cannot be used as a September 17 trading record.

## Classification

- **Explicit:** spoken in a retained transcript or directly visible in a frame. An explicit example is not automatically a universal requirement.
- **Inferred:** a reasonable interpretation that the source does not fully define.
- **Engineering choice:** a numerical or operational rule introduced to make a system executable; it requires independent validation.
- **Absent:** the recordings provide no applicable rule. Any implementation belongs in the engineering category.

## Rule-to-source comparison

| Rule | Timestamped evidence | Classification and support | Baseline code and remaining gap |
| --- | --- | --- | --- |
| Traded market | V2 00:22.720–00:27.480; V3 Nasdaq chart at 01:03 | **Explicit:** Nasdaq context. | QQQ is an **engineering instrument choice**, not an identical instrument or feed to the displayed Nasdaq chart. Contract details are not confidently legible. |
| Previous-day areas | V2 00:01.360–00:05.560 | **Explicit:** mark previous-day high and low. | `prior_day_zones` uses the prior broker-calendar regular session. Video does not define cash versus full-session extrema. |
| Meaning of sweep | V2 00:06.560–00:22.720 | **Explicit:** price breaks above/below a pivotal area on a higher timeframe. | Previous-day branch detects boundary penetration by hourly high/low; it does not require closing back inside. Requiring a universal reclaim would exceed V2. Exact minimum excursion, duration and completed-bar requirement are absent. |
| Four-hour construction | V3 00:18.160–00:41.880; frames 00:20, 00:28, 00:34, 00:41 | **Explicit:** manually mark repeated touches **or breakthroughs** on four-hour view. Lines pass through several bodies/wicks, not just isolated extremes. | `zones` uses only confirmed local high/low extrema and requires two nonadjacent pivot observations. That approximation can exclude repeated crossings described in the source. |
| Width and minimum history | Same V3 passage | **Absent:** no percent width, volatility width, lookback or clustering formula. Visible line thickness is not a price tolerance. | `tolerance=0.001` gives 0.1% outer margins; extrema group within 0.2% of a group's first point. Total zone width includes the extrema spread, so it is not invariably 0.1% or 0.2%. Sixty calendar days of stock history and nonadjacent pivots are engineering choices. |
| Point-in-time availability | V3 00:39.480–00:57.440 says predetermined/manual areas | **Explicit** premarking; **inferred** requirement that the area must exist before the event. No swing confirmation lag is specified. | Waiting for the right-hand four-hour swing bar to close is an engineering mechanism to avoid hindsight. Its timing, strict inequalities and event anchors must be tested without future bars. |
| Session/time-zone alignment | V3 01:03 shows UTC/ETH on Nasdaq chart; 01:55 and 02:00 show UTC/Full trading hours on VIX | **Explicit example:** full-session chart settings are displayed. **Absent:** a universal alignment/session specification. | Regular-session-only bars anchored at 09:30 New York are engineering choices. Full four-hour buckets omit the 13:30–16:00 remainder; a standard day contributes only the 09:30–13:30 bucket. That differs materially from a full-session chart. |
| Hourly level event | V3 00:41.880–00:57.440; frames 00:45–00:53 | **Explicit:** move to one hour, wait for a break, trade a retest at a premarked area. | `pivot_event` recognizes completed-bar continuation and a failed-break/reclaim alternative. Exact body/wick conditions, whether intrabar entries are allowed, and permitted retest delay are absent. |
| Relationship of level variants | V2 prior-day passage; V3 repeated-four-hour passage | **Inferred:** related ways to locate the same workflow. | Treating them as alternatives is plausible; requiring both in sequence is not spoken. Neither formulation has comparative performance evidence. |
| Direction after a break | V3 01:01.520–01:25.440; frame 01:03 | **Explicit conditional example:** after breaking above a red Nasdaq line, consider a short if leaders sell from supply. | Direction cannot be mechanically equated with break direction. Code takes leader direction independently of `pivot_event` direction. Whether every such opposite-direction combination is valid remains unresolved. |
| Leader universe | V2 00:33.240–00:41.000; V3 01:08.560–01:15.720 and 02:18.200–02:27.600 | **Explicit:** Magnificent Seven; Nvidia and Apple emphasized in an example. | Seven distinct companies are a sensible interpretation. Mandatory AAPL+NVDA, index-cap weights and duplicate Alphabet votes are not stated. One name in the closing list remains unclear; subtitles are imperfect. |
| Required participation | V2 00:41.000–00:51.480 | **Explicit:** qualitative majority buying favors long, majority selling favors short. | Four of seven is an **inferred numerical interpretation**, assuming seven equal and binary votes. The video does not define denominator under neutral/unavailable evidence or require exactly the code's zone-reaction test. |
| Opposing evidence | V2 00:51.480–01:06.400 | **Explicit:** abstain when evidence is mixed across buying/selling or supply/demand. | Rejecting one opposing zone reaction is a conservative **engineering translation**. Video does not define whether one dissenting company defeats a strong majority. Neutral, mixed and missing evidence need separate states. |
| Leader zones | V3 01:13.600–01:25.440 and 01:30.960–01:43.680; frames 01:17, 01:22, 01:35, 02:23 | **Explicit:** examine supply/demand/pivotal areas and require rejection/selling in the short example. | Blue/orange areas coexist with daily/weekly/monthly lines. Their construction is not shown. Requiring each leader's own repeated four-hour extrema is an **engineering choice**, not an explicit source rule. |
| Leader timeframe and reaction | Same V3 sequence; clear NVIDIA header at 02:23 and Apple header at 01:35 | **Explicit example:** leader headers show `5` (five minutes). **Absent:** universal fifteen-minute leader rule. | Code uses fifteen-minute candle overlap, close outside zone, candle direction, and close beyond previous close. Those exact conditions are engineering choices. The visual five-minute example is a reason to test timeframe fidelity, not proof that five minutes improves returns. |
| Confirmation timing | V2 01:06.400–01:14.840; V3 01:19.200–01:25.440, 02:02.320–02:27.600 | **Explicit:** enter only with contemporaneously relevant confirmation. **Absent:** candle synchronization, elapsed-minute window, or acceptable confirmation age. | All votes from the latest closed fifteen-minute bar and immediate expiry at the next bar are engineering choices. Persistence could better represent a continuing reaction, but no specific lifetime is source-backed. |
| Reset and invalidation | No precise definition in either primary clip | **Absent:** maximum setup age, zone failure, opposite-close invalidation, or cross-session persistence. | A continuation invalidated by a close through the other zone side is an engineering choice. Persistent variants must bind each vote to one setup and specify zone failure, expiry and reset; stale votes from unrelated setups cannot be combined. |
| VIX direction and location | V2 01:22.200–01:36.440; V3 01:40.680–02:27.600 | **Explicit:** a short requires VIX buying from demand/pivotal area; reverse the relationship for a long. | Actual VIX is source-consistent; merely rising/falling away from an area is insufficient. An ETF substitute is not demonstrated. The inverse relationship is a required confirmation in the narration, not a guarantee of future Nasdaq returns. |
| VIX timeframe and area | V3 01:47.400–02:02.320; frames 01:50, 01:55, 02:00 | **Explicit example:** switch VIX from five to fifteen minutes for extra confirmation and inspect earlier pivots. | Fifteen minutes is shown; a universal two-repeated-extrema requirement with 0.1% margins is absent. Code applies `zones` to fifteen-minute VIX bars despite that helper's four-hour-oriented labels. |
| VIX persistence/freshness | Same VIX passage | **Absent:** quote freshness threshold, candle TTL, provider quota, synchronization tolerance. | Fresh actual-index observations, refusal of delayed/proxy data, provider cache and allowance limits are engineering data-integrity requirements. They are not new source trading signals. |
| Entry order and price | V3 00:48.560–00:57.440 and conditional short at 01:19.200–01:25.440 | **Explicit:** trade after location and confirmation. **Absent:** order type, market/limit price, latency or completed-bar fill assumption. | The analysis candle close is a reference, not a realizable earlier fill. Broker execution must occur after the decision is knowable, at executable prices with costs. |
| Stop, target, exits | Not specified anywhere in V2/V3 | **Absent.** | Stop beyond event/zone with $0.01 offset, next opposing level as target, target monitoring, one position, and closing near session end are engineering policies. V1's VWAP exit is not part of this primary workflow. |
| Position sizing and loss limits | Not specified anywhere in V2/V3 | **Absent.** | The owner's $25 purchase target is an input; it is not a $25 loss allowance or source sizing rule. Fractional QQQ long workflow, whole-share short restriction, buying-power checks and independent loss limits require separate execution/risk evidence. |

Code references: [strategy](../../pivot/strategy.py), [feed/session construction](../../pivot/feeds.py), [declared execution policy](../../pivot/policy.py), [sizing](../../pivot/sizing.py). The table describes the frozen baseline, not approval to change production.

## Annotated examples and accepted/rejected status

The [local gallery](../../runtime/research/video-evidence/annotated-examples.html) contains eight examples with separate **observed**, **interpretation**, and **not established** notes. Exact decoded timestamps are in the gallery and extraction manifest. These are source-evidence annotations, not synthetic replacements for source images.

| Example | Selected source time | What is accepted or withheld | Evidence limit |
| --- | --- | --- | --- |
| Repeated manual areas | V3 00:28.003333 | **Accepted as source location illustration:** multiple interactions with red lines. | Does not validate the code's width or swing-only construction. |
| Break/retest | V3 00:50.005000; compare 00:48 and 00:53 | **Accepted as event illustration:** the speaker identifies a break/retest context. | No complete accepted order can be labeled from this alone. |
| Break above, possible short | V3 01:03.005000 | **Accepted as a candidate location**, conditional on leaders/VIX. | A green Nasdaq candle is not independent long permission. |
| Apple supply reaction | V3 01:35.008333 | **Accepted as one visible leader example:** red move from blue upper area. | Exact zone definition and all other votes are unavailable. |
| Nvidia leader view | V3 02:23.013333 | **Accepted as evidence of five-minute leader charts.** | Does not determine a universal minimum count or lifetime. |
| VIX before switch | V3 01:50.010000 | **Incomplete short confirmation:** visible VIX candles are falling. | This is not an observed broker or app rejection. |
| VIX at fifteen minutes | V3 01:55.010000 | **Withhold an immediate short under the spoken rule** until buying from the area appears. This is researcher inference. | Exact pivotal boundary and eventual current reaction are missing. |
| VIX historical pivot | V3 02:00.011667 | **Accepted as historical location analogy**, not proof of a current turn. | Panning to old candles does not establish synchronized current confirmation. |

There is **no fully accepted, synchronized setup with a known entry, protective order, exit and result** in the supplied clips. Fabricating such a label would conceal missing evidence. Separately generated replay examples must label their input provenance and which engine accepted/rejected them. A scripted fixture can verify software behavior; it cannot become source or market-performance evidence.

## Ambiguities and apparent tensions

1. **Majority versus mixed evidence.** V2 describes a majority, then says to avoid mixed signals. These overlap if four sell and three buy. The clips do not resolve whether unanimity among active reactions, majority plus neutral remainder, or discretion is intended. Four agree/zero oppose is one transparent translation.
2. **Break/retest versus reversal.** V3 describes trading retests, then entertains a short after an upward break. A source-faithful research family must preserve this ambiguity and inspect leader/VIX direction; a single continuation rule cannot be attributed universally.
3. **Four-hour Nasdaq lines versus leader areas.** The explicit four-hour construction passage is on the Nasdaq chart. Applying the identical algorithm to every leader is not demonstrated. The later five-minute leader views carry several kinds of reference levels whose role is not fully explained.
4. **VIX anticipation versus confirmation.** The speaker anticipates a pivot but repeatedly conditions the short on VIX actually starting to buy. Anticipation alone is insufficient evidence of a completed reaction. The clips omit how much movement or which closed candle suffices.
5. **Session settings.** Nasdaq ETH and VIX Full trading hours are visible, but the app uses regular-session equity aggregation. The equity/futures/VIX sessions are different; neither identical bucket alignment nor a universal session filter is supported.
6. **Discretion versus an algorithm.** Manual lines, qualitative pressure and edited/filmed charts leave many numerical choices open. Caption errors and the unclear closing list do not justify silently choosing new requirements.

## Research implications

Keep a frozen baseline and two clearly named research categories:

- **Source-aligned approximations:** alternative representations of manual areas, repeated crossings, five-minute leader behavior, and context that remains active while an area still holds. Every numerical boundary remains declared; none is certified as the creator's exact system.
- **Proposed enhancements:** fewer required leaders, tolerated dissent, altered entries/exits or timeouts selected to improve a measurable objective. These require prespecified experiments and unseen-data evidence independently of the source.

Before persistent votes can be compared, define a setup identity from instrument, event, zone and event time; vote start at an observable closed reaction; expiry; opposite-side invalidation; reset on setup change and session change; and whether a new same-direction reaction refreshes age. These requirements prevent stale or unrelated votes from accumulating. Their values are research choices, not newly discovered video rules.

The current evidence justifies measuring where candidates disappear: no pre-existing area; no Nasdaq event; no individual zone reaction; too few current votes; conflicting evidence; stale/absent VIX; absent stop/target geometry; non-executable account/order constraints. It does **not** justify treating more surviving candidates as profitable.

## What would resolve the source-fidelity gaps

Independent research can continue now. A stronger fidelity claim would require:

1. Exported chart examples with instrument, feed, session, timezone and bar timestamps, showing the premarked areas **before** outcomes.
2. The construction method and intended timeframe for leaders' supply/demand areas; how bodies, wicks and crossings set boundaries.
3. Several complete accepted **and deliberately skipped** setups, including all seven companies and VIX at the same decision time, with explanations of neutral and opposing evidence.
4. Explicit rules or labeled examples for vote persistence, timing order, invalidation and reset.
5. Entry/stop/target/exit and position-risk definitions, followed by an execution record if execution fidelity is claimed.

Until those exist, the appropriate conclusion is **a documented mechanical interpretation with unresolved fidelity, and no profitability conclusion from these sources**. Performance and execution commissioning must be reported separately.
