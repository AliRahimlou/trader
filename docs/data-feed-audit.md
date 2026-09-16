# Data collection audit · September 16, 2026

## Result and budget

The data budget remains **$0**. No subscription, new account, or data purchase was made.

Read-only checks against the existing providers confirmed current QQQ and all seven technology-stock histories, the QQQ bid/ask quote, and the broker account, positions, orders and market clock. **Actual live VIX remains unavailable with the existing credentials.** The app therefore cannot yet meet every input required by the video strategy. Owner live permission does not bypass the missing VIX gate.

| Required input | Observed result | Limitation |
| --- | --- | --- |
| Account, positions, open orders, market clock | Connected | Polling; order placement is separately checked by the execution worker |
| QQQ bid and ask | Current timestamp, valid prices | IEX quote, not the consolidated national bid/ask |
| QQQ 15-minute, hourly, four-hour and daily history | Current completed candles | IEX only |
| AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA 15-minute and four-hour history | All seven current | IEX only |
| Actual Cboe VIX snapshot and 15-minute history | Both provider endpoints returned access denied | No verified free replacement connected |

Alpaca's free IEX feed is real-time data from one exchange. It is not all-exchange SIP coverage. A separate read-only SIP quote check returned access denied. This distinction is now visible in the app. [Alpaca coverage documentation](https://docs.alpaca.markets/us/docs/market-data-faq)

At the owner's explicit request, a direct Alpaca check at 17:52 UTC requested `QQQ,VIX` through both the latest-quotes and 15-minute-history endpoints using the existing credentials. QQQ returned a current quote and 35 candles over the requested day; VIX returned neither quotes nor candles. Alpaca's current index-options documentation also explicitly excludes underlying spot-index data, including VIX. This is not a working alternative VIX source. [Alpaca spot-index limitation](https://docs.alpaca.markets/us/docs/index-options)

## Collection fixes

- Fetch the exchange calendar for the complete history window. Holidays and early-close sessions use their actual opening and closing times.
- Accept complete early-close daily candles; reject daily/hourly/four-hour aggregates with missing component intervals.
- Exclude unfinished candles. Validate symbol identities, candle prices, timestamp alignment, ordering, duplicates and pagination before accepting a response.
- Refresh the previous and current trading sessions incrementally, with an hourly full-history refresh for older corrections. Failed downloads do not replace the validated cache or masquerade as successful refreshes.
- Audit every required symbol and interval against the latest complete bucket expected from the exchange calendar, with 90 seconds for publication.
- Detect missing candles throughout the calendar history used for pivot comparisons; a current last candle does not hide an earlier gap.
- Expire status between refreshes. Entry verification is bounded by the actual VIX update timestamp as well as the analysis age, and is checked again after broker reads and before submitting a prepared entry.
- Use the same validated QQQ quote adapter for status reporting and the execution worker.
- Require the actual VIX snapshot to identify `I:VIX`, `indices`, `REAL-TIME`, and a current timestamp before accepting completed historical candles. HTTP success alone does not establish real-time entitlement. [Massive snapshot fields](https://massive.com/docs/rest/indices/snapshots/indices-snapshot)
- Show per-input status, coverage, candle counts, last-completed times, errors and refresh duration under **Data connections → Check every data input**.

The approved full-bucket convention remains unchanged: each regular session contributes complete hourly and four-hour buckets only. Short closing buckets are excluded. A four-hour candle therefore normally ends at 13:30 Eastern; the daily candle covers the entire actual session. This audit did not change the video interpretation or the entry/exit rules.

## Free-source investigation

These findings concern sources actually checked; they do not establish that no free source could ever exist.

The [expanded free VIX research](free-vix-research.md) records the later provider checks, the Tallac public API result, and the conditional TradeStation route with its $10,000 funding requirement for new API applicants. No free replacement has been verified with the accounts currently available.

| Source | Verified finding | Suitability |
| --- | --- | --- |
| Existing Alpaca IEX | Working real-time stock quotes and intraday history | Retained at $0; does not supply actual spot VIX |
| Yahoo Finance `^VIX` | Official exchange table lists Cboe indices delayed 15 minutes | Cannot satisfy current VIX confirmation. [Disclosure](https://help.yahoo.com/kb/SLN2310.html) |
| Google Finance `INDEXCBOE:VIX` | Official disclosure lists a 15-minute delay; Sheets history supports daily/weekly intervals | No current VIX plus 15-minute history API. [Disclosure](https://www.google.com/googlefinance/disclaimer/), [Sheets documentation](https://support.google.com/docs/answer/3093281?hl=en) |
| TradingView `CBOE:VIX` | Free Cboe index data is delayed 15 minutes | Not live. [Coverage](https://www.tradingview.com/data-coverage/) |
| TradingView `TVC:VIX` | Exact source/equivalence not verified; newer official MCP offers data access only on paid plans, and terms restrict automated trading use | No verified free automated-feed route. [MCP documentation](https://www.tradingview.com/mcp/docs), [terms §3](https://www.tradingview.com/policies/) |
| Cboe public pages | Delayed dashboard; free historical series is daily; automated quote-table extraction is prohibited | Wrong freshness/granularity and no supported extraction route. [Dashboard](https://www.cboe.com/delayed_quotes/_vix/quote_table), [history](https://www.cboe.com/tradable-products/vix/vix-historical-data), [extraction restriction](https://www.cboe.com/delayed_quotes/API/quote_table) |
| Tradier index quotes | Provider announcement describes ORATS model-derived pricing for VIX and several indices | Not verified actual spot VIX. [Provider announcement](https://blog.tradier.com/blog/spx-pricing?hs_amp=true) |
| Twelve Data Basic | Free stocks/ETFs, 8 credits/minute and 800/day; US coverage is a subset | Potential stock alternative, not a verified solution for actual VIX. [Plan](https://twelvedata.com/pricing), [coverage](https://support.twelvedata.com/en/articles/9935903-us-equities-market-data) |
| Finnhub Free | Free quotes/streaming limits; required historical candle access not included on the checked free plan | No verified complete replacement. [Plan](https://finnhub.io/pricing) |
| Tastytrade | REST real-time quotes require a funded account; historical candles are documented through streaming | Candidate only if an existing account has actual VIX entitlement; no free VIX entitlement or authenticated data was verified. [Requirements](https://developer.tastytrade.com/reference/market-data/getMarketDataByType/), [streaming](https://developer.tastytrade.com/docs/concepts/streaming/) |
| Investing.com | VIX page says real-time derived; no public data API | Not verified actual VIX. [Quote page](https://www.investing.com/indices/volatility-s-p-500), [API policy](https://www.investing-support.com/hc/en-us/articles/115005473825-Do-You-Offer-API-Access-at-Investing-com) |
| Barchart | Index quotes and minute history API exist, but no perpetual free actual real-time VIX entitlement was verified | Free trial is not a verified $0 ongoing feed. [API](https://www.barchart.com/ondemand/api), [trial terms](https://www.barchart.com/register/realtime/form.php?ID=MW) |
| CNBC / Stooq | No readable primary documentation establishing required VIX freshness, history and programmatic entitlement was found in the bounded search | Unresolved; not connected |

No delayed quote was relabeled live, and no VIX ETF, future, derived value or scraped chart was substituted for the actual index. Connecting a future free provider requires verifying its actual instrument, permitted use, current timestamps and complete 15-minute candles first.

## Verification

An isolated read-only service with temporary storage, no broker execution adapter and existing provider credentials completed full and incremental refreshes. All eight stock histories were current; full collection took 7.65 seconds and incremental collection took 0.87 seconds in that run. QQQ bid/ask was current. Actual VIX access was denied. Timings vary with the network and provider.

Automated tests cover calendar handling, incremental cache replacement, failed updates, malformed/partial/duplicated data, delayed/incorrect VIX provenance, pagination, stale quotes and execution checks. Tests deny external network access. No real broker order was submitted or canceled by this audit; live fills and profitability are not established by these checks.

Final validation: **155 backend tests and 3 interface tests passed**, and the frontend production build passed. The local managed backend was restarted after confirming zero positions, orders and active intents; the existing execution database was backed up and the owner's Live money On permission was preserved. The updated browser page showed all eight stock histories current and the expanded per-input detail correctly. A subsequent production incremental refresh took 0.50 seconds, with zero calendar gaps in required stock intervals and a current QQQ quote. VIX remained blocked (access denial was confirmed earlier; one later refresh also encountered a connection failure), and the entry data deadline remained absent. The audit did not place or cancel orders.
