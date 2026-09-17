# Video alignment and no-trades audit — September 17, afternoon

## Conclusion

The hosted app is running its current mechanical rules, but those rules are **not an exact implementation of the supplied video**. Healthy data, passing execution tests and a successful deployment do not resolve the strategy differences. No live rule, permission, purchase amount or broker order was changed by this audit.

The source is `ScreenRecording_09-14-2026 18-11-24_1.MP4` (V3). The retained machine-assisted transcript, relevant original frames, prior source map and current code were reviewed together. This is not a new full audio transcription. The user has no additional tutorial explaining the leader-area construction.

## What the hosted records establish

Installed version: **2.2.1**, revision `86bf7c3e5b818dcf9de03d94400c26b145bbe7b8`. The snapshot showed current stock and actual-VIX data, Live On, a saved $5 purchase target, and no active managed trade or open positions/orders.

| Evidence | Available coverage (Eastern) | Result |
| --- | --- | --- |
| Saved strategy history | 12:42:32–15:38:03 | 31 distinct evidence rows, representing 176 repeated observations. Every first failure was `Nasdaq level event`; zero fully qualified signals. |
| Saved execution history | 15:02:07–15:39:00 | 443 checks: 85 blocked by Live Off and 358 waiting for the Nasdaq event. Zero recorded submission attempts. |
| Data in saved strategy rows | Same afternoon coverage | All rows reported current stocks/VIX and data readiness. Zero qualifying leader reactions; no leader's newest candle touched a generated leader area. |
| Saved permission events | Available events for the day | Four Off intervals totaled 31 minutes 22.8 seconds. These are permission durations, not measured service downtime. |

Both history endpoints returned no next-page cursor. These are the available recorded windows, not a complete full-day brokerage fill audit. Repeated checks are not independent opportunities. The historical `order_authorized=false` flag always prevents records being used as current order authority; it is not evidence that broker admission was or was not evaluated.

The latest completed QQQ hour, ending **15:30**, ranged from **715.93 to 716.93** and closed at **716.54**. It stayed entirely inside the app's generated **714.93435–717.71700** area, as did the previous hour. This explains `AT_LEVEL` and the absent coded break/retest. It does not prove that the creator would use the same area or abstain.

The earlier [morning reconstruction](current-session-audit-20260917.md) separately found a 10:30 previous-day sweep with zero leader confirmations; at 11:30 the next hourly candle no longer satisfied an event. That study used subsequently collected history, not retained live execution logs. Using genuine five-minute leaders with the same generated areas still produced no confirmed leader group in that morning sample.

## Video versus deployed rules

| Component | Video evidence | Deployed behavior | Assessment |
| --- | --- | --- | --- |
| Nasdaq levels | **00:18–00:42:** manually marks repeated touches or crossings. | `zones()` accepts repeated confirmed four-hour swing extrema. Repeated crossings without extrema can produce no area. | Narrower area construction than the described method. Width and clustering are still unspecified by the clip. |
| Break/retest timing | **00:42–00:57:** uses a one-hour view and trades a break/retest. | Requires specific completed-hour close/body geometry, with the newest completed hour supplying the event. A prior-day sweep disappears as an active event when the next hour is not another sweep. | Added numerical/timing choices. Neither universal close-only nor universal intrabar entry is established by the recording. |
| Leader timeframe | Apple **01:35.008333**, Nvidia **02:23.013333** show five-minute chart headers. | Fifteen-minute leader reactions. | Directly observed mismatch. |
| Leader rejection | **01:13.6–01:25.44:** rejection and subsequent selling are discussed; Nvidia is already below supply in retained frames. | Latest candle must overlap an algorithmic four-hour area, close outside it with matching body direction, and exceed the previous close. Default confirmation lasts one candle. | The mandatory current touch and immediate expiry are not stated in the clip. |
| Leader areas/participation | **01:08–01:43:** supply/demand or pivotal locations, especially Nvidia and Apple, and directional participation. | The same four-hour extrema algorithm is applied to all seven; four must confirm with zero opposition. | Exact leader area formula, vote threshold and opposing-vote rule remain app choices. |
| Actual VIX | **01:43–02:27:** opposite VIX reaction near an area; fifteen-minute view shown as extra confirmation. | Actual VIX data and an opposite fifteen-minute zone-reaction check. | Broad structure matches; numerical area/reaction formula remains an interpretation. Today's first signal gate prevented the VIX gate from being reached. |

The app's first-failed-gate message returns before later confirmation checks. It must not be read as “all other requirements are ready.” The two source entry variants also share a fallback priority: a four-hour event is selected before a prior-day sweep rather than both being independently attributed. Repeated retest candles receive different event identities even when they refer to the same originating break; current deduplication is per event candle and direction.

## Reproducible validation

`research/tests/test_video_rule_boundaries.py` adds five synthetic characterizations of the current event lifetime, crossing-only level exclusion, branch priority, repeated-retest identity and old originating breaks. A complete-history fixture accepts a retest 172 hours after its originating break because no maximum break age or session reset is defined. These tests demonstrate the implemented boundaries; they do not endorse them as source rules, validate profitability, or reproduce a known missed live trade.

**Validation:** all 42 tests in the focused boundary, strategy, current-rule audit and analyzer-to-simulated-broker suites passed. No new universal event-detector deadlock was found. The important result is a documented mismatch between the implemented conditions and the evidence in the recording, rather than a claim that software test success establishes source fidelity.

The private [saved-history report](../../runtime/research/no-trades-audit-20260917/REPORT.md), [summary with input hashes](../../runtime/research/no-trades-audit-20260917/summary.json), and [offline audit script](../../runtime/research/no-trades-audit-20260917/audit_saved_histories.py) reproduce the recorded counts without network access or credentials.

Source evidence: [timestamped rule map](source-rule-map.md), [leader/context follow-up](primary-source-followup-20260917.md), and [V3 reading transcript](../video-transcripts/video-3.srt). A bounded check of the creator's [published supply/demand overview](https://socratesinvestments.com/2024/10/28/analysis-of-nasdaq-gold-using-supply-and-demand-levels-october-28-2024/) did not supply the missing blue/orange leader-area construction. That separate article is not imported into the video strategy.

## What remains to implement and validate

The unambiguous timeframe correction is five-minute leader input. It must use genuine five-minute data throughout fetching, freshness checks, diagnostics and replay. The morning comparison shows why that correction alone cannot be represented as a no-trades fix.

The larger work is a declared, versioned interpretation of repeated-interaction areas and ongoing rejection context: when an area is established, when an event begins, which evidence remains relevant, and what invalidates it. Both entry variants should be measured independently and execution deduplicated by the underlying opportunity. Without the missing source formula, a new numerical area or lifetime rule must be described as an app choice and evaluated on untouched data; it cannot be certified as the creator's exact system.

This audit establishes an operational app and documented strategy discrepancies. It does not establish full video fidelity, a missed profitable trade, a live fill lifecycle, or a profitable edge.
