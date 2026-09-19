# 4H Range Reversal: source review and executable observation rules

Reviewed September 19, 2026. **This document records the v3.3 observation baseline. The subsequent executable baseline and resolved application choices are documented in [v4.0.0](../production-v4.0.0.md); its original source ambiguities remain disclosed.** Source: the owner's 7:14.857 screen recording ending `07-59-36_1.MP4`. The visible repost is labeled Hous Bennett; that label does not establish the original narrator's identity. The displayed title is “The Best 5 Minute Scalping Strategy That Works Daily (Tested).”

The complete available audio was transcribed locally with whisper.cpp base.en, followed by transcript review, 29 overview images and selected exact-timestamp chart frames. This is machine-assisted transcription and chart inspection, not a claim of certified verbatim accuracy or continuous manual playback. The recording starts and ends mid-sentence. Full transcript, raw ASR, original file hash and inspected images stay in the owner's private research directory; they are not published with this public repository.

## Conclusion

This is a **separate strategy family**, named **4H Range Reversal**. It shares level-based confirmation with Socrates, but changes the level construction, timing, direction, confirmations and target. Combining its triggers with Socrates' leader/VIX gates would create a third, unsupported hybrid.

| Component | Current Socrates interpretation | New recording |
| --- | --- | --- |
| Market examples | Nasdaq; app translates to QQQ | Bitcoin demonstrated; forex and gold mentioned |
| Reference levels | Previous-day extrema or repeated four-hour areas | High and low of the day's first completed four-hour candle |
| Entry timing | Hourly sweep or break/later retest | Five-minute close outside, then later close inside |
| Trade direction | Leader direction with opposite VIX reaction | Fade the failed breakout: above then inside = short; below then inside = long |
| Confirmations | Five leaders agreeing, at most one opposing, plus actual VIX | Neither leader quorum nor VIX is required in this recording |
| Basic stop | Declared app geometry around the event/area | Breakout candle's extreme; later examples introduce discretion |
| Target | Nearest eligible opposing level | Twice initial stop distance, or 2R |
| More than one trade | Existing app owns one active position | Repeated new excursions are demonstrated; concurrent positions are not specified |

The five/one Socrates quorum remains the owner's selected interpretation, not an established formula supplied by either creator. See [separately sourced Socrates rules](current-method-specifications.md).

## Timestamped source findings

| Recording time | What is actually supplied | Implementation status |
| --- | --- | --- |
| 00:10–00:18 | Narrator says the method was tested on Bitcoin, forex and gold | A claim, without a test dataset or ledger. Bitcoin is the first observation market. |
| 00:43–00:59 | First four-hour candle defines the day's high/low range | Implemented with the provisional anchor described below. |
| 01:25–01:37 | Trade the return into the range, rather than chase the initial break | Implemented as a two-stage state machine. |
| 01:37–02:08 | Set a four-hour chart to New York display time; mark the first candle; wait for its full close | Source does not expose a legible candle-open/session anchor. A chart timezone alone cannot recover it. |
| 02:12–02:26 | Five-minute candle must close outside; a wick alone is insufficient | Strict outside close. Opening price need not also be outside. |
| 02:26–02:43 | A later five-minute candle closes back inside, during the same day | Strict inside close, distinct later bar, current-day reset. |
| 02:44–02:55 | Upper failed break proposes short; lower failed break proposes long | Implemented as observations, without sending orders. |
| 02:55–03:03 | Stop goes at the breakout candle extreme | First outside-close candle's high/low is the declared provisional choice. |
| 03:03–03:14 | Target is twice stop distance | Implemented reference geometry: long target = entry + 2 × (entry − stop); short reverses signs. |
| 03:17–03:55 | Bitcoin long example, confirmation-close entry, claimed 2R win | Illustrated trade, not an independently verified fill. Narration also says “breakout move,” which broadens the possible stop interpretation. |
| 03:57–04:26 | Later short example, claimed 2R win | Exact frames near 04:14–04:16 show the stop moved toward the first outside-close candle's high, rather than the full excursion's highest point. |
| 04:28–04:48 | Long example loses approximately 1R | Some drawings lie near candle bodies despite thin wicks; exact stop tick cannot be recovered confidently. |
| 04:58–05:13 | Narrator claims 40% is break-even at 2:1, and a 72% average win rate | The first claim is mathematically wrong before costs; the second is unsupported by records. |
| 05:16–05:59 | After a large excursion, tighten the stop at nearer support/resistance or an order block | No numerical threshold or reproducible level selection is given. Not implemented. |
| 05:59–06:10 | Several fresh outside/inside cycles may be traded in a day | Record each distinct excursion; this does not establish pyramiding or simultaneous ownership. |
| 06:10–06:23 | Favor longs in a daily/weekly uptrend and shorts in a downtrend | Optional preference without an objective trend formula. Displayed as unresolved, not invented as a gate. |
| 06:23–06:44 | Volume/structure/context can improve selection | Optional, undefined; no new scoring model added. |
| 06:54–07:13 | Log and test 20–30 examples | Useful observational exercise, not enough by itself to establish profitable deployment. |

## Exact current observation contract

The analyzer is `pivot/range_reversal.py`, rule version `range-reversal-observation-v1`. Every result and candidate has `can_enter: false`. It has no broker dependency.

1. **Data:** genuine native five-minute BTC/USD spot candles from Alpaca's US crypto data endpoint. No QQQ resampling, VIX substitute or historical larger-candle splitting. Source identity, receipt time and resolution are explicit. Alpaca spot prices are not Bullpen perpetual execution prices.
2. **Day:** New York calendar date. The provisional first range starts at New York midnight and spans four elapsed hours, giving 48 candles. On DST-change days it ends at 05:00 or 03:00 local clock time. This is a testable app convention, not a recovered creator rule. Selecting New York display time is insufficient to prove a chart's bar alignment; see [TradingView's time documentation](https://www.tradingview.com/pine-script-docs/concepts/time/).
3. **Coverage:** all completed five-minute bars from day start through the most recently expected closed bar must exist. Only the newest bar receives 90 seconds of publication allowance. Duplicates, nonascending/off-grid timestamps, invalid OHLC, missing earlier bars and stale/future receipts suppress observations. Forming candles are ignored.
4. **Range:** maximum high and minimum low of those first 48 closed bars. No setup before the range closes. Flat ranges are unavailable.
5. **Excursion:** close strictly above high or below low starts a pending event. Boundary equality and wick-only breaks do not start one. Store the first outside candle and a stable event identifier.
6. **Confirmation:** a later close strictly between low and high confirms the reversal. Use its close only as an entry reference, never as a claimed executable fill. Stop is the first outside candle's relevant extreme; target is 2R. Invalid price geometry is rejected.
7. **Continuation:** later extremes in the same excursion do not move the declared stop. A close beyond the opposite side without an intervening inside close invalidates the old excursion and starts another; this is explicitly an app interpretation. New completed excursions can produce more observations. Midnight resets pending events.
8. **Current versus historical:** only a confirmation on the latest admitted completed candle is current. Older confirmations remain history; repeated polling does not produce another event or keep an old entry ready. Receipt refresh cannot make missing or old candles current.
9. **Isolation:** a separate observer fetches Bitcoin once per minute. Failure cannot alter Socrates' required data, account permission, selected setup or execution worker. Observer startup/snapshot failures return an unavailable observer panel. The live toggle still controls Socrates only.
10. **Evidence:** private local SQLite history retains unique closed-bar sets and provider corrections with reference analysis. Repeated unchanged candles are deduplicated. Private regular-file checks and SQLite page bounds cap the database at 64 MiB; reaching the cap reports archive attention rather than silently discarding old evidence. This archive contains observations, not simulated or actual trade returns.

The endpoint and native interval are documented in [Alpaca's crypto bars reference](https://docs.alpaca.markets/us/v1.1/reference/cryptobars-1). An actual read-only collection was successfully checked on September 19: 100 completed five-minute bars, a complete 48-bar range, and two earlier reversal observations under this declared interpretation. That is data/analyzer verification, not a profitability result.

## Performance claims and evaluation boundary

For exact +2R winners and −1R losers before costs, expected return is `3p − 1` R per trade, with break-even at `p = 1/3`. At 40%, the idealized expectation is +0.2R, not break-even. Commissions, spread, slippage, funding, late entries and partial exits change the result. A 72% win rate is not accepted as evidence merely because the narrator says it.

No backtest or actual fill performance is claimed for this addition. Next evaluation must freeze the anchor, stop variant and trading venue first; label already-inspected examples as known; collect an untouched forward period; use observed executable prices after confirmation, with costs and conservative same-bar stop/target handling. Compare the basic first-candle stop and any separately defined alternative independently. Do not tune the reference rules until a historical equity curve looks attractive.

## Before live or simultaneous trading

- Confirm the creator's instrument/feed/session candle anchor, including DST behavior, or explicitly select a documented adaptation.
- Resolve first breakout candle versus breakout-move stop, and either define or omit discretionary tightening and trend preference.
- Specify entry latency/order type, sizing, overnight positions, conflicting signals and stop/target handling when the market gaps.
- For crypto, select spot versus perpetuals and verify venue prices, minimum order sizes, fees, leverage/funding and long/short support. A stock broker permission is not authorization for another venue.
- Add a portfolio coordinator that assigns position/order ownership to strategies, reserves shared buying power, and reconciles opposing or overlapping trades. Existing Socrates whole-position management and one-active-trade guards must not be removed to manufacture concurrency.
- Commission the complete isolated broker workflow, including partial fills, protective-order failure, restart reconciliation, and costs, before enabling the new family.

The selector therefore offers **Socrates**, **4H Range Reversal**, and **All strategies** as analysis views. It does not select order routing or grant simultaneous trading. This boundary is visible in the interface.
