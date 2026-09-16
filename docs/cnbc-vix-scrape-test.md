# CNBC VIX scraping feasibility test — September 16, 2026

## Result

**Successful one-time extraction of a current VIX quote and 22 consecutive 15-minute OHLC candles from CNBC's rendered chart.** Two completed candle samples matched Yahoo's actual VIX chart exactly. This establishes technical readability; it does not establish a production-ready or authorized continuous feed.

Evidence: `runtime/video-review-v2/cnbc-vix-scrape-20260916.json`.

## What was actually tested

1. Opened [CNBC actual VIX](https://www.cnbc.com/quotes/.VIX) while signed out. The visible instrument was CBOE Volatility Index, `.VIX:Exchange`, labeled RT Quote. At 19:05:35 UTC, the displayed quote was 17.50 with a 3:05 PM EDT timestamp.
2. Selected 1D range, then 15 Min interval. Read individual chart tooltips through the browser's visible accessibility text. No private endpoints, credentials, cookies, hidden chart data arrays, or internal application state were extracted.
3. Exported 22 consecutive candles labeled September 16, 09:30 through 14:45. The sample was collected from 19:10:15 through 19:10:41 UTC. Each selection was checked against its expected timestamp, and each candle satisfied positive finite prices and valid OHLC bounds. The ongoing 15:00 candle was excluded.
4. Independently selected Yahoo's 13:15 and 13:30 America/Chicago candles (14:15 and 14:30 Eastern). Both matched CNBC to 0.01 across all four prices:

| Eastern chart time | Open | High | Low | Close |
| --- | --- | --- | --- | --- |
| 14:15 | 16.70 | 16.95 | 16.67 | 16.84 |
| 14:30 | 16.82 | 17.54 | 16.56 | 16.66 |

5. CNBC also exposed the newer 14:45 candle: open 16.70, high 17.78, low 16.66, close 17.73. Yahoo's corresponding delayed candle was still partial at the earlier comparison time. Thus the live-quote-only limitation in the previous research is superseded: CNBC's rendered chart also exposes recent intraday OHLC.
6. The CNBC 5D view offered 15-minute intervals; switching to 1M offered daily/weekly intervals only. Full 14-calendar-day intraday coverage was not extracted. Yahoo's chart displayed a longer 15-minute history range, but only selected historical candles were verified, not a complete bulk series.

## Other free sources

- [Alpaca's current index-options announcement](https://alpaca.markets/blog/alpaca-launches-index-options-via-trading-api/) explicitly says index data is not currently offered. The existing account's earlier authenticated spot-VIX requests also returned no VIX data. Stock candles and VIX options are different data.
- [Yahoo VIX](https://finance.yahoo.com/chart/%5EVIX) supplies readable 15-minute actual-index candles, but Cboe indices are [15 minutes delayed](https://help.yahoo.com/kb/SLN2310.html). It is a possible older-history comparison source, not a replacement for the current completed candle.
- TradingView's free official Cboe candles were previously verified readable and delayed 15 minutes; Cboe's free downloadable history is daily. See `free-vix-research.md`.

## Reliability and strategy limits

The app uses the last two completed VIX 15-minute candles to evaluate zone reactions (`pivot/strategy.py`), and requests 14 calendar days of history (`pivot/index_data.py`). A recent quote does not complete a delayed candle's missing high/low. Any combined source needs separate history and quote timestamps, explicit timezone/session conventions, and a source watermark proving the latest history interval is complete.

The browser test encountered layout movement that left an old tooltip readable after a coordinate selection missed the chart. Those repeated samples were excluded from the final 22-candle export. A collector must validate the selected timestamp and OHLC after each interaction; text presence alone is insufficient. Minute-only quote timestamps also limit precise freshness validation.

The local audit additionally found that the existing VIX path does not check interior historical gaps or explicitly filter the accepted VIX session, although the stock path does. Any future provider adapter should address these gaps rather than copying the existing health test unchanged. No production code was altered in this research task.

## Permission for a continuous scraper

[CNBC's terms](https://www.cnbc.com/terms/) incorporate the [Versant terms](https://www.versantmedia.com/terms), which incorporate [Prohibited Actions](https://www.versantmedia.com/terms/prohibited-actions). Section 12 expressly restricts data extraction/scraping, whether automated or manual. Section 10 restricts incorporating content into another application unless authorized. [CNBC Market Data Terms](https://www.cnbc.com/market-data-terms-of-service/) allow limited internal use/copying but do not establish authorization for an ongoing scraper.

This is a specific provider restriction, not a claim that web scraping is universally illegal. A provider-authorized feed or separate authorization would be needed before recommending CNBC as the app's ongoing collector.

## Deployment status

No subscription, recurring collector, app-feed change, order, or Live money toggle action was performed. The chart remains available for reviewing the test. The successful sample is research evidence only; the app's VIX blocker has not been removed.

## Continuous-collector follow-up

After the request to implement a persistent collector, two ordinary unauthenticated HTTP GET requests to the same public quote page returned **403 Access Denied** from CNBC's Akamai server. The response contained no quote or candle data. No alternate identity, session-cookie extraction, or access-control bypass was attempted.

The earlier browser extraction therefore does not demonstrate that an unattended HTTP collector can retrieve the data. Retries and timestamp validation cannot resolve this access failure. No persistent collector was deployed, and the live VIX dependency remains unresolved.

`pivot/vix_preview.py` and its offline tests validate the previously captured local research sample only. The validator recomputes the two candle comparisons, rejects malformed or discontinuous data, and always marks the output ineligible for execution. It is not connected to the app's UI, data feeds, strategy, or broker. These files do not establish a live source.
