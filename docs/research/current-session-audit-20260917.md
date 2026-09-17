# Current-session audit: September 17 through 12:15 p.m. Eastern

## Finding

**The stock data was complete, but the leader-zone interpretation blocked every entry in this morning's reconstruction.** The app found a previous-day Nasdaq sweep at 10:30. It then found zero qualifying reactions among the seven companies, using either the existing fifteen-minute candles or genuinely five-minute candles. Changing only the candle interval would not have produced a qualifying leader group in this sample.

This is a diagnosis of the stated rules, not evidence that no discretionary trade existed or that those rules exactly reproduce the videos. The source follow-up found material differences in how leader locations and ongoing reactions are interpreted. Full source fidelity remains unresolved.

## Method and actual data

The [fixed audit plan](today-audit-plan.json) was committed as `5fd20e920347c738a0d54f0f9400a4a5b3e5d256` before collecting current-day results. The pure analyzer was loaded from that exact revision, isolating the reconstruction from subsequent target fixes. Its SHA-256 is `373f0e70feebc355dbbdad2b14814e799dcaf1969db27ef0f50ee3f901c0f1a8`.

Read-only Alpaca IEX collection supplied current-day five- and fifteen-minute equity bars for QQQ and all seven leaders through the fixed 12:15 cutoff. The existing immutable history supplied the preceding rolling sixty-calendar-day context. All required historical fifteen-minute bars and today's five-/fifteen-minute bars were present for all eight symbols. No candle was invented to fill a gap. There were no broker-order or additional VIX requests.

The script reconstructed 33 five-minute checkpoints and separately labeled the 11 quarter-hour checkpoints. It used the same pre-existing four-hour zone and candle-reaction calculations in both frame comparisons. Five-minute candles retained their real interval and were not mislabeled as fifteen-minute input. This is subsequently fetched split-adjusted history with assumed observation clocks; it does not recreate every actual arrival delay or the consolidated market.

A supplemental offline consistency check found that all **88 native fifteen-minute candles matched aggregates of 264 five-minute candles exactly**, including open, high, low, close and volume. No future, partial, outside-session or misaligned candles were present. See the [consistency check](../../runtime/research/today-20260917/supplemental-aggregation-check.json). This verifies internal candle consistency, not independent accuracy against all exchanges.

## Why the message changed

| Completed-candle checkpoints | Result |
| --- | --- |
| Before 10:30 | The first current-session full hourly candle was not yet complete. |
| 10:30 through 11:25 | The 10:30 previous-day sweep qualified; zero leaders confirmed under either tested frame. |
| 11:30 through 12:15 | The latest completed hourly candle no longer met a Nasdaq event condition; the message therefore moved back to that earlier gate. |

This accounts for the observed change from waiting for the Magnificent Seven to waiting for a Nasdaq event without any code deployment.

At the 11:15 checkpoint, the distance from each leader's close to its nearest generated area was:

| Company | Eligible areas | Distance as percentage of its price |
| --- | ---: | ---: |
| Apple | 2 | 2.83% |
| Microsoft | 0 | No eligible area |
| Nvidia | 1 | 4.43% |
| Amazon | 1 | 1.94% |
| Meta | 2 | 10.62% |
| Alphabet | 1 | 2.36% |
| Tesla | 1 | 3.51% |

These are descriptions of the app's generated areas, not recommended trading levels. The current requirement for four current-candle reactions cannot be satisfied by companies that have no relevant nearby area. In the earlier nine-session sample, no more than two companies even touched a generated area at any of the 70 Nasdaq-event checkpoints.

## What is correct, fixed, or still unresolved

- **Data coverage:** complete for the examined stock frames. The public production snapshot separately reported current actual VIX with no data errors. The full current-day VIX price series was not available in this isolated replay, so its directional pattern and historical entry-quote availability remain unknown. The failed leader gate alone was sufficient to prohibit entry under the tested rules.
- **Logical reachability:** offline fixtures reach complete long and short signal states; there is no universal analyzer deadlock. A $5 fractional QQQ long is representable. The current account and whole-share short requirement prohibit the corresponding $5 short.
- **Confirmed target bug:** the prior-day-sweep branch could confirm but fail for lack of a target because the target calculation ignored prior-day levels. The corrected release considers all already validated premarked four-hour and prior-day levels, choosing the nearest in the trade direction. This is an internal exit-policy correction; the videos do not supply exit rules. It did not cause this morning's inactivity because the leader check failed earlier.
- **Source mismatch:** the [new timestamped primary-video review](primary-source-followup-20260917.md) confirms five-minute leader charts and ongoing rejection after leaving a zone. Four-hour extrema on every leader, a newest-candle touch, and a precisely four-of-seven/no-opposition vote are app interpretations. Replacing these with arbitrary green/red candle votes would create another unsupported interpretation.
- **Execution proof:** software tests and a passing container build do not establish a successful actual buy/protect/sell lifecycle or profitability. No live order was used as a test.

## Release and next research action

Release the reviewed diagnostics, remote read-only decision history, rejection/protection fixes and target consistency correction through the normal Live-Off/flat-account updater. Keep the remaining signal rules explicitly identified as interpretations. The $5 target is owner-saved; owner reactivation is required after installation. Do not advertise this as exact video replication or expect these fixes alone to create trades.

The missing input is the creator's method or indicator for the blue/orange leader supply-and-demand areas, plus how long a rejection remains valid. A request for that source has been sent. Until it is available, preserve independent source-fidelity research and production evidence rather than choosing an area formula after seeing which one would trade this morning. [The continuing review plan](../production-review-plan.md) separates source fidelity, data health, order reconciliation and after-cost results.

## Reproduction and artifacts

Run the read-only collector/reconstruction with a new output directory:

```sh
.venv/bin/python -m research.today_audit \
  --base-dataset runtime/research/dataset-v1.json.gz \
  --env-file /path/to/private/Alpaca.env \
  --plan-commit 5fd20e920347c738a0d54f0f9400a4a5b3e5d256 \
  --out runtime/research/today-20260917-repeat
```

Repeated provider collection may contain corrections; retain the original local dataset for exact evidence. Dataset SHA-256: `91c64f089c920b40ce5491597bf7b3c3c6d683b4220cb1567dbde697ccd46c74`. Results SHA-256: `e476f6091397cd5754286ce80a4082cb39bffbdfa252a9a182732f94ce084007`.

The full original [results](../../runtime/research/today-20260917/results.json), [manifest](../../runtime/research/today-20260917/manifest.json), and [chronological table](../../runtime/research/today-20260917/REPORT.md) remain private local artifacts. A historical wording in that original table's introduction calls overall eligibility unknown; more precisely, the leader failure proves no entry under the tested rules, while the unobserved VIX component remains unknown. No synthetic VIX evidence was substituted.
