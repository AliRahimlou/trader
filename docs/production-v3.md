# Pivot 3.0 — two independent video entry methods

## Scope and source evidence

This release implements the owner's request to correct the audited strategy differences and publish the tested app on AllSpark. The primary recordings remain `17-25-23_1` and `18-11-24_1`. The other recording supplies no tape, VWAP or structure-shift requirement.

The [September 17 audit](research/video-alignment-audit-20260917-afternoon.md) records the previous release's behavior. It remains historical evidence; passing old tests does not validate the new rules. Historical replay tools use an explicitly frozen v1 analyzer. Production uses v2 only, with a new owner-reviewed execution policy.

## Choices fixed before evaluating outcomes

| Component | Version 3 behavior | Evidence or app choice |
| --- | --- | --- |
| Instrument / session | QQQ, completed regular-session hourly candles; four-hour areas and actual previous trading-day boundaries | QQQ and session/close conventions are app choices. The videos use Nasdaq charts. |
| Four-hour method | Premarked repeated interactions, then an hourly break/retest | V3 00:18–00:58. A later retest cannot reset the original event identity or lifetime. |
| Previous-day method | A boundary sweep evaluated independently of the four-hour method | V2 00:01–00:24. No mandatory close back inside is added. |
| Area approximation | Fixed ±0.1% bands around historical high/low anchors; two nonadjacent touches or crossings; deterministic deduplication; no later price moves an established band | The creator's exact blue/orange area formula is unavailable. This is a declared approximation, not a verified reproduction. |
| Leaders | Genuine five-minute candles and five-minute historical interaction areas; four of seven companies agree, none oppose | Five-minute Apple/Nvidia charts are visible in V3. Area construction and numerical participation are app choices. |
| Reaction context | At most 15 minutes of same-session follow-through; missing bars or opposite boundary closes invalidate it | Ongoing rejection is visible/described. The lifetime and invalidation geometry are app choices. |
| Opportunity lifetime | 180 minutes from original break/sweep, limited to its session; one entry per opportunity regardless of later retests or changed direction | Explicit operational choice. It replaces unlimited old breaks and per-retest duplicate identities. |
| VIX | Actual index, completed 15-minute candles, opposite zone reaction; fresh quote checked before entry | Broad confirmation follows V2/V3. Existing numerical VIX area rule, free provider, freshness and durable quota remain unchanged. |
| Size / exits | Saved purchase target; planned amount 99–100% of it. One position, broker stop after fill, target at next opposing premarked level; entry cutoff 10 minutes and closing starts 5 minutes before close | Position sizing and exits are absent from these clips. Sell proceeds depend on price and shares held. |

These choices were fixed before new outcome evaluation. They are not parameters selected to produce trades in today's market. A signal reaching the end of the analyzer is still subject to separate broker admission. Neither a quiet day nor a new trade proves correct source interpretation or profitability.

## Data and budget

The existing 15-minute stock context still supplies the QQQ hourly, four-hour and daily bars. Seven leaders additionally receive **native Alpaca 5Min** history over the last seven actual exchange sessions. The two fetches share atomic cache publication: failure or incomplete data cannot silently promote one new frame alongside unverified others. Incremental refresh rereads overlapping sessions for corrections; periodic refresh reconciles history. The dashboard identifies missing frames and the actual feed.

Alpaca documents [native 5Min aggregation](https://docs.alpaca.markets/us/reference/stockbarsingle-1). The free IEX feed represents one exchange, not the consolidated market; see its [market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq). No paid feed or new hosting service is added. Actual VIX keeps its existing free provider and request allowance.

Alpaca supports fractional DAY orders but not fractional short sales, per its [fractional trading documentation](https://docs.alpaca.markets/us/docs/fractional-trading). Therefore a $5 target cannot execute the videos' short examples using QQQ. The app reports those broker constraints; it does not inflate the target or substitute another instrument.

## Observability and release controls

The homepage shows both methods and their separate blockers, current leader direction, positions/orders and the installed version. Decision history records genuine five-minute inputs, each method's event identity/lifetime, confirmation evidence and first failed condition. Five-minute checkpoints preserve missing or stalled input evidence. The separate execution journal records admission failures, attempts and recovery. Historical records never authorize an order.

The updater obtains tested releases from GitHub without requiring the laptop or local Wi-Fi. It pauses new entries during a broker-verified flat-account switch and preserves durable account history, settings and saved permission. Unknown exposure or failed verification retains the entry hold or rolls back under the existing protocol.

This release changes strategy rules, so it has a new execution policy identifier. Saved approval of the previous strategy does not approve the replacement. The app displays **Review updated live rules** until the owner reviews the concrete policy with the top-right switch. Installation does not turn on Live money, alter $5, or place a commissioning trade.

## Verification and practical limits

Release verification covers native data fetching/coverage, no-lookahead areas, both independent methods, event expiry and deduplication, input freshness, analyzer-to-simulated-broker execution, order/stop recovery, durable histories, UI models and the actual Linux container build. Production verification checks the installed revision, authenticated page, saved amount, worker freshness, data frames and logging.

Synthetic broker tests establish software behavior under their fixtures. They do not prove actual live fills, creator-identical signals, unseen data-provider behavior or profitable returns. The retained historical evidence establishes no profitable edge. Subsequent evaluation must report missing data, skipped entries and costs instead of treating every wait as a defect or retuning rules after each loss.
