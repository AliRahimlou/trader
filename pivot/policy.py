"""Execution choices absent from the clips, displayed before the owner enables orders."""
POLICY_VERSION = 'nasdaq-qqq-execution-v4-video-five-minute'
POLICY = {
    'version': POLICY_VERSION,
    'instrument': 'QQQ',
    'summary': [
        'Trade QQQ, an ETF tracking the Nasdaq-100; it is not Nasdaq futures.',
        'Evaluate both primary entry methods separately: a four-hour area with an hourly break/retest, or a sweep of a previous-day extreme. QQQ uses completed regular-session hourly candles.',
        'The recordings do not define the colored areas numerically. App interpretation: fixed 0.1% bands around historical price anchors, confirmed by two nonadjacent touches or crossings; four-hour Nasdaq areas and five-minute leader areas.',
        'Use genuine five-minute leader candles. At least four of seven leaders must agree, with none opposing. A rejection may remain valid for up to 15 minutes in the same session; missing candles or an opposite boundary close invalidate it.',
        'An opportunity expires 180 minutes after its original break or sweep, or at the session change. Only one entry is allowed for that opportunity, even if a later retest or leader direction changes. These timing rules are app choices, not numerical rules supplied by the creator.',
        'Actual VIX must react in the opposite direction using completed 15-minute candles and pre-existing areas. Futures, CFDs and volatility ETFs are not substitutes.',
        'Actual VIX candles come from InsightSentry Free, refreshed after completed 15-minute periods. A fresh actual-index quote is required before entry; cached quotes are never treated as fresh.',
        'The free data allowance has a durable request limit. Missing candles, delayed data, an exhausted allowance or an unsuccessful quote check blocks new entries. A quote can be attempted once per candle period.',
        'Buy the saved dollar amount, with the planned purchase within 1% below the target. Shorts require whole shares within 1% of that amount, account permission and borrow availability. Small fractional targets cannot open shorts.',
        'Stop beyond the event/zone; take profit at the next opposing premarked 4-hour or previous-day level. These exit rules are engineering choices, not specified in the videos. One position at a time. Start closing five minutes before market close.',
        'Broker stop orders are placed after entry fills. Targets and end-of-day exits require the host machine to remain running and connected. Fractional stops expire each day.',
        'Rejected orders pause new entries. Unconfirmed stop protection has a persistent 30-second acceptance deadline; nonworking protection pauses entries and starts cancellation-safe recovery. Unknown cancellation or broker state requires owner review and cannot authorize a competing sale.',
        'Off stops new entries and cancels unfinished entries; existing positions continue their exits. Fills, slippage and profits are not guaranteed.',
    ],
}
