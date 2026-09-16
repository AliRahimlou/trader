# Authoritative video rulebook — September 16, 2026

## Scope and conclusion

The owner's final clarification makes **17:25 (V2) and 18:11 (V3) primary**. The 12:45 recording (V1) is another trader's related approach and is supplementary only. This supersedes the earlier two-independent-strategies assumption and the initial September 16 review.

V2/V3 support **one location-first Nasdaq workflow**: premarked areas → level event → technology-stock confirmation → VIX-at-zone confirmation. They present previous-day extrema and repeated four-hour levels as related ways to locate a trade. They do not explicitly state that both location variants must happen in sequence. Neither requires V1's 30-minute pullback, 5-minute structure shift, tape absorption/exhaustion, or VWAP exit.

The code can implement and test a transparent mechanical interpretation. It cannot truthfully claim exact equivalence to these discretionary demonstrations: numerical definitions and trade management are omitted. Owner-controlled execution is now implemented; see [execution behavior](live-execution.md). Actual VIX access still blocks entries.

## Review method

Full audio was re-extracted from the original MP4 files and transcribed locally with whisper.cpp base.en, beam size 8. Raw ASR and lightly corrected, timestamped reading copies are retained in [video-transcripts](video-transcripts/README.md). Sequential chart/caption frames were inspected across both primary clips. V3's retest passage was inspected at one-second intervals. Ambiguous words are marked rather than silently treated as facts. No media was uploaded. A failed larger-model download was not used.

Source file SHA-256 hashes:

- V2: `e91af8d2ef2a6b6570da05f146cc105ba650379771706368244cec79613a17f7`
- V3: `b6d885efc6a93e90c37012a5cd8f1d854e2e221ba94b4b905bd899d050a879ad`

## Rule map

| Stage | Evidence | What the source supports | What must not be silently invented |
| --- | --- | --- | --- |
| Instrument | V2 00:22–00:27; V3 chart | The context is Nasdaq. | Scanning unrelated cheap stocks as independent entry candidates; treating QQQ or individual stocks as identical to the chart instrument. |
| Location variant A | V2 00:01–00:22 | Mark previous-day high/low; observe a break above/below a pivotal area, called a sweep. | Requiring a close back inside, an FVG, or the old daily-sweep code. Those are not stated here. |
| Location variant B | V3 00:18–00:42 | Manually mark four-hour areas where price touched or broke through repeatedly. | Claiming the clip specifies candle bodies, exact zone width, swing-strength threshold or clustering algorithm. |
| Event | V3 00:42–00:58 | Move to one hour; wait for a break and retest at a predetermined area. | A one-minute substitute or a fixed next-bar retest deadline presented as a video rule. |
| Direction | V3 01:01–01:25 | A break above is followed by a possible short example, conditioned on stock selling at supply. | Automatically buying every upward break or assuming every example is trend continuation. |
| Leaders | V2 00:34–01:14; V3 01:08–01:43 | Magnificent Seven direction matters; mixed evidence means no trade. Zone rejection matters; Nvidia/Apple are emphasized in the example. | Counting GOOG and GOOGL as separate companies; weighted Nasdaq breadth as a source requirement; an exact 4-of-7 or mandatory AAPL+NVDA threshold presented as spoken. |
| VIX | V2 01:14–01:36; V3 01:43–02:27 | For a Nasdaq short, VIX should buy from demand/pivotal area; opposite for a long. | VIXY/VXX/UVXY/SVIX substitution, delayed data treated as live, or requiring a universally fixed 15-minute timeframe because the example switches to it. |
| Stop / exit | Not specified in V2/V3 | Must be defined for a complete executable system. | Importing V1's VWAP target or the old profit lock/stop policy while claiming video fidelity. |
| Sizing | Owner's dollar-box request, not a video rule | $25 is a target around $25; $50 around $50. Exit the held quantity. | Calling $0.46 the purchase amount, or inheriting the old 70%-of-equity floor and fixed share cap. |

## Implemented interpretation, explicitly provisional

The isolated primary analyzer uses closed, regular-session candles; repeated confirmed four-hour extrema form zones with a 0.1% band and at least two nonadjacent touches. Four-hour bars are anchored to 09:30 ET and incomplete buckets are excluded. These are implementation choices, not quoted instructions.

The prototype recognizes a completed one-hour break/retest, including retests after intervening candles and a previous-day boundary break. It also recognizes a failed-break reaction at a four-hour level. A continuation is invalidated by an intervening close through the opposite side of the zone; this too is an implementation choice. Direction comes from leader reactions rather than the sign of the Nasdaq break alone.

Leaders use 15-minute reactions at their own pre-existing four-hour zones: at least four of seven must agree and none may provide an opposite zone reaction. This is a conservative, documented interpretation of majority plus no conflicting evidence, not a threshold given in the recordings. AAPL/NVDA are not hardcoded mandatory votes. Actual VIX uses its own repeated 15-minute areas. Stop beyond the event/zone and next opposing level as target are explicit app execution choices absent from the recordings.

The analyzer uses **QQQ as an explicitly labeled Nasdaq ETF proxy**. The owner reviews this instrument choice before enabling orders. An all-green result is `SETUP_READY`; analyzer `can_enter` remains false because only the separate executor can admit an order after permission, fresh-data and broker checks. Required direct VIX access was rejected by the connected provider with HTTP 403. The actual Nasdaq-index feed tested was delayed. No proxy is promoted to actual VIX.

## What was removed from the active app

All legacy decision imports, opening-range/FVG/break/pullback/session-sweep engines, ranking scores, multi-market/perpetual/prediction-market routes, optional reference-policy overlays, inherited fixed allocation/risk settings, and automatic live startup. Old source is archived privately, not destroyed or loaded.

## Remaining gaps

1. Real-time actual VIX access. Existing Massive credentials do not have entitlement; no paid plan was purchased.
2. Confirm the execution instrument and exact zone/event/rejection definitions against annotated historical examples.
3. Validate the documented stop, exit, dollar sizing and one-position policy against representative examples. Those rules are app choices absent from V2/V3.
4. Complete representative historical replay with costs, slippage and out-of-sample checks.
5. The new broker adapter has offline lifecycle coverage. Paper-account and live-account commissioning, including real fractional protection behavior, remain unverified.

Current provider references: [Massive index aggregates](https://massive.com/docs/rest/indices/aggregates/custom-bars), [Alpaca order behavior](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [fractional trading](https://docs.alpaca.markets/us/docs/fractional-trading). Provider capability does not establish strategy profitability.
