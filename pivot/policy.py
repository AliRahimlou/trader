"""Execution choices absent from the clips, displayed before the owner enables orders."""
POLICY_VERSION = 'nasdaq-qqq-execution-v2-insightsentry'
POLICY = {
    'version': POLICY_VERSION,
    'instrument': 'QQQ',
    'summary': [
        'Trade QQQ, an ETF tracking the Nasdaq-100; it is not Nasdaq futures.',
        'Use the two primary videos: premarked levels, a completed hourly event, technology-stock reactions and actual VIX confirmation.',
        'Mechanical interpretation: two repeated touches with a 0.1% zone band; at least four of seven leaders agree and none oppose; 15-minute leader/VIX reactions.',
        'Actual VIX candles come from InsightSentry Free, refreshed after completed 15-minute periods. A fresh actual-index quote is required before entry; cached quotes are never treated as fresh.',
        'The free data allowance has a durable request limit. Missing candles, delayed data, an exhausted allowance or an unsuccessful quote check blocks new entries. A quote can be attempted once per candle period.',
        'Buy the saved dollar amount. Shorts require whole shares within 1% of that amount, account permission and borrow availability.',
        'Stop beyond the event/zone; take profit at the next opposing level. One position at a time. Start closing five minutes before market close.',
        'Broker stop orders are placed after entry fills. Targets and end-of-day exits require the host machine to remain running and connected. Fractional stops expire each day.',
        'Off stops new entries and cancels unfinished entries; existing positions continue their exits. Fills, slippage and profits are not guaranteed.',
    ],
}
