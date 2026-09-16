# Paid live VIX comparison — September 16, 2026

## Recommendation

**Best technical fit for the existing app: Massive Indices Advanced, advertised at $99/month billed monthly for eligible individual/nonprofessional use.** The app already implements its actual `I:VIX` snapshot and 15-minute historical OHLC endpoints in `pivot/index_data.py`. This is a conditional purchasing recommendation: confirm that the subscription covers this private app's automated analysis and trading signals for the owner's Alpaca account before paying. The published retail fee alone does not establish those rights.

**Lowest published recurring broker fee: Interactive Brokers Pro with Cboe Streaming Market Indexes, $3.50/month, requiring at least $500 account equity plus subscription fees.** This needs a new adapter and account/session management, exact VIX entitlement confirmation, and confirmation of data-only use with Alpaca. It has not been authenticated or tested for this owner.

No account was opened, subscription purchased, deposit made, provider contacted, trading setting changed, or order submitted during this research.

## Comparison

| Provider | Published cost and requirements | Assessment for this app |
| --- | --- | --- |
| Massive Indices Advanced | $99/month billed monthly; all index tickers, real-time snapshots, minute aggregates, WebSockets, and at least one year of history. Individual/nonprofessional pricing. | Closest fit, existing API-key adapter. Exact automated-use rights still require confirmation. |
| Interactive Brokers Pro / Cboe Streaming Market Indexes | $3.50/month; $500 equity plus fees to activate/maintain data; API access acknowledgment. | Lowest recurring fee among the shortlisted broker packages. Exact VIX package coverage, cross-broker use, and current 15-minute history need account-level validation. |
| TradeStation API | Included Cboe index data and no API subscription cost; new API applications require $10,000 funding. | Actual `$VIX.X` documented. Useful for an existing eligible funded account; an excessive new funding requirement solely for VIX data. |
| DTN IQFeed | $108.15/month Core + $9.29/month Cboe Indexes = $117.44/month; $50 startup. Custom developer registration separately lists $597/year. | Streaming and intraday history exist, but added cost and Windows client requirements make it less suitable here. Mac/Linux require a compatibility layer. |
| dxFeed | Actual VIX Spot from Cboe documented; API/history/intended-use package requires a quote. | Strong technical fallback for a tailored license, without a verified public all-in cost. |

Prices are public listings checked on the research date, not account-specific quotes. Taxes, professional classification, or additional usage rights can change the total. Funding requirements are retained capital, not subscription fees.

## Massive evidence and remaining condition

- [Indices pricing](https://massive.com/indices): Advanced is $99 monthly and real-time. Starter is $49 monthly and **15 minutes delayed**, so Starter does not solve this requirement. Stocks and Options are separate products and do not substitute for the Indices plan.
- [Snapshot documentation](https://massive.com/docs/rest/indices/snapshots/indices-snapshot): explicitly uses `I:VIX` in an example, exposes source timestamp and `REAL-TIME`/`DELAYED` label, and assigns real-time access to Advanced.
- [Custom index OHLC](https://massive.com/docs/rest/indices/aggregates/custom-bars): supports custom minute intervals, including a multiplier of 15. These are index-value aggregates, not futures or ETF candles.
- [Indices overview](https://massive.com/docs/rest/indices/overview) describes integration into trading algorithms, but the [Market Data Terms](https://massive.com/legal/market-data-terms-of-service), sections 2 and 5(d), default to display use and restrict non-display/strategy use unless licensed or expressly permitted. Marketing and API availability do not settle this account-specific condition.
- Local source inspection confirms `load_vix` uses these two endpoints. Earlier authenticated checks of the existing key returned access denied. No paid entitlement or successful live session was claimed during this research.

Before purchase, obtain confirmation covering: actual real-time `I:VIX`, recent 15-minute OHLC, a private single-user app generating automated signals for the owner's Alpaca account, no redistribution, and the total recurring fee for that use. Prefer monthly billing until access and data quality are validated.

## Broker evidence and operating tradeoffs

- IBKR [pricing and equity requirements](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php), [API requirements](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-requirements), and [package descriptions](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/popular-market-data-subscriptions/introduction). The package covers Cboe indices; exact VIX coverage should be checked with its Market Data Assistant before treating it as guaranteed.
- IBKR [historical OHLC API](https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-market-data/get-md-history) supports the relevant kind of data. No authenticated exact-symbol history result is available here.
- For individual Client Portal Gateway use, [daily manual reauthentication](https://www.interactivebrokers.com/docs/web-api/authentication/cpgw/client-portal-gateway-faq) is required. The TWS/IB Gateway path supports daily restart but still has [weekly manual authentication](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm). This matters for the owner's unattended-hosting objective.
- IBKR's [TWS API license description](https://interactivebrokers.github.io/) focuses on managing the user's IB accounts. Permission for a data-only connection powering Alpaca is not established by that description.
- TradeStation [API onboarding](https://www.tradestation.com/platforms-and-tools/trading-api/), [actual VIX symbol](https://clientcenter.tradestation.com/fees/market_data_services.shtm), [market-data pricing](https://www.tradestation.com/pricing/market-data-pricing/), and [bar API](https://api.tradestation.com/docs/specification/#tag/MarketData/operation/GetBars). Its live pricing page was previously inspected in the browser; current text fetch remained unavailable.

## Other vendors checked

- IQFeed [fees](https://www.iqfeed.net/index.cfm?displayaction=data&section=fees), [API capabilities](https://www.iqfeed.net/API/index.cfm?displayaction=developer&section=main), [developer pricing](https://www.iqfeed.net/dev/index.cfm?section=register), and [developer agreement](https://www.iqfeed.net/dev/newagreement.cfm). The custom-developer agreement includes software for self-use, so the annual developer fee cannot be silently omitted from this app's comparison.
- dxFeed [US coverage](https://dxfeed.com/coverage/us/) identifies VIX Spot from Cboe; [REST documentation](https://kb.dxfeed.com/en/market-data-api/data-access-solutions/rest.html) documents data access. A custom quote must settle live/history coverage and intended usage.
- Barchart [API capabilities](https://www.barchart.com/ondemand/api) include index quotes and minute history; no verified VIX-specific total was found.
- MarketData.app's current [coverage page](https://www.marketdata.app/data/) labels indices Coming Soon. Its old VIX announcement is insufficient evidence for a current purchasable feed.
- TradingView's chart subscriptions remain unsuitable as the purchasing recommendation for this unattended feed; see `free-vix-research.md` for the API, delayed feed, and automated-use findings.

## Acceptance after an approved subscription

Confirm source identity, explicit live entitlement, fresh timestamps during index publication hours, complete recent completed 15-minute candles, and recovery after data interruptions. Buying a plan is not itself proof that the current app has received and accepted correct data.
