# Free actual VIX research — September 16, 2026

## Conclusion

**Updated after authenticated testing:** the owner's new InsightSentry Free account successfully returned actual Cboe spot VIX, current quotes and 1,000 fifteen-minute OHLC candles. Two request sequences, a candle-close transition and a 14-calendar-day session-coverage check passed. See [the verification report](insightsentry-vix-verification.md). A continuous app adapter is still needed; the free monthly quota cannot support the existing polling loop unchanged.

The existing Alpaca account supplies the app's equity data, but not spot VIX. The existing Massive key was denied access to both required VIX endpoints. A free brokerage account, a free chart, or an API supporting indices generally does not by itself establish VIX access for this app.

The user created the InsightSentry account. Its data key was saved locally and standalone diagnostics were added. Running trading code, market-data requirements, Live money permission and broker orders were not changed. No subscription upgrade or purchase was made.

## Accounts already available

| Provider | Evidence | Result |
| --- | --- | --- |
| Alpaca | Earlier authenticated, read-only requests for `QQQ,VIX` returned a QQQ quote and 35 QQQ 15-minute candles, with no VIX quote or candles. Sanitized evidence: `runtime/video-review-v2/alpaca-vix-check.json`. [Official documentation](https://docs.alpaca.markets/us/docs/index-options) explicitly excludes underlying spot indices. | Cannot supply actual VIX through the checked stock-data endpoints. VIX options do not replace spot VIX. |
| Massive | Earlier authenticated snapshot and intraday-history requests for `I:VIX` returned `NOT_AUTHORIZED`. [Index plans](https://massive.com/indices) distinguish end-of-day, delayed, and real-time access. | Current key is not entitled; no paid upgrade made. |
| InsightSentry Free | Authenticated current quotes and 15-minute candles passed the [recorded checks](insightsentry-vix-verification.md), including zero reported delay and complete expected sessions for the checked history. | Working free-account access verified in samples; continuous adapter and quota controls remain unfinished. |

## Additional public sources checked

| Source | Provider evidence / direct observation | Decision |
| --- | --- | --- |
| MarketData.app | Its [free-account documentation](https://www.marketdata.app/docs/account/free-accounts/), updated September 3, 2026, limits free and trial accounts to data at least 24 hours old. | Does not provide the required current data for free. |
| Alpha Vantage | The [VIX endpoint](https://www.alphavantage.co/documentation/#vix) is premium and offers daily, weekly, or monthly intervals. | Wrong price and granularity. |
| dxFeed public demo | [Official demo documentation](https://dxfeed.com/demo/) describes delayed demo data and limited historical replay. No free production real-time VIX entitlement was established. | A working demo symbol alone would not prove a usable production feed. |
| Twelve Data | Its [indices page](https://twelvedata.com/indices) says indices are coming soon. No current actual VIX listing and free entitlement were established. | Unverified for VIX; generic real-time stock marketing is insufficient. |
| Nasdaq Data Link | [Real-time/delayed API documentation](https://docs.data.nasdaq.com/docs/api-for-real-time-or-delayed-data-1) requires onboarding and credentials. No free actual Cboe VIX product was verified. | No qualifying free route established. |
| Tallac Options | [Pricing](https://www.tallacoptions.com/Pricing) advertises free historical VIX samples, with real-time access separately priced. A documented public GET to [VIX instrument metadata](https://www.tallacoptions.com/api/instruments/VIX) reported intraday coverage ending **2022-05-17**, while daily coverage reached **2026-09-15**. [API documentation](https://www.tallacoptions.com/apis). | Does not supply the recent intraday history needed. Minute `Last` samples also do not establish complete 15-minute high/low candles. |

Previously checked delayed or derived sources remain unsuitable: Yahoo `^VIX` and Google Cboe quotes disclose 15-minute delay; free TradingView Cboe data is delayed; Investing.com labels its quote derived; Tradier announced model-derived index prices; Cboe's free history is daily. See [the data collection audit](data-feed-audit.md) for their primary sources.

## CNBC quote-page follow-up

A read-only browser inspection of [CNBC's actual VIX page](https://www.cnbc.com/quotes/.VIX) on September 16, 2026 showed `CBOE Volatility Index .VIX:Exchange`, labeled **RT Quote | Exchange | USD**, while signed out. The displayed quote advanced from 17.41 at 2:53 PM EDT to 17.47 at 2:54 PM EDT. The independently checked clock was 18:54:12 UTC (2:54:12 PM EDT) between these observations. Daily open/high/low and previous close were also visible. This is positive evidence of a current, freely viewable spot VIX quote; the generic footer about delayed data should not be used to label this specific RT-marked symbol delayed.

The rendered chart exposed chart styles and time-range controls, but complete recent 15-minute OHLC extraction and unattended collection were not validated. A current quote plus daily high/low is insufficient to reconstruct each historical 15-minute candle's high/low. Targeted official-documentation searches did not establish a supported public CNBC API supplying actual live VIX and that history. This is an unverified interface, not a claim that no interface exists.

CNBC's [Data Stream help](https://cnbc.zendesk.com/hc/en-us/articles/26346065200539-What-is-the-Data-Stream) describes a Pro live-TV companion feature, not an API entitlement. A CNBC Pro purchase should not be recommended as completing this app's data requirements on that evidence. No collector, subscription or feed substitution was installed.

**Later same-day update:** At the owner's request, the browser test successfully extracted **22 consecutive 15-minute CNBC VIX candles** through 14:45 Eastern; two older samples matched Yahoo OHLC exactly. CNBC therefore exposes recent candles as well as its latest quote. Full 14-day history and uninterrupted collection remain unverified, and CNBC's incorporated Versant terms explicitly restrict scraping. See [the detailed scrape test](cnbc-vix-scrape-test.md) and `runtime/video-review-v2/cnbc-vix-scrape-20260916.json`. No production adapter was connected.

## Brokerage routes: what is established and what is missing

### Webull — confirmed free platform quotes, API access unresolved

- **Established:** Webull [advertises free real-time CGIF data after registration](https://www.webull.com/trading-investing/index-options). Cboe [identifies VIX as part of CGIF](https://www.cboe.com/data/global-indices-feed). Webull's [claim instructions](https://www.webull.com/help/faq/10517-How-do-I-claim-the-CGIF-Data) say to use Menu → Market Quotes → CGIF Index → Claim; otherwise its index quotes are delayed ten minutes.
- **API limitation:** The [OpenAPI overview](https://developer.webull.com/apis/docs/market-data-api/overview/) explicitly separates app/desktop data subscriptions from OpenAPI permissions. Its listed products do not include spot indices. The [Data API](https://developer.webull.com/apis/docs/market-data-api/data-api/) documents `US_STOCK` and `US_ETF` categories, with dedicated futures and crypto paths, but no spot-index category.
- **Status:** A documented free route for viewing index data in Webull. Free programmatic VIX and recent 15-minute VIX history for this app are not verified. No Webull credentials or authenticated results were available. Do not infer that claiming free platform quotes enables the bot.

#### Logged-in HTML collection proposal

An ordinary browser inspection on September 16, 2026 confirmed that the visible Webull web interface exposes actual `VIX CBOE — CBOE Market Volatility Index`, its displayed value, and a quote timestamp as readable page text. At the observed page clock of 14:25:22 EDT, the quote showed 16.69 with a 14:15 EDT timestamp: approximately ten minutes delayed. The browser was signed out. No authenticated real-time update or complete historical 15-minute OHLC series was validated. This inspection did not use private endpoints, session-token extraction, or hidden application state.

Reading a rendered latest value is technically feasible, but repeated latest-value samples do not establish complete historical candle highs/lows or reliable unattended collection through logouts and page changes. A browser collector therefore remains an unvalidated prototype idea, not a production feed.

The [U.S.-linked Data Disclaimer](https://www.webull.com/policy?code=DATA_DISCLAIMER), dated September 9, 2024, requires prior written permission for reproducing, storing, modifying, transmitting, or distributing application content. The [U.S.-linked Terms of Service](https://www.webull.com/policy?id=2NHCVCO676J8F1ORFTCSPV2AN8_2.0), sections 3.1(b) and 3.5, additionally require exchange/provider terms. This does not establish that scraping is universally illegal; it means login and free display access alone do not establish permission to build the proposed stored automation feed. No provider permission was requested or represented as obtained.

### TradeStation — strongest documented conditional route

- **Established:** The [official symbol list](https://clientcenter.tradestation.com/fees/market_data_services.shtm) identifies actual `$VIX.X` as Volatility S&P 500 in its CBOE Indices package. The current [production market-data pricing page](https://www.tradestation.com/pricing/market-data-pricing/#indices), inspected in the browser, includes CBOE Indices Real-Time Data under included data for both professional and nonprofessional views.
- **API capabilities:** The [official API specification](https://api.tradestation.com/docs/specification/#tag/MarketData/operation/GetBars) supports minute bars with an interval of 15, historical and streaming bars, and a quote-stream `IsDelayed` field.
- **Critical condition:** The [individual API page](https://www.tradestation.com/platforms-and-tools/trading-api/) advertises no API subscription cost but requires **$10,000 funding for new API applicants** before key issuance. Existing customers must request API access. A deposit is capital rather than a subscription fee, but this is not a new unfunded $0 setup.
- **Status:** Credible no-additional-data-fee route for an already eligible funded customer with approved API access. Actual authenticated VIX quotes and history were not tested, and permission for data-only use to drive another broker still needs confirmation. No TradeStation account has been established as available in this task.

### Schwab

The [standard brokerage pricing](https://www.schwab.com/pricing) establishes $0 opening/maintenance fees and no account minimum. It does not establish VIX market-data entitlement. Publicly readable [individual API product documentation](https://developer.schwab.com/products/trader-api--individual) did not resolve whether actual VIX and its intraday history are included at $0. No authenticated Schwab check was available. Do not conflate free account opening with verified free VIX API access.

### Questrade

- **Established:** The [official Plus FAQ](https://forward.questrade.com/learning/questrade-basics/questrade-plus/questrade-plus-faq) includes CBOE Global Indices in the free package, explicitly streamed to Edge platforms. Brokerage customers can use its API. [Historical candle documentation](https://www.questrade.com/api/documentation/rest-operations/market-calls) and [interval enumeration](https://www.questrade.com/api/documentation/rest-operations/enumerations/enumerations) support 15-minute requests.
- **Missing:** Confirmation that the free package grants continuous, current **VIX API** access and the required historical coverage. The [API quote documentation](https://www.questrade.com/api/documentation/rest-operations/market-calls/markets-quotes-id) warns that accounts without the needed real-time package consume limited snapshot quotes and can then receive delayed quotes. It says to inspect `delay`; it does not settle whether the free Cboe package qualifies.
- **Eligibility:** Questrade says U.S. residents generally cannot open accounts, with limited exceptions. [Account requirements](https://corporatefx.questrade.com/learning/questrade-basics/how-to-open-an-account/photo-id).
- **Status:** Existing eligible account could be checked read-only. Not confirmed as a free feed for this app.

### tastytrade

- **Established:** [Official market-data documentation](https://developer.tastytrade.com/docs/concepts/market-data/) provides real-time REST quotes, including an index category, to **funded** account holders. [Streaming documentation](https://developer.tastytrade.com/docs/concepts/streaming/) supports historical candles through DXLink. [Pricing](https://tastytrade.com/pricing) advertises no subscriptions for its brokerage pricing.
- **Missing:** An authenticated check establishing current actual VIX, recent VIX candles, and account-specific fees/entitlement. Generic index support does not prove every index is included.
- **Use restriction needing confirmation:** The [API terms](https://assets.tastyworks.com/production/documents/open_api_terms_and_conditions.pdf), section 11 definitions, tie the permitted purpose to facilitating transactions with tastytrade. Using it solely to drive Alpaca trades is therefore not established as permitted by these public terms. Provider confirmation would resolve that question.
- **Status:** Candidate with additional conditions, not a verified free VIX source for the Alpaca app. No funding or account action performed.

## TradingView follow-up and correction

The older [API help article](https://www.tradingview.com/support/solutions/43000474413-i-need-access-to-your-api-in-order-to-get-data-or-indicator-values/) says no data API is available. Newer [official MCP documentation](https://www.tradingview.com/mcp/docs) does expose OHLCV history, including 15-minute bars. Therefore the blanket statement that TradingView has no data-access interface is incomplete. MCP requires Essential or higher and excludes trial plans; it is not a $0 route.

The [coverage table](https://www.tradingview.com/data-coverage/) lists free Cboe Global Indices data as 15-minute delayed and real-time access as paid. [Terms section 3](https://www.tradingview.com/policies/) restricts market data, including alerts and webhooks, to display use and explicitly excludes automated trading and algorithmic decision-making. [Webhook guidance](https://www.tradingview.com/support/solutions/43000722015-using-credentials-for-webhooks/) also says alerts are not designed for automated trading. The MCP and alerts do not establish a free, permitted actual real-time VIX feed for this app.

### Direct browser extraction check

At the owner's request, a read-only browser test followed the Volatility S&P 500 link from `https://www.tradingview.com/markets/` to the CBOE VIX page and opened its full chart. The chart identified the actual displayed series as **`CBOE_DLY:VIX`** and explicitly displayed **Market open · Data is delayed**. The 15-minute interval and Data window were accessible without logging in.

A one-time read of the rendered Data window succeeded: September 16, 2026, bar time 18:15 UTC, open 16.70, high 16.71, low 16.69, close 16.71. The page clock was about 18:32 UTC. The displayed bar was still updating in the delayed stream; its interval start time is not an exact quote publication timestamp and this observation does not establish a completed candle. Full historical extraction, coverage and reliable unattended collection were not tested.

Result: **browser-rendered fields are technically readable, but the inspected free stream is delayed**. Scraping does not remove the source delay or grant automated-trading rights. No background collector or runner integration was installed.

TradingView's [official TVC explanation](https://www.tradingview.com/support/solutions/43000709178-what-does-tvc-stand-for-in-the-instrument-ticker/) adds that TVC denotes TradingView-calculated data from multiple sources, rather than an exchange. Such symbols can differ from exchange counterparts. The exact inputs and equivalence of `TVC:VIX` to official Cboe VIX remain unverified, so a freely updating TVC chart would not by itself qualify.

### Proposed continuous-futures and CFD alternatives

The owner's suggested `CBOE:VX1!`, `CAPITALCOM:VIX`, and Pepperstone alternatives were checked against provider documentation on September 16, 2026:

- **`CBOE:VX1!`:** TradingView's [coverage table](https://www.tradingview.com/data-coverage/) lists Cboe Futures Exchange data as **10 minutes delayed for free**, with real-time access separately priced. The claim of universally free zero-delay access is contradicted by that table. [Cboe](https://www.cboe.com/tradable-products/vix/vix-futures) explains that futures represent expectations for VIX at future expirations; they are a different instrument from current spot VIX.
- **`CAPITALCOM:VIX`:** Capital.com's [instrument specification](https://capital.com/en-int/markets/indices/volatility-index-index) states that the price uses the front two VIX futures months, with a daily roll adjustment at 20:59:55 UTC. It is a broker CFD based on futures, not an official spot-index mirror.
- **Pepperstone VIX CFD:** Its [methodology guide](https://pepperstone.com/en-gb/insights/guides/risk-management/trading-volatility-and-understanding-the-vix-index) describes a calendar-weighted blend of front- and second-month VIX futures. Its [instrument description](https://pepperstone.com/en-af/trading/instruments/vix/) likewise identifies futures-based construction.

TradingView's coverage table lists Capital.com and Pepperstone provider rows with $0 real-time fees, but these rows are not VIX-specific. A freely updating CFD chart still does not establish actual spot VIX identity, authenticated feed freshness, complete candle coverage, or permitted automated use.

**Strategy implication (inference from the documented instrument differences):** futures weighting and roll adjustments can change price levels, candles, and pivot reactions. Substitution would require explicitly defining and validating a separate strategy variation. No proxy was connected, no missing-data check was removed, and no trading setting was changed.

## Additional provider table checked — September 16, 2026

The owner's suggested FMP, Finnhub, Twelve Data, InsightSentry and VIXCentral/Parse routes were checked against current provider pages. **Follow-up: InsightSentry Free successfully returned current actual VIX quotes and 15-minute candles using the owner's new account.** See [the authenticated verification report](insightsentry-vix-verification.md). A continuous adapter is not installed yet.

| Provider | Verified result for this app |
| --- | --- |
| Financial Modeling Prep | [Basic pricing](https://site.financialmodelingprep.com/pricing-plans) confirms 250 calls/day but end-of-day data. Intraday charts are paid. Exact current VIX entitlement remains unverified. |
| Finnhub | [Pricing](https://finnhub.io/pricing) confirms 60 calls/minute free. [Stock Candles](https://finnhub.io/docs/api/stock-candles) requires Premium, including resolution 15. Its US-stock quote promise does not establish spot VIX coverage. |
| Twelve Data | [Basic pricing](https://twelvedata.com/pricing) confirms 800/day and 8/minute. The [indices page](https://twelvedata.com/indices) still says Coming soon. Free actual VIX access was not established. |
| InsightSentry | Public [symbol search](https://insightsentry.com/search) confirms CBOE:VIX as CBOE Volatility Index, INDEX, US. [Coverage](https://insightsentry.com/coverage) labels Cboe Global Indices Feed real-time, with a general caveat about activation and possible additional fees. |
| VIXCentral via Parse | The [wrapper's documented endpoints](https://parse.bot/marketplace/13ec11b7-b3ef-49b8-a3d4-7bbd51009074/vixcentral-com-api) return curves and daily/latest values, not intraday OHLC. Its timestamp records retrieval time rather than the market observation. The advertised 200 credits do not resolve these gaps. |

### InsightSentry free-account test completed

Its [plan documentation](https://insightsentry.com/llms.txt) includes current REST quotes and recent 30,000-point OHLCV series, with 1,000 requests/month and 5/minute. Free WebSocket access is not included; streaming starts on the paid Pro plan. The owner's key is now configured locally. Two read-only test sequences returned HTTP 200, zero reported delay and fresh source timestamps; completed-candle and calendar checks passed. See the linked report for observations and limits.

The [OpenAPI specification](https://insightsentry.com/openapi.json) documents this read-only request with the dashboard token in the Authorization Bearer header:

`GET https://api.insightsentry.com/v3/symbols/CBOE%3AVIX/series?bar_type=minute&bar_interval=15&dp=500`

The tests checked the returned instrument, interval, market timestamps, complete OHLC, latest completed candle, history gaps and explicit delay flags. Response-generation timestamps were not substituted for price timestamps. The user created the account; the agent purchased no subscription, connected no trading-provider adapter, and changed no live permission.

Quota planning only: one candle-history request after each regular-session 15-minute close is 26 requests/day, or 572 for 22 trading days. That could fit the free allowance before retries, metadata, and separate quote requests. This is not a tested polling design; the existing minute-by-minute analysis loop would need provider-specific caching and freshness handling before integration.

## What would finish verification

For an eligible existing broker account or a provider-confirmed free plan:

1. Confirm actual Cboe spot VIX identity and included access; exclude futures, ETFs, and model-derived substitutes.
2. Confirm the plan's $0 recurring cost and the intended private application's use.
3. Make read-only requests during VIX publication hours and inspect source timestamps and delay indicators over repeated updates.
4. Fetch the recent completed 15-minute OHLC history needed by the strategy; validate session coverage, missing intervals, timestamp alignment, and current last completed candle.
5. Only after those checks pass, implement and test a provider adapter. Account signup, a chart screenshot, or an HTTP 200 response alone is not completion.

InsightSentry's account-access and sampled-data checks have now passed. The outstanding implementation is a provider adapter with durable caching, monthly request accounting and preserved freshness checks; the app's VIX requirement must not be removed.
